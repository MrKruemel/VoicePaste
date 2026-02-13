"""Voice-to-Summary Paste Tool -- Main Entry Point.

A Windows desktop application that lets users press a hotkey (default: Ctrl+Shift+V),
speak into their microphone, and have a clean German-language summary
automatically pasted at their cursor position.

v0.2 Core Experience: Summarization, tray states, audio cues, clipboard
preservation, error handling with toast notifications, Escape cancel.

v0.2.1: Startup UX improvements -- startup balloon notification, fatal
error message boxes for --noconsole builds, --debug CLI flag.

Architecture:
    Main thread:   pystray event loop (system tray)
    Thread 1:      keyboard hotkey listener (daemon)
    Thread 2:      Recording + STT + Summarization + Paste pipeline (spawned per session)
"""

import ctypes
import ctypes.wintypes
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from typing import Optional

# Ensure src directory is on the path for imports
if getattr(sys, "frozen", False):
    _base_dir = os.path.dirname(sys.executable)
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))
    _parent_dir = os.path.dirname(_base_dir)
    if _base_dir not in sys.path:
        sys.path.insert(0, _base_dir)

from constants import (
    APP_NAME,
    APP_VERSION,
    AppState,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
)
from config import AppConfig, load_config
from audio import AudioRecorder
from stt import CloudWhisperSTT, STTError
from summarizer import CloudLLMSummarizer, PassthroughSummarizer, SummarizerError
from paste import clipboard_backup, clipboard_restore, paste_text
from hotkey import HotkeyManager
from tray import TrayManager
from notifications import (
    play_cancel_cue,
    play_error_cue,
    play_recording_start_cue,
    play_recording_stop_cue,
)

logger = logging.getLogger(APP_NAME)

# Windows MessageBox constants
_MB_OK = 0x00000000
_MB_ICONERROR = 0x00000010
_MB_ICONWARNING = 0x00000030
_MB_TOPMOST = 0x00040000


def _show_fatal_error(message: str, title: str = APP_NAME) -> None:
    """Show a fatal error message box using the Windows API.

    This is used for errors that occur before the tray icon is available,
    or when the application must exit immediately. Essential for
    --noconsole builds where there is no console to display errors.

    The message box is topmost so it appears above other windows.

    Args:
        message: Error message body.
        title: Message box title (defaults to APP_NAME).
    """
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            title,
            _MB_OK | _MB_ICONERROR | _MB_TOPMOST,
        )
    except Exception:
        # Last resort: if even MessageBox fails, there is nothing we can do.
        pass


def _enable_debug_console() -> None:
    """Allocate a console window for a windowed (--noconsole) application.

    When VoicePaste.exe is built in release mode (no console), passing
    --debug on the command line will call this function to attach a
    console window so that log output is visible in real time.

    Uses kernel32.AllocConsole() which creates a new console window.
    After allocation, sys.stdout and sys.stderr are redirected to the
    new console so that print() and logging StreamHandler work.
    """
    try:
        ctypes.windll.kernel32.AllocConsole()
        # Reopen stdout/stderr to point to the new console
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    except Exception:
        # If AllocConsole fails (e.g., console already attached), ignore.
        pass

# REQ-S27: Single-instance mutex via Windows kernel32
_MUTEX_NAME = "Global\\VoicePasteToolMutex"
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_mutex() -> Optional[ctypes.wintypes.HANDLE]:
    """Attempt to acquire a Windows named mutex for single-instance enforcement.

    REQ-S27: Prevents multiple instances of the application from running
    simultaneously. Uses kernel32 CreateMutexW to create a system-wide
    named mutex.

    Returns:
        The mutex handle if successfully acquired, None if another instance
        is already running or if mutex creation fails.
    """
    kernel32 = ctypes.windll.kernel32

    handle = kernel32.CreateMutexW(
        None,   # default security attributes
        True,   # initial owner
        _MUTEX_NAME,
    )

    if handle == 0 or handle is None:
        logger.error(
            "Failed to create mutex '%s'. GetLastError=%d",
            _MUTEX_NAME,
            kernel32.GetLastError(),
        )
        return None

    last_error = kernel32.GetLastError()
    if last_error == _ERROR_ALREADY_EXISTS:
        logger.error(
            "Another instance of %s is already running (mutex '%s' exists).",
            APP_NAME,
            _MUTEX_NAME,
        )
        # Close our duplicate handle since we are not the owner
        kernel32.CloseHandle(handle)
        return None

    logger.info("Single-instance mutex acquired: '%s'.", _MUTEX_NAME)
    return handle


def _release_single_instance_mutex(handle: ctypes.wintypes.HANDLE) -> None:
    """Release and close the single-instance mutex.

    Args:
        handle: The mutex handle returned by _acquire_single_instance_mutex.
    """
    kernel32 = ctypes.windll.kernel32

    try:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
        logger.info("Single-instance mutex released.")
    except Exception:
        logger.exception("Error releasing single-instance mutex.")


def setup_logging(config: AppConfig) -> None:
    """Configure logging to file and console.

    REQ-S01: API key is never logged (handled by config.masked_api_key).
    REQ-S11: Audio data is never logged.
    REQ-S25: Only safe data is logged.

    Args:
        config: Application configuration with log level and paths.
    """
    log_level = getattr(logging, config.log_level, logging.INFO)

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear any existing handlers
    root_logger.handlers.clear()

    # File handler with rotation (REQ-S26: 5 MB max, 3 backup files)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            str(config.log_path),
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        root_logger.addHandler(file_handler)
    except OSError as e:
        # Fall back to console only if file logging fails
        print(f"Warning: Could not create log file: {e}", file=sys.stderr)

    # Console handler (for development/debugging)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(console_handler)


class VoicePasteApp:
    """Main application class orchestrating all components.

    Manages the state machine and coordinates hotkey, recording,
    transcription, summarization, and pasting.

    v0.2 features:
    - CloudLLMSummarizer for text cleanup
    - Dynamic tray icon states
    - Audio cues on state transitions
    - Escape to cancel recording
    - Clipboard backup/restore
    - Toast notifications for errors

    Attributes:
        config: Application configuration.
        state: Current application state.
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize the application with all components.

        Args:
            config: Validated application configuration.
        """
        self.config = config
        self._state = AppState.IDLE
        self._state_lock = threading.Lock()

        # Initialize components
        self._recorder = AudioRecorder(on_auto_stop=self._on_auto_stop)
        self._stt = CloudWhisperSTT(api_key=config.openai_api_key)

        # v0.2: Use CloudLLMSummarizer when enabled, else passthrough
        if config.summarization_enabled:
            self._summarizer: PassthroughSummarizer | CloudLLMSummarizer = (
                CloudLLMSummarizer(api_key=config.openai_api_key)
            )
            logger.info("Summarization enabled (CloudLLMSummarizer).")
        else:
            self._summarizer = PassthroughSummarizer()
            logger.info("Summarization disabled (PassthroughSummarizer).")

        self._hotkey_manager = HotkeyManager(hotkey=config.hotkey)
        self._tray_manager = TrayManager(
            on_quit=self._shutdown,
            hotkey_label=config.hotkey,
        )

        self._shutdown_event = threading.Event()
        self._pipeline_thread: threading.Thread | None = None

    @property
    def state(self) -> AppState:
        """Current application state (thread-safe read)."""
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: AppState) -> None:
        """Set the application state (thread-safe).

        Updates the tray icon to reflect the new state.
        All state transitions are logged.

        Args:
            new_state: The new application state.
        """
        with self._state_lock:
            old_state = self._state
            self._state = new_state
            logger.info("State: %s -> %s", old_state.value, new_state.value)

        # Update tray icon to match state
        self._tray_manager.update_state(new_state)

    def _play_audio_cue(self, cue_fn: callable) -> None:
        """Play an audio cue if audio cues are enabled in config.

        Args:
            cue_fn: The audio cue function to call (from notifications module).
        """
        if self.config.audio_cues_enabled:
            cue_fn()

    def _show_error(self, message: str) -> None:
        """Show an error notification and play error audio cue.

        Args:
            message: Error message to display in the toast notification.
        """
        self._play_audio_cue(play_error_cue)
        self._tray_manager.notify(APP_NAME, message)

    def _on_hotkey(self) -> None:
        """Handle the global hotkey press.

        Implements the state machine transitions:
            IDLE -> RECORDING (start recording)
            RECORDING -> PROCESSING (stop recording, start pipeline)
            PROCESSING -> ignored (debounced/blocked)
            PASTING -> ignored (too brief to interact)
        """
        current = self.state
        logger.info(
            "Hotkey callback invoked. Current state: %s", current.value
        )

        if current == AppState.IDLE:
            logger.info("Transition: IDLE -> RECORDING (starting recording)")
            self._start_recording()

        elif current == AppState.RECORDING:
            logger.info("Transition: RECORDING -> PROCESSING (stopping recording)")
            self._stop_recording_and_process()

        elif current == AppState.PROCESSING:
            logger.info("Hotkey pressed during PROCESSING state, ignored.")

        elif current == AppState.PASTING:
            logger.info("Hotkey pressed during PASTING state, ignored.")

    def _on_cancel(self) -> None:
        """Handle the Escape cancel hotkey.

        Only active during RECORDING state. Discards audio, returns to IDLE.
        """
        current = self.state

        if current != AppState.RECORDING:
            logger.debug("Cancel pressed outside RECORDING state, ignored.")
            return

        logger.info("Recording cancelled by user.")

        # Stop recording and discard audio
        self._recorder.stop()

        # Unregister cancel hotkey
        self._hotkey_manager.unregister_cancel()

        # Play cancel cue and return to idle
        self._play_audio_cue(play_cancel_cue)
        self._set_state(AppState.IDLE)

    def _on_auto_stop(self) -> None:
        """Handle auto-stop when recording reaches max duration.

        Called from the AudioRecorder's max-duration timer thread.
        Triggers the same stop-and-process pipeline as a manual hotkey
        press, but also shows a notification informing the user that
        the recording was auto-stopped.
        """
        current = self.state
        if current != AppState.RECORDING:
            logger.debug(
                "Auto-stop fired but state is %s, not RECORDING. Ignored.",
                current.value,
            )
            return

        logger.info("Recording auto-stopped after max duration.")
        self._tray_manager.notify(
            APP_NAME, "Recording auto-stopped after 5 minutes."
        )
        self._stop_recording_and_process()

    def _start_recording(self) -> None:
        """Transition from IDLE to RECORDING."""
        logger.debug("Attempting to start audio recording...")
        success = self._recorder.start()
        if success:
            self._set_state(AppState.RECORDING)
            self._play_audio_cue(play_recording_start_cue)

            # Register Escape cancel hotkey (v0.2)
            self._hotkey_manager.register_cancel(self._on_cancel)
            logger.info("Recording started. Press hotkey again to stop, Escape to cancel.")
        else:
            logger.error("Failed to start recording. Staying in IDLE.")
            self._show_error("No microphone detected. Check your audio settings.")

    def _stop_recording_and_process(self) -> None:
        """Transition from RECORDING to PROCESSING.

        Stops the recording and spawns a worker thread for the
        STT + summarization + paste pipeline.
        """
        self._set_state(AppState.PROCESSING)
        self._play_audio_cue(play_recording_stop_cue)

        # Unregister cancel hotkey (no longer in RECORDING)
        self._hotkey_manager.unregister_cancel()

        audio_data = self._recorder.stop()

        if audio_data is None:
            logger.info("No audio data captured. Returning to IDLE.")
            self._tray_manager.notify(APP_NAME, "No speech detected.")
            self._set_state(AppState.IDLE)
            return

        # Run the pipeline in a worker thread to avoid blocking
        self._pipeline_thread = threading.Thread(
            target=self._run_pipeline,
            args=(audio_data,),
            daemon=True,
            name="pipeline-worker",
        )
        self._pipeline_thread.start()

    def _run_pipeline(self, audio_data: bytes) -> None:
        """Execute the STT, summarization, and paste pipeline in a worker thread.

        Includes clipboard backup/restore for clipboard preservation (US-0.2.5).
        All errors are caught and reported via toast notifications.

        Args:
            audio_data: WAV audio bytes to transcribe and paste.
        """
        # Backup clipboard before we overwrite it (US-0.2.5)
        clip_backup = clipboard_backup()

        try:
            # Step 1: Transcribe
            transcript = self._stt.transcribe(audio_data)

            if not transcript or not transcript.strip():
                logger.info("Empty transcript. Nothing to paste.")
                self._tray_manager.notify(APP_NAME, "No speech detected.")
                self._set_state(AppState.IDLE)
                return

            # Step 2: Summarize (v0.2: CloudLLMSummarizer)
            summary = self._summarizer.summarize(transcript)

            # Handle empty summary (e.g., all filler words removed)
            if not summary or not summary.strip():
                logger.info("Empty summary after processing. Nothing to paste.")
                self._tray_manager.notify(APP_NAME, "No speech detected.")
                self._set_state(AppState.IDLE)
                return

            # Step 3: Paste
            self._set_state(AppState.PASTING)
            success = paste_text(summary)

            if success:
                logger.info("Pipeline complete. Text pasted successfully.")
            else:
                logger.warning("Pipeline complete but paste may have failed.")

        except STTError as e:
            logger.error("STT pipeline error: %s", e)
            self._show_error(f"Transcription error: {e}")

        except SummarizerError as e:
            logger.error("Summarizer pipeline error: %s", e)
            self._show_error(f"Summarization error: {e}")

        except Exception:
            logger.exception("Unexpected error in pipeline.")
            self._show_error("An unexpected error occurred.")

        finally:
            # Always restore clipboard and return to IDLE
            # Brief delay to ensure paste has completed before restoring
            time.sleep(0.1)
            clipboard_restore(clip_backup)
            self._set_state(AppState.IDLE)

    def _shutdown(self) -> None:
        """Clean shutdown of all components."""
        logger.info("Shutting down %s...", APP_NAME)
        self._shutdown_event.set()

        # Stop recording if active
        if self._recorder.is_recording:
            self._recorder.stop()

        # Unregister hotkeys
        self._hotkey_manager.unregister()

        # Stop tray (this unblocks the main thread)
        self._tray_manager.stop()

        logger.info("%s shutdown complete.", APP_NAME)

    def run(self) -> None:
        """Start the application.

        Sets up the hotkey listener in a daemon thread and runs
        the pystray event loop on the main thread.

        Raises:
            RuntimeError: If hotkey registration fails (e.g., no admin
                privileges). The caller (main) is responsible for showing
                a user-visible error message.
        """
        logger.info(
            "Starting %s v%s (hotkey=%s, summarization=%s, audio_cues=%s)",
            APP_NAME,
            APP_VERSION,
            self.config.hotkey,
            "on" if self.config.summarization_enabled else "off",
            "on" if self.config.audio_cues_enabled else "off",
        )

        # Register hotkey (keyboard library runs its own listener thread).
        # On some Windows configurations, the keyboard library requires
        # Administrator privileges. If registration fails, we raise so
        # that main() can show a message box to the user.
        try:
            self._hotkey_manager.register(self._on_hotkey)
        except Exception as exc:
            logger.exception("Failed to register hotkey.")
            raise RuntimeError(
                f"Could not register the hotkey ({self.config.hotkey}).\n\n"
                f"The 'keyboard' library may require Administrator privileges "
                f"on this system.\n\n"
                f"Try right-clicking VoicePaste.exe and selecting "
                f"'Run as administrator'.\n\n"
                f"Technical detail: {exc}"
            ) from exc

        logger.info(
            "Hotkey '%s' registered. Waiting for user input. "
            "If the hotkey does not respond, check the log for errors above, "
            "try running as Administrator, or change the hotkey in config.toml.",
            self.config.hotkey,
        )

        # Run tray on main thread (blocks until stop)
        try:
            self._tray_manager.run()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received.")
            self._shutdown()
        except Exception:
            logger.exception("Tray icon error.")
            self._shutdown()


def main() -> None:
    """Application entry point.

    Handles the full startup sequence with user-visible error reporting.
    In --noconsole builds (release .exe), every failure path shows a
    Windows message box so the user is never left with a silent exit.

    Startup sequence:
        1. Parse --debug flag and allocate console if requested.
        2. Set up bootstrap logging.
        3. Acquire single-instance mutex (REQ-S27).
        4. Load and validate configuration.
        5. Reconfigure logging with config settings.
        6. Create and run the application (tray + hotkey).

    Each step that can fail shows a MessageBox before exiting.
    """
    # ----------------------------------------------------------------
    # Step 0: --debug flag -- allocate a console for the release build
    # ----------------------------------------------------------------
    if "--debug" in sys.argv:
        _enable_debug_console()

    # ----------------------------------------------------------------
    # Step 1: Bootstrap logging (console + minimal format)
    # ----------------------------------------------------------------
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )

    logger.info("=" * 60)
    logger.info("%s v%s starting up...", APP_NAME, APP_VERSION)
    logger.info("Python %s | frozen=%s", sys.version, getattr(sys, "frozen", False))
    logger.info("Working directory: %s", os.getcwd())
    logger.info("Executable: %s", sys.executable)
    logger.info("Arguments: %s", sys.argv)

    # Log keyboard library version for debugging hotkey issues
    try:
        import keyboard as _kb_diag
        logger.info("keyboard library version: %s", _kb_diag.version)
    except Exception as e:
        logger.warning("Could not determine keyboard library version: %s", e)

    # Log sounddevice/PortAudio version for debugging audio issues
    try:
        import sounddevice as _sd_diag
        logger.info(
            "sounddevice %s | PortAudio %s",
            _sd_diag.__version__,
            _sd_diag.get_portaudio_version()[1],
        )
    except Exception as e:
        logger.warning("Could not determine sounddevice version: %s", e)

    logger.info("=" * 60)

    # ----------------------------------------------------------------
    # Step 2: Single-instance mutex (REQ-S27)
    # ----------------------------------------------------------------
    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        msg = (
            f"{APP_NAME} is already running.\n\n"
            f"Look for the tray icon in the system tray "
            f"(click the ^ arrow in the taskbar).\n\n"
            f"If the previous instance is stuck, open Task Manager "
            f"and end the 'VoicePaste' process, then try again."
        )
        logger.error("Cannot start: another instance is already running.")
        _show_fatal_error(msg)
        sys.exit(1)

    try:
        # ------------------------------------------------------------
        # Step 3: Load configuration
        # ------------------------------------------------------------
        config = load_config()
        if config is None:
            # Determine the specific reason for the failure so we can
            # show a helpful message to the user.
            from config import _get_app_directory
            app_dir = _get_app_directory()
            config_path = app_dir / "config.toml"

            if not config_path.exists():
                # Template creation itself failed (OSError).
                msg = (
                    f"config.toml could not be created.\n\n"
                    f"Expected location:\n"
                    f"  {config_path}\n\n"
                    f"Please create the file manually with your "
                    f"OpenAI API key and restart {APP_NAME}.\n\n"
                    f"Check the log file for details:\n"
                    f"  {app_dir / 'voice-paste.log'}"
                )
            else:
                msg = (
                    f"Configuration error in config.toml.\n\n"
                    f"Please check the file at:\n"
                    f"  {config_path}\n\n"
                    f"Most likely the OpenAI API key is empty.\n"
                    f"Open config.toml, set your key under [api], "
                    f"and restart {APP_NAME}.\n\n"
                    f"Other possible issues:\n"
                    f"  - TOML syntax error (mismatched quotes, etc.)\n\n"
                    f"Check the log file for details:\n"
                    f"  {app_dir / 'voice-paste.log'}"
                )

            logger.error("Configuration invalid. Exiting.")
            _show_fatal_error(msg)
            sys.exit(1)

        # ------------------------------------------------------------
        # Step 4: Reconfigure logging with config settings
        # ------------------------------------------------------------
        setup_logging(config)

        # ------------------------------------------------------------
        # Step 5: Create and run the application
        # ------------------------------------------------------------
        app = VoicePasteApp(config)
        app.run()

    except RuntimeError as exc:
        # VoicePasteApp.run() raises RuntimeError for hotkey failures
        logger.error("Startup error: %s", exc)
        _show_fatal_error(str(exc))
        sys.exit(1)

    except Exception:
        # Catch-all for truly unexpected errors. In --noconsole mode,
        # without this the app would just vanish.
        tb = traceback.format_exc()
        logger.critical("Unhandled exception during startup:\n%s", tb)

        # config may not be defined if the error occurred before load_config()
        try:
            log_hint = str(config.log_path)
        except NameError:
            log_hint = "voice-paste.log (next to VoicePaste.exe)"

        _show_fatal_error(
            f"{APP_NAME} encountered an unexpected error and must close.\n\n"
            f"Please check the log file for details:\n"
            f"  {log_hint}\n\n"
            f"Error:\n{tb[:500]}"
        )
        sys.exit(1)

    finally:
        # Always release the mutex on shutdown
        if mutex_handle is not None:
            _release_single_instance_mutex(mutex_handle)


if __name__ == "__main__":
    main()

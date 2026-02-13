"""Voice-to-Summary Paste Tool -- Main Entry Point.

A Windows desktop application that lets users press a hotkey (default: Ctrl+Shift+V),
speak into their microphone, and have a clean German-language summary
automatically pasted at their cursor position.

v0.2 Core Experience: Summarization, tray states, audio cues, clipboard
preservation, error handling with toast notifications, Escape cancel.

v0.2.1: Startup UX improvements -- startup balloon notification, fatal
error message boxes for --noconsole builds, --debug CLI flag.

v0.3: Settings dialog, keyring integration, OpenRouter support,
      configurable model/base_url/prompt, hot-reload.

v0.4: Local STT via faster-whisper. Factory-based backend selection,
      model lifecycle management, dual-mode (cloud/local) support.

Architecture:
    Main thread:   pystray event loop (system tray)
    Thread 1:      keyboard hotkey listener (daemon)
    Thread 2:      Recording + STT + Summarization + Paste pipeline (spawned per session)
    Thread 3:      Settings dialog (tkinter, spawned on demand, v0.3)
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
    PROMPT_SYSTEM_PROMPT,
)
from config import AppConfig, load_config
from audio import AudioRecorder
from stt import CloudWhisperSTT, STTError, create_stt_backend
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
        self._active_mode: str = "summary"  # "summary" or "prompt"

        # Initialize components
        self._recorder = AudioRecorder(on_auto_stop=self._on_auto_stop)

        # v0.4: STT backend via factory (cloud or local)
        self._stt = create_stt_backend(config)
        if self._stt is None:
            logger.warning(
                "No STT backend available (backend=%s). "
                "Configure via Settings dialog.",
                config.stt_backend,
            )

        # v0.3: Build summarizer from config (provider, model, base_url, prompt)
        self._rebuild_summarizer()

        self._hotkey_manager = HotkeyManager(
            hotkey=config.hotkey,
            prompt_hotkey=config.prompt_hotkey,
        )

        # v0.3: TrayManager gets settings callback and state accessor
        self._tray_manager = TrayManager(
            on_quit=self._shutdown,
            on_settings=self._open_settings,
            hotkey_label=config.hotkey,
            prompt_hotkey_label=config.prompt_hotkey,
            get_state=lambda: self.state,
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

    def _rebuild_summarizer(self) -> None:
        """(Re)create the summarizer based on current config.

        Called on init and after settings changes (hot-reload).
        """
        config = self.config
        if not config.summarization_enabled:
            self._summarizer = PassthroughSummarizer()
            logger.info("Summarization disabled (PassthroughSummarizer).")
            return

        api_key = config.active_summarization_api_key
        if not api_key:
            self._summarizer = PassthroughSummarizer()
            logger.warning(
                "No API key for summarization provider '%s'. "
                "Using PassthroughSummarizer.",
                config.summarization_provider,
            )
            return

        self._summarizer = CloudLLMSummarizer(
            api_key=api_key,
            model=config.summarization_model,
            base_url=config.active_summarization_base_url,
            system_prompt=config.active_system_prompt,
        )
        logger.info(
            "Summarizer configured: provider=%s, model=%s, base_url=%s",
            config.summarization_provider,
            config.summarization_model,
            config.active_summarization_base_url or "(default)",
        )

    def _open_settings(self) -> None:
        """Open the settings dialog. Called from tray menu (pystray thread)."""
        from settings_dialog import open_settings_dialog

        opened = open_settings_dialog(
            config=self.config,
            on_save=self._on_settings_saved,
        )
        if not opened:
            logger.info("Settings dialog already open, request ignored.")

    def _on_settings_saved(self, changed_fields: dict) -> None:
        """Handle settings save. Recreate API clients as needed.

        Called from the tkinter settings thread. Thread-safe because
        we only replace object references (atomic under GIL) and the
        pipeline thread checks are guarded by state.

        v0.4: Also handles STT backend switching and local model lifecycle.

        Args:
            changed_fields: Dict of field names that were changed.
        """
        logger.info(
            "Settings saved. Changed fields: %s", list(changed_fields.keys())
        )

        # v0.4: Determine if STT backend needs rebuild
        stt_keys = {
            "openai_api_key",
            "stt_backend",
            "local_model_size",
            "local_device",
            "local_compute_type",
        }
        if changed_fields.keys() & stt_keys:
            # Unload previous local model if switching away from local
            old_stt = self._stt
            if old_stt is not None and hasattr(old_stt, "unload_model"):
                logger.info("Unloading previous local STT model...")
                try:
                    old_stt.unload_model()
                except Exception as e:
                    logger.warning("Error unloading local model: %s", e)

            self._stt = create_stt_backend(self.config)
            if self._stt is not None:
                logger.info(
                    "STT backend rebuilt: %s", type(self._stt).__name__
                )
            else:
                logger.warning("STT backend unavailable after settings change.")

        # Determine if summarizer needs rebuild
        summarizer_keys = {
            "openai_api_key",
            "openrouter_api_key",
            "summarization_provider",
            "summarization_model",
            "summarization_base_url",
            "summarization_enabled",
            "summarization_custom_prompt",
        }
        if changed_fields.keys() & summarizer_keys:
            self._rebuild_summarizer()
            logger.info("Summarizer rebuilt with updated settings.")

        # Notify user via toast
        self._tray_manager.notify(
            APP_NAME, "Settings saved and applied."
        )

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
            logger.info("Transition: IDLE -> RECORDING (summary mode)")
            self._active_mode = "summary"
            self._start_recording()

        elif current == AppState.RECORDING:
            logger.info("Transition: RECORDING -> PROCESSING (stopping recording)")
            self._stop_recording_and_process()

        elif current == AppState.PROCESSING:
            logger.info("Hotkey pressed during PROCESSING state, ignored.")

        elif current == AppState.PASTING:
            logger.info("Hotkey pressed during PASTING state, ignored.")

    def _on_prompt_hotkey(self) -> None:
        """Handle the Voice Prompt hotkey press.

        Same state machine as _on_hotkey, but sets mode to "prompt"
        so the pipeline sends the transcript as a prompt to the LLM
        instead of cleaning/summarizing it.
        """
        current = self.state
        logger.info(
            "Prompt hotkey callback invoked. Current state: %s", current.value
        )

        if current == AppState.IDLE:
            logger.info("Transition: IDLE -> RECORDING (prompt mode)")
            self._active_mode = "prompt"
            self._start_recording()

        elif current == AppState.RECORDING:
            logger.info("Transition: RECORDING -> PROCESSING (stopping recording)")
            self._stop_recording_and_process()

        elif current == AppState.PROCESSING:
            logger.info("Prompt hotkey pressed during PROCESSING state, ignored.")

        elif current == AppState.PASTING:
            logger.info("Prompt hotkey pressed during PASTING state, ignored.")

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
        """Transition from IDLE to RECORDING.

        Performs pre-flight checks before starting the recording:
        - STT backend must be available.
        - For local mode: model must be downloaded and ready.
        """
        # v0.4: Check STT backend availability
        if self._stt is None:
            if self.config.stt_backend == "local":
                # Provide specific guidance based on what is missing
                try:
                    from local_stt import is_faster_whisper_available
                    import model_manager

                    if not is_faster_whisper_available():
                        self._show_error(
                            "Local STT is not available.\n"
                            "The faster-whisper library could not be loaded.\n"
                            "Reinstall it or switch to Cloud mode in Settings."
                        )
                    elif not model_manager.is_model_available(
                        self.config.local_model_size
                    ):
                        self._show_error(
                            f"Whisper model '{self.config.local_model_size}' "
                            f"is not downloaded.\n"
                            f"Right-click the tray icon > Settings > "
                            f"Transcription > Download Model."
                        )
                    else:
                        self._show_error(
                            "Local STT is not available.\n"
                            "Check the log file for details.\n"
                            "Right-click the tray icon > Settings to configure."
                        )
                except Exception:
                    self._show_error(
                        "Local STT is not available.\n"
                        "Check that faster-whisper is installed and "
                        "a model is downloaded.\n"
                        "Right-click the tray icon > Settings to configure."
                    )
            else:
                self._show_error(
                    "No OpenAI API key configured.\n"
                    "Right-click the tray icon > Settings to add your key."
                )
            return

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

            # Step 2: Summarize or Prompt (v0.5: voice prompt mode)
            if self._active_mode == "prompt":
                logger.info("Prompt mode: sending transcript as prompt to LLM.")
                summary = self._summarizer.summarize(
                    transcript, system_prompt=PROMPT_SYSTEM_PROMPT
                )
            else:
                summary = self._summarizer.summarize(transcript)

            # Handle empty result (e.g., all filler words removed)
            if not summary or not summary.strip():
                logger.info("Empty result after processing. Nothing to paste.")
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
            self._show_error(f"Transcription error:\n{e}")

        except SummarizerError as e:
            logger.error("Summarizer pipeline error: %s", e)
            self._show_error(f"Summarization error:\n{e}")

        except ImportError as e:
            # Catches late-binding import failures in local STT (e.g.,
            # faster-whisper's CTranslate2 DLL not found, numpy ABI
            # mismatch, etc.)
            logger.error(
                "Import error during pipeline: %s: %s",
                type(e).__name__,
                e,
            )
            error_msg = str(e)
            if "DLL" in error_msg or "dll" in error_msg:
                self._show_error(
                    "A required library (DLL) could not be loaded.\n"
                    "Install the Visual C++ Redistributable (x64):\n"
                    "https://aka.ms/vs/17/release/vc_redist.x64.exe"
                )
            else:
                self._show_error(
                    f"A required module could not be loaded:\n{error_msg}\n\n"
                    f"Try reinstalling the application."
                )

        except RuntimeError as e:
            # CTranslate2 and other native libs raise RuntimeError for
            # internal failures (CUDA errors, model corruption, etc.)
            logger.error(
                "RuntimeError during pipeline: %s: %s",
                type(e).__name__,
                e,
            )
            error_msg = str(e)
            if "cuda" in error_msg.lower() or "gpu" in error_msg.lower():
                self._show_error(
                    "GPU error during processing.\n"
                    "Try setting device to 'cpu' in Settings."
                )
            elif "out of memory" in error_msg.lower():
                self._show_error(
                    "Out of memory.\n"
                    "Try a smaller model or shorter recording."
                )
            else:
                self._show_error(
                    "Processing error.\n"
                    "Check the log file for details."
                )

        except MemoryError:
            logger.error("Out of memory during pipeline execution.")
            self._show_error(
                "Out of memory.\n"
                "Try a smaller model or shorter recording, "
                "or close other applications."
            )

        except Exception:
            logger.exception("Unexpected error in pipeline.")
            self._show_error(
                "An unexpected error occurred.\n"
                "Check the log file for details."
            )

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

        # v0.4: Unload local STT model to free memory
        if self._stt is not None and hasattr(self._stt, "unload_model"):
            try:
                self._stt.unload_model()
                logger.info("Local STT model unloaded.")
            except Exception as e:
                logger.warning("Error unloading local STT model: %s", e)

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
            "Starting %s v%s (hotkey=%s, stt=%s, summarization=%s, audio_cues=%s)",
            APP_NAME,
            APP_VERSION,
            self.config.hotkey,
            self.config.stt_backend,
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

        # Register Voice Prompt hotkey (v0.5)
        try:
            self._hotkey_manager.register_prompt(self._on_prompt_hotkey)
        except Exception as exc:
            logger.warning(
                "Failed to register prompt hotkey '%s': %s. "
                "Voice Prompt mode will not be available.",
                self.config.prompt_hotkey,
                exc,
            )

        logger.info(
            "Hotkeys registered: summary='%s', prompt='%s'. "
            "Waiting for user input.",
            self.config.hotkey,
            self.config.prompt_hotkey,
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
        # v0.3: Missing API key is no longer fatal. The user can
        # enter it via the Settings dialog after the app starts.
        # ------------------------------------------------------------
        config = load_config()
        if config is None:
            # load_config() only returns None on unrecoverable errors.
            # Use a default config so the Settings dialog can still open.
            logger.warning(
                "Could not load config. Starting with defaults. "
                "Use Settings dialog to configure."
            )
            from config import AppConfig as _AC
            config = _AC()

        # v0.3: Log warning if no API key (no longer fatal)
        if not config.openai_api_key:
            logger.warning(
                "No OpenAI API key configured. "
                "Right-click the tray icon > Settings to add your key."
            )

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

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

import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback

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
    PASTE_COUNTDOWN_BEEP_DURATION_MS,
    PASTE_COUNTDOWN_BEEP_FREQ,
    PROMPT_SYSTEM_PROMPT,
    SUPPORTED_LANGUAGES,
    TTS_MAX_TEXT_LENGTH,
    VALID_TRANSITIONS,
)
from config import AppConfig, load_config
from audio import AudioRecorder
from stt import CloudWhisperSTT, STTError, create_stt_backend
from summarizer import CloudLLMSummarizer, PassthroughSummarizer, SummarizerError
from platform_impl import (
    acquire_single_instance_lock,
    clipboard_backup,
    clipboard_restore,
    enable_debug_console,
    paste_text,
    play_beep,
    register_key_press,
    release_single_instance_lock,
    send_key,
    show_fatal_error,
    unregister_key_hook,
)
from hotkey import HotkeyManager
from tray import TrayManager
from tts import TTSError, create_tts_backend
from tts_cache import TTSAudioCache, CacheConfig, CacheKey
from tts_export import TTSAudioExporter
from audio_playback import AudioPlayer
from notifications import (
    play_cancel_cue,
    play_error_cue,
    play_recording_start_cue,
    play_recording_stop_cue,
    play_wakeword_cue,
)
from api_server import start_api_server, stop_api_server
from api_dispatch import APIController
from claude_code import (
    ClaudeCodeBackend,
    ClaudeCodeError,
    ClaudeCodeNotFoundError,
    ClaudeCodeTimeoutError,
)
from tts_orchestrator import TTSOrchestrator

logger = logging.getLogger(APP_NAME)

# Platform functions imported from platform_impl:
#   show_fatal_error, enable_debug_console,
#   acquire_single_instance_lock, release_single_instance_lock


def setup_logging(config: AppConfig, force_debug: bool = False) -> None:
    """Configure logging to file and console.

    REQ-S01: API key is never logged (handled by config.masked_api_key).
    REQ-S11: Audio data is never logged.
    REQ-S25: Only safe data is logged.

    Args:
        config: Application configuration with log level and paths.
        force_debug: If True, override config log level with DEBUG
            (used by --verbose CLI flag).
    """
    log_level = logging.DEBUG if force_debug else getattr(logging, config.log_level, logging.INFO)

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
        self._active_mode: str = "summary"  # "summary", "prompt", "tts", "tts_ask"

        # Initialize components
        self._recorder = AudioRecorder(
            on_auto_stop=self._on_auto_stop,
            device=config.audio_device_index,
        )

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

        # v0.6: TTS backend and audio player
        self._tts = None
        self._audio_player = AudioPlayer()
        self._rebuild_tts()

        # v1.0: TTS audio cache
        self._tts_cache = self._create_tts_cache()

        # v1.0: TTS audio export (permanent saves to user directory)
        self._tts_exporter = self._create_tts_exporter()

        self._hotkey_manager = HotkeyManager(
            hotkey=config.hotkey,
            prompt_hotkey=config.prompt_hotkey,
            tts_hotkey=config.tts_hotkey,
            tts_ask_hotkey=config.tts_ask_hotkey,
            claude_code_hotkey=config.claude_code_hotkey,
            terminal_mode_hotkey=config.terminal_mode_hotkey,
        )

        # v1.2: Claude Code CLI backend
        self._claude_code: ClaudeCodeBackend | None = None
        self._rebuild_claude_code()

        # v0.3: TrayManager gets settings callback and state accessor
        # v0.8.7: Pass TTS config so startup notification shows TTS hotkeys
        self._tray_manager = TrayManager(
            on_quit=self._shutdown,
            on_settings=self._open_settings,
            hotkey_label=config.hotkey,
            prompt_hotkey_label=config.prompt_hotkey,
            get_state=lambda: self.state,
            tts_enabled=config.tts_enabled,
            tts_hotkey_label=config.tts_hotkey,
            tts_ask_hotkey_label=config.tts_ask_hotkey,
            on_handsfree_toggle=self._toggle_handsfree,
            get_handsfree_active=lambda: self._handsfree_active,
            get_tts_cache_entries=lambda: self._tts_cache.list_entries(),
            on_tts_replay=lambda eid: self.replay_tts_entry(eid),
            on_tts_cache_clear=lambda: self._tts_cache.clear(),
            on_language_changed=self._on_language_changed,
            get_current_language=lambda: self.config.transcription_language,
            claude_code_enabled=config.claude_code_enabled,
            claude_code_hotkey_label=config.claude_code_hotkey,
            get_claude_code_available=lambda: self._claude_code is not None,
            on_claude_new_conversation=self._on_claude_new_conversation,
            on_terminal_mode_toggle=self._toggle_terminal_mode,
            get_terminal_mode_active=lambda: self._terminal_mode,
            terminal_mode_hotkey_label=config.terminal_mode_hotkey,
        )

        # v1.2: TTS orchestrator (extracted from main.py)
        self._tts_orchestrator = TTSOrchestrator(
            config=self.config,
            tts_backend=self._tts,
            audio_player=self._audio_player,
            tts_cache=self._tts_cache,
            tts_exporter=self._tts_exporter,
            set_state=self._set_state,
            get_state=lambda: self.state,
            register_cancel=self._hotkey_manager.register_cancel,
            unregister_cancel=self._hotkey_manager.unregister_cancel,
            show_error=self._show_error,
        )
        self._tts_orchestrator.on_cancel = self._on_cancel

        # v1.3: Terminal Mode toggle (runtime-only, not persisted)
        self._terminal_mode: bool = False

        self._shutdown_event = threading.Event()
        self._pipeline_thread: threading.Thread | None = None
        # Pipeline queueing: allow one recording while processing
        self._queued_audio: bytes | None = None
        self._queued_mode: str = "summary"
        self._recording_during_processing: bool = False
        # v0.9: Confirm-before-paste synchronization
        self._paste_confirm_event = threading.Event()
        self._paste_cancel_event = threading.Event()
        # v0.9: Hands-Free Mode
        self._wake_detector = None
        self._handsfree_active: bool = False
        # v0.9: HTTP API server
        self._api_server = None
        self._api_thread = None
        # v1.1: API dispatch controller (extracted from main.py)
        self._api_controller = APIController(
            app=self,
            tts_backend=self._tts,
            audio_player=self._audio_player,
            tts_cache=self._tts_cache,
            tts_exporter=self._tts_exporter,
            paste_cancel_event=self._paste_cancel_event,
        )

    @property
    def state(self) -> AppState:
        """Current application state (thread-safe read)."""
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: AppState) -> None:
        """Set the application state (thread-safe).

        Validates the transition against the explicit transition table,
        updates the tray icon to reflect the new state.
        All state transitions are logged.

        Args:
            new_state: The new application state.
        """
        with self._state_lock:
            old_state = self._state
            if old_state != new_state and (old_state, new_state) not in VALID_TRANSITIONS:
                logger.error(
                    "Invalid state transition: %s -> %s",
                    old_state.value,
                    new_state.value,
                )
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

    def _rebuild_tts(self) -> None:
        """(Re)create the TTS backend based on current config.

        Called on init and after settings changes (hot-reload).
        """
        config = self.config
        if not config.tts_enabled:
            self._tts = None
            logger.info("TTS disabled.")
            return

        self._tts = create_tts_backend(
            api_key=config.elevenlabs_api_key,
            provider=config.tts_provider,
            voice_id=config.tts_voice_id,
            model_id=config.tts_model_id,
            output_format=config.tts_output_format,
            local_voice=config.tts_local_voice,
            speed=config.tts_speed,
        )
        if self._tts is not None:
            logger.info("TTS backend ready: %s", config.tts_provider)
        else:
            logger.warning("TTS backend unavailable (no API key?).")

    def _rebuild_claude_code(self) -> None:
        """(Re)create the Claude Code backend based on current config.

        Called on init and after settings changes (hot-reload).
        """
        if self.config.claude_code_enabled and ClaudeCodeBackend.is_available():
            self._claude_code = ClaudeCodeBackend(
                working_directory=self.config.claude_code_working_dir or None,
                system_prompt=self.config.claude_code_system_prompt or None,
                timeout_seconds=self.config.claude_code_timeout,
                skip_permissions=self.config.claude_code_skip_permissions,
                continue_conversation=self.config.claude_code_continue_conversation,
            )
            logger.info("Claude Code backend ready (cwd=%s).",
                        self.config.claude_code_working_dir or "(default)")
        else:
            self._claude_code = None
            if self.config.claude_code_enabled:
                logger.warning("Claude Code enabled but CLI not found in PATH.")
            else:
                logger.debug("Claude Code integration disabled.")

    def _on_claude_new_conversation(self) -> None:
        """Reset Claude Code conversation state (tray menu callback)."""
        if self._claude_code:
            self._claude_code.new_conversation()
            self._tray_manager.notify(APP_NAME, "Claude Code: New conversation started.")
        else:
            logger.debug("New conversation requested but Claude Code not active.")

    def _create_tts_cache(self) -> TTSAudioCache:
        """Create a TTS audio cache from current config."""
        cfg = CacheConfig(
            enabled=self.config.tts_cache_enabled,
            max_size_mb=self.config.tts_cache_max_size_mb,
            max_age_days=self.config.tts_cache_max_age_days,
            max_entries=self.config.tts_cache_max_entries,
        )
        return TTSAudioCache(cfg)

    def _get_tts_cache_key(self, text: str) -> CacheKey:
        """Build a CacheKey from the current TTS config and text."""
        return self._tts_orchestrator.get_cache_key(text)

    def _get_tts_voice_label(self) -> str:
        """Get a human-readable label for the current TTS voice."""
        return self._tts_orchestrator.get_voice_label()

    def _create_tts_exporter(self) -> TTSAudioExporter:
        """Create a TTS audio exporter from current config.

        Returns:
            Configured TTSAudioExporter instance.
        """
        from pathlib import Path

        export_path = Path(self.config.tts_export_path) if self.config.tts_export_path else Path("")
        return TTSAudioExporter(
            export_dir=export_path,
            enabled=self.config.tts_export_enabled,
        )

    def _export_tts_audio(self, text: str, audio_data: bytes) -> None:
        """Export TTS audio to the user's export directory if enabled."""
        self._tts_orchestrator.export_audio(text, audio_data)

    def replay_tts_entry(self, entry_id: str) -> bool:
        """Replay a cached TTS entry by its ID. Returns True if started."""
        return self._tts_orchestrator.replay_entry(entry_id)

    def _on_tts_hotkey(self) -> None:
        """Handle the TTS clipboard readout hotkey (Ctrl+Alt+T).

        Reads the current clipboard text and speaks it aloud via TTS.
        If already SPEAKING, stops the current playback.
        """
        current = self.state
        logger.info("TTS hotkey invoked. State: %s", current.value)

        if current == AppState.SPEAKING:
            logger.info("Stopping TTS playback.")
            self._audio_player.stop()
            return

        if current != AppState.IDLE:
            logger.info("TTS hotkey ignored (state=%s).", current.value)
            return

        if not self._tts:
            self._show_error(
                "TTS is not configured.\n"
                "Right-click tray > Settings > Text-to-Speech."
            )
            return

        # Read clipboard content using ctypes-based clipboard API (paste.py)
        text = clipboard_backup()
        if text is None:
            text = ""

        text = text.strip() if text else ""
        if not text:
            self._tray_manager.notify(APP_NAME, "Clipboard is empty.")
            return

        if len(text) > TTS_MAX_TEXT_LENGTH:
            self._tray_manager.notify(
                APP_NAME,
                f"Text too long for TTS ({len(text)} chars, max {TTS_MAX_TEXT_LENGTH}).",
            )
            return

        # Start TTS pipeline in worker thread
        self._set_state(AppState.PROCESSING)
        thread = threading.Thread(
            target=self._run_tts_pipeline,
            args=(text,),
            daemon=True,
            name="tts-worker",
        )
        thread.start()

    def _on_tts_ask_hotkey(self) -> None:
        """Handle the TTS Ask AI + readout hotkey (Ctrl+Alt+Y).

        Records speech, transcribes, sends to LLM, then reads the
        LLM answer aloud via TTS. The answer is also placed on the
        clipboard (but NOT auto-pasted).
        """
        current = self.state
        logger.info("TTS Ask hotkey invoked. State: %s", current.value)

        if current == AppState.SPEAKING:
            logger.info("Stopping TTS playback.")
            self._audio_player.stop()
            return

        if current == AppState.IDLE:
            if not self._tts:
                self._show_error(
                    "TTS is not configured.\n"
                    "Right-click tray > Settings > Text-to-Speech."
                )
                return
            logger.info("Transition: IDLE -> RECORDING (tts_ask mode)")
            self._active_mode = "tts_ask"
            self._start_recording()
        elif current == AppState.RECORDING:
            logger.info("Transition: RECORDING -> PROCESSING")
            self._stop_recording_and_process()
        else:
            logger.info("TTS Ask hotkey ignored (state=%s).", current.value)

    def _on_claude_code_hotkey(self) -> None:
        """Handle the Claude Code hotkey (Ctrl+Alt+C).

        Records speech, transcribes, sends to Claude Code CLI, then
        delivers the response based on config (paste, speak, or both).
        """
        current = self.state
        logger.info("Claude Code hotkey invoked. State: %s", current.value)

        if current == AppState.SPEAKING:
            logger.info("Stopping TTS playback.")
            self._audio_player.stop()
            return

        if current == AppState.IDLE:
            if not self._claude_code:
                if not ClaudeCodeBackend.is_available():
                    self._show_error(
                        "Claude Code CLI not found.\n"
                        "Install: npm i -g @anthropic-ai/claude-code"
                    )
                else:
                    self._show_error(
                        "Claude Code integration is disabled.\n"
                        "Enable in Settings > Claude Code."
                    )
                return
            logger.info("Transition: IDLE -> RECORDING (claude_code mode)")
            self._active_mode = "claude_code"
            self._start_recording()

        elif current == AppState.RECORDING:
            if self._recording_during_processing:
                logger.info("Stopping queued recording (pipeline still processing).")
                self._stop_queued_recording()
            else:
                logger.info("Transition: RECORDING -> PROCESSING")
                self._stop_recording_and_process()

        elif current == AppState.PROCESSING:
            if self._recording_during_processing:
                logger.info("Queue full: already recording during PROCESSING.")
                self._play_audio_cue(play_error_cue)
            else:
                logger.info("Starting queued recording during PROCESSING.")
                self._queued_mode = "claude_code"
                self._start_queued_recording()

        else:
            logger.info("Claude Code hotkey ignored (state=%s).", current.value)

    def _run_tts_pipeline(self, text: str) -> None:
        """Synthesize text and play audio. Delegates to TTSOrchestrator."""
        self._tts_orchestrator.synthesize_and_play(text)

    def _run_tts_export_pipeline(self, text: str, filename_hint: str = "") -> None:
        """Synthesize text and export to file. Delegates to TTSOrchestrator."""
        self._tts_orchestrator.synthesize_and_export(text, filename_hint)

    # --- v0.9: HTTP API ---

    def _api_dispatch(self, command: dict) -> dict:
        """Handle an API command and return a JSON-serializable response.

        Delegates to the extracted APIController (v1.1).

        Args:
            command: Dict with "action" key and optional parameters.

        Returns:
            Response dict with "status" key.
        """
        return self._api_controller.dispatch(command)

    def _start_api_server(self) -> None:
        """Start the HTTP API server if enabled."""
        if not self.config.api_enabled:
            return
        try:
            self._api_server, self._api_thread = start_api_server(
                port=self.config.api_port,
                dispatch=self._api_dispatch,
            )
            self._tray_manager.notify(
                APP_NAME,
                f"HTTP API started on http://127.0.0.1:{self.config.api_port}",
            )
        except OSError as e:
            logger.error("Failed to start API server: %s", e)
            self._tray_manager.notify(
                APP_NAME,
                f"API server failed to start (port {self.config.api_port} in use?)",
            )

    def _stop_api_server(self) -> None:
        """Stop the HTTP API server if running."""
        if self._api_server is not None:
            stop_api_server(self._api_server)
            self._api_server = None
            self._api_thread = None

    # --- v0.9: Hands-Free Mode ---

    def _start_handsfree(self) -> None:
        """Start the wake word detector for Hands-Free mode."""
        if self._handsfree_active:
            logger.info("Hands-Free already active.")
            return

        try:
            from local_stt import is_faster_whisper_available
            if not is_faster_whisper_available():
                self._show_error(
                    "Hands-Free Mode requires faster-whisper.\n"
                    "Install it or use the Local STT build."
                )
                return
        except ImportError:
            self._show_error("Hands-Free Mode requires the faster-whisper library.")
            return

        from wake_word import WakeWordDetector

        self._wake_detector = WakeWordDetector(
            wake_phrase=self.config.wake_phrase,
            on_detected=self._on_wake_word_detected,
            cooldown_seconds=self.config.handsfree_cooldown_seconds,
            match_mode=self.config.wake_phrase_match_mode,
            language="en",  # Wake phrases are typically English
            should_listen=lambda: self.state == AppState.IDLE,
        )

        success = self._wake_detector.start()
        if success:
            self._handsfree_active = True
            logger.info("Hands-Free mode started.")
            logger.debug("Wake phrase: '%s'", self.config.wake_phrase)
            self._tray_manager.notify(
                APP_NAME,
                f"Hands-Free mode ON\nSay \"{self.config.wake_phrase}\" to start recording.",
            )
            self._tray_manager.update_state(self.state)
        else:
            self._show_error("Failed to start Hands-Free mode.\nCheck the log file for details.")

    def _stop_handsfree(self) -> None:
        """Stop the wake word detector."""
        if not self._handsfree_active:
            return
        if self._wake_detector is not None:
            self._wake_detector.stop()
            self._wake_detector.unload_model()
            self._wake_detector = None
        self._handsfree_active = False
        logger.info("Hands-Free mode stopped.")
        self._tray_manager.notify(APP_NAME, "Hands-Free mode OFF")
        self._tray_manager.update_state(self.state)

    def _toggle_handsfree(self) -> None:
        """Toggle Hands-Free mode on/off. Called from tray menu."""
        if self._handsfree_active:
            self._stop_handsfree()
            self.config.handsfree_enabled = False
        else:
            self.config.handsfree_enabled = True
            self._start_handsfree()
        self.config.save_to_toml()

    def _on_wake_word_detected(self) -> None:
        """Handle wake word detection. Called from the wake word detector thread."""
        current = self.state
        if current != AppState.IDLE:
            logger.info("Wake word detected but state is %s. Ignored.", current.value)
            return

        logger.info(
            "Wake word detected! Starting Hands-Free recording (pipeline=%s).",
            self.config.handsfree_pipeline,
        )

        # Play confirmation tone
        self._play_audio_cue(play_wakeword_cue)
        time.sleep(0.15)  # Brief pause so tone is audible before recording

        # Set pipeline mode and start recording with silence auto-stop
        # Map config pipeline names to internal _active_mode names
        _pipeline_to_mode = {
            "ask_tts": "tts_ask", "summary": "summary",
            "prompt": "prompt", "claude_code": "claude_code",
        }
        self._active_mode = _pipeline_to_mode.get(self.config.handsfree_pipeline, "tts_ask")
        # Start recording for hands-free pipeline
        self._start_recording_handsfree()

    def _start_recording_handsfree(self) -> None:
        """Start recording for Hands-Free mode with silence-based auto-stop."""
        if self._stt is None:
            self._show_error("No STT backend available for Hands-Free recording.")
            return

        # Create a new AudioRecorder with silence auto-stop
        self._recorder = AudioRecorder(
            on_auto_stop=self._on_auto_stop,
            on_silence_stop=self._on_silence_auto_stop,
            silence_timeout_seconds=self.config.silence_timeout_seconds,
            max_duration_override=self.config.handsfree_max_recording_seconds,
            device=self.config.audio_device_index,
        )

        success = self._recorder.start()
        if success:
            self._set_state(AppState.RECORDING)
            # Register Escape to cancel
            self._hotkey_manager.register_cancel(self._on_cancel)
            logger.info(
                "Hands-Free recording started. Auto-stop on %.1fs silence.",
                self.config.silence_timeout_seconds,
            )
        else:
            logger.error("Failed to start Hands-Free recording.")
            self._show_error("No microphone detected.")

    def _on_silence_auto_stop(self) -> None:
        """Handle auto-stop triggered by silence detection."""
        current = self.state
        if current != AppState.RECORDING:
            return
        logger.info("Silence auto-stop triggered (Hands-Free mode).")
        self._tray_manager.notify(APP_NAME, "Silence detected — processing...")
        self._stop_recording_and_process()

    def _get_effective_paste_shortcut(self) -> str:
        """Determine the effective paste shortcut considering terminal mode.

        Terminal Mode (runtime toggle) overrides the config's paste_shortcut.
        When Terminal Mode is active, always uses Ctrl+Shift+V.
        Otherwise, uses the config setting (auto/ctrl+v/ctrl+shift+v).

        Returns:
            Paste shortcut string: "ctrl+shift+v", "ctrl+v", or "auto".
        """
        if self._terminal_mode:
            return "ctrl+shift+v"
        if self.config.paste_shortcut != "auto":
            return self.config.paste_shortcut
        return "auto"

    def _toggle_terminal_mode(self) -> None:
        """Toggle terminal paste mode (Ctrl+Shift+V vs Ctrl+V).

        This is a runtime toggle that overrides the paste_shortcut config.
        When enabled, all paste operations use Ctrl+Shift+V (terminal shortcut).
        When disabled, the config's paste_shortcut setting is used.
        The state does NOT persist to config.toml -- it resets on restart.

        Useful on Wayland where auto-detection of the focused window is unreliable.
        """
        self._terminal_mode = not self._terminal_mode
        mode_label = "Terminal (Ctrl+Shift+V)" if self._terminal_mode else "GUI (Ctrl+V)"
        logger.info("Paste mode toggled: %s", mode_label)
        # Update tray menu to reflect new check state
        if hasattr(self, '_tray_manager'):
            self._tray_manager.refresh_menu()
        # Show notification
        self._tray_manager.notify(APP_NAME, f"Paste: {mode_label}")

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

        # v0.6+v0.7: Determine if TTS backend needs rebuild
        tts_keys = {
            "tts_enabled",
            "elevenlabs_api_key",
            "tts_provider",
            "tts_voice_id",
            "tts_model_id",
            "tts_output_format",
            "tts_local_voice",
        }
        if changed_fields.keys() & tts_keys:
            # Unload previous local TTS model if switching away
            old_tts = self._tts
            if old_tts is not None and hasattr(old_tts, "unload_model"):
                logger.info("Unloading previous local TTS model...")
                try:
                    old_tts.unload_model()
                except Exception as e:
                    logger.warning("Error unloading local TTS model: %s", e)

            self._rebuild_tts()
            self._tts_orchestrator.update_tts(self._tts)
            self._api_controller.update_tts(self._tts)
            logger.info("TTS backend rebuilt with updated settings.")

            # Register/unregister TTS hotkeys based on new enabled state
            if self.config.tts_enabled:
                if not self._hotkey_manager._tts_registered:
                    try:
                        self._hotkey_manager.register_tts(self._on_tts_hotkey)
                    except Exception as exc:
                        logger.warning("Failed to register TTS hotkey: %s", exc)
                if not self._hotkey_manager._tts_ask_registered:
                    try:
                        self._hotkey_manager.register_tts_ask(self._on_tts_ask_hotkey)
                    except Exception as exc:
                        logger.warning("Failed to register TTS Ask hotkey: %s", exc)
            else:
                self._hotkey_manager.unregister_tts()

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

        # v0.9: Hands-Free hot-reload
        handsfree_keys = {
            "handsfree_enabled", "wake_phrase", "silence_timeout_seconds",
            "handsfree_pipeline", "wake_phrase_match_mode",
            "handsfree_cooldown_seconds", "handsfree_max_recording_seconds",
        }
        if changed_fields.keys() & handsfree_keys:
            if self._handsfree_active:
                self._stop_handsfree()
            # Only restart if IDLE — avoid stream conflicts during recording
            if self.config.handsfree_enabled and self.state == AppState.IDLE:
                self._start_handsfree()

        # v1.0: TTS cache hot-reload
        cache_keys = {
            "tts_cache_enabled", "tts_cache_max_size_mb",
            "tts_cache_max_age_days", "tts_cache_max_entries",
        }
        if changed_fields.keys() & cache_keys:
            self._tts_cache = self._create_tts_cache()
            self._tts_orchestrator.update_cache(self._tts_cache)
            self._api_controller.update_cache(self._tts_cache)
            logger.info("TTS cache rebuilt with updated settings.")

        # v1.0: TTS export hot-reload
        export_keys = {"tts_export_enabled", "tts_export_path"}
        if changed_fields.keys() & export_keys:
            self._tts_exporter = self._create_tts_exporter()
            self._tts_orchestrator.update_exporter(self._tts_exporter)
            self._api_controller.update_exporter(self._tts_exporter)
            logger.info("TTS exporter rebuilt with updated settings.")

        # v0.9: API server hot-reload
        api_keys = {"api_enabled", "api_port"}
        if changed_fields.keys() & api_keys:
            self._stop_api_server()
            self._start_api_server()

        # v1.2: Claude Code hot-reload
        claude_keys = {
            "claude_code_enabled", "claude_code_working_dir",
            "claude_code_system_prompt", "claude_code_timeout",
            "claude_code_response_mode", "claude_code_skip_permissions",
            "claude_code_continue_conversation",
        }
        if changed_fields.keys() & claude_keys:
            self._rebuild_claude_code()
            if self.config.claude_code_enabled and self._claude_code:
                if not self._hotkey_manager._slots["claude_code"].registered:
                    try:
                        self._hotkey_manager.register_claude_code(
                            self._on_claude_code_hotkey
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to register Claude Code hotkey: %s", exc
                        )
            else:
                self._hotkey_manager.unregister_claude_code()
            logger.info("Claude Code backend rebuilt with updated settings.")

        # Notify user via toast
        self._tray_manager.notify(
            APP_NAME, "Settings saved and applied."
        )

    def _on_language_changed(self, lang_code: str) -> None:
        """Handle language change from tray submenu.

        Updates config, saves to disk, and notifies the user.

        Args:
            lang_code: Language code (e.g. "de", "en", "auto").
        """
        if lang_code == self.config.transcription_language:
            return
        self.config.transcription_language = lang_code
        self.config.save_to_toml()
        display = SUPPORTED_LANGUAGES.get(lang_code, lang_code)
        logger.info("Transcription language changed to '%s' (%s).", lang_code, display)
        self._tray_manager.notify(APP_NAME, f"Language: {display}")

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
            if self._recording_during_processing:
                # Stop queued recording: save audio, stay in PROCESSING
                logger.info("Stopping queued recording (pipeline still processing).")
                self._stop_queued_recording()
            else:
                logger.info("Transition: RECORDING -> PROCESSING (stopping recording)")
                self._stop_recording_and_process()

        elif current == AppState.PROCESSING:
            if self._recording_during_processing:
                # Already recording while processing — queue is full
                logger.info("Queue full: already recording during PROCESSING.")
                self._play_audio_cue(play_error_cue)
            else:
                # Start a new recording while pipeline is processing
                logger.info("Starting queued recording during PROCESSING.")
                self._queued_mode = "summary"
                self._start_queued_recording()

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
            if self._recording_during_processing:
                logger.info("Stopping queued recording (pipeline still processing).")
                self._stop_queued_recording()
            else:
                logger.info("Transition: RECORDING -> PROCESSING (stopping recording)")
                self._stop_recording_and_process()

        elif current == AppState.PROCESSING:
            if self._recording_during_processing:
                logger.info("Queue full: already recording during PROCESSING.")
                self._play_audio_cue(play_error_cue)
            else:
                logger.info("Starting queued recording during PROCESSING.")
                self._queued_mode = "prompt"
                self._start_queued_recording()

        elif current == AppState.PASTING:
            logger.info("Prompt hotkey pressed during PASTING state, ignored.")

    def _on_cancel(self) -> None:
        """Handle the Escape cancel hotkey.

        Active during RECORDING state (discards audio), SPEAKING state
        (stops TTS playback), and AWAITING_PASTE state (cancels paste, v0.9).
        Returns to IDLE (or PROCESSING if cancelling a queued recording).
        """
        current = self.state

        if current == AppState.SPEAKING:
            logger.info("TTS playback cancelled by user.")
            self._audio_player.stop()
            # State transition happens in _run_tts_pipeline finally block
            return

        if current == AppState.AWAITING_PASTE:
            logger.info("Paste cancelled by user (via _on_cancel).")
            self._paste_cancel_event.set()
            return

        if current == AppState.PROCESSING and self._recording_during_processing:
            # Cancel queued recording, but keep the pipeline running
            logger.info("Queued recording cancelled by user.")
            self._recorder.stop()
            self._recording_during_processing = False
            self._queued_audio = None
            self._hotkey_manager.unregister_cancel()
            self._play_audio_cue(play_cancel_cue)
            # Stay in PROCESSING — the pipeline is still running
            self._tray_manager.update_state(AppState.PROCESSING)
            return

        if current != AppState.RECORDING:
            logger.debug("Cancel pressed outside RECORDING/SPEAKING/AWAITING_PASTE state, ignored.")
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

        # Handle auto-stop during queued recording
        if current == AppState.PROCESSING and self._recording_during_processing:
            logger.info("Queued recording auto-stopped after max duration.")
            self._tray_manager.notify(
                APP_NAME, "Queued recording auto-stopped."
            )
            self._stop_queued_recording()
            return

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

    def _start_queued_recording(self) -> None:
        """Start a new recording while the pipeline is still processing.

        Does NOT change the application state (stays in PROCESSING).
        The recorder runs in the background; audio is saved to
        _queued_audio when the user stops the recording.
        """
        if self._stt is None:
            logger.info("No STT backend — cannot queue recording.")
            self._play_audio_cue(play_error_cue)
            return

        # Create a fresh recorder for the queued recording
        self._recorder = AudioRecorder(
            on_auto_stop=self._on_auto_stop,
            device=self.config.audio_device_index,
        )
        success = self._recorder.start()
        if success:
            self._recording_during_processing = True
            self._play_audio_cue(play_recording_start_cue)
            self._hotkey_manager.register_cancel(self._on_cancel)
            # Update tray tooltip to indicate recording + processing
            self._tray_manager.set_processing_step("Recording (queued)...")
            logger.info("Queued recording started.")
        else:
            logger.error("Failed to start queued recording.")
            self._play_audio_cue(play_error_cue)

    def _stop_queued_recording(self) -> None:
        """Stop a queued recording and save the audio for later processing.

        Does NOT start a new pipeline thread — the audio is saved to
        _queued_audio and will be picked up by _run_pipeline's finally block.
        """
        self._play_audio_cue(play_recording_stop_cue)
        self._hotkey_manager.unregister_cancel()

        audio_data = self._recorder.stop()
        self._recording_during_processing = False

        if audio_data is None:
            logger.info("No audio captured in queued recording.")
            self._queued_audio = None
        else:
            self._queued_audio = audio_data
            self._active_mode = self._queued_mode
            logger.info(
                "Queued recording saved (%d bytes). Will process after current pipeline.",
                len(audio_data),
            )

        # Restore tray to processing state
        self._tray_manager.update_state(AppState.PROCESSING)

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

    def _on_paste_confirm(self) -> None:
        """Handle Enter key press during AWAITING_PASTE state."""
        if self.state == AppState.AWAITING_PASTE:
            logger.info("Paste confirmed by user (Enter).")
            self._paste_confirm_event.set()

    def _on_paste_cancel(self) -> None:
        """Handle Escape key press during AWAITING_PASTE state."""
        if self.state == AppState.AWAITING_PASTE:
            logger.info("Paste cancelled by user (Escape).")
            self._paste_cancel_event.set()

    def _wait_before_paste(self, summary: str) -> bool:
        """Wait for user confirmation or delay before pasting.

        Returns True if the paste should proceed, False if cancelled.

        Args:
            summary: The text that will be pasted (for preview notification).
        """
        config = self.config
        needs_confirmation = config.paste_require_confirmation
        delay = config.paste_delay_seconds

        # Fast path: no delay, no confirmation → paste immediately
        if not needs_confirmation and delay <= 0:
            return True

        self._set_state(AppState.AWAITING_PASTE)

        # Reset events
        self._paste_confirm_event.clear()
        self._paste_cancel_event.clear()

        # Register Enter (confirm) and Escape (cancel) hotkeys
        enter_hook = register_key_press("enter", lambda _: self._on_paste_confirm(), suppress=False)
        self._hotkey_manager.register_cancel(self._on_paste_cancel)

        try:
            # Show preview notification
            preview = summary[:150].replace("\n", " ")
            if len(summary) > 150:
                preview += "..."

            if needs_confirmation:
                self._tray_manager.notify(
                    APP_NAME,
                    f"Enter = paste, Escape = cancel\n{preview}",
                )
                timeout = config.paste_confirmation_timeout
                logger.info(
                    "Awaiting paste confirmation (timeout=%.0fs).", timeout
                )

                # Wait for Enter or Escape or timeout
                elapsed = 0.0
                while elapsed < timeout:
                    if self._paste_confirm_event.is_set():
                        return True
                    if self._paste_cancel_event.is_set():
                        self._tray_manager.notify(APP_NAME, "Paste cancelled.")
                        self._play_audio_cue(play_cancel_cue)
                        return False
                    # Beep each second as countdown feedback
                    if config.audio_cues_enabled and elapsed > 0 and elapsed % 1.0 < 0.1:
                        try:
                            play_beep(
                                PASTE_COUNTDOWN_BEEP_FREQ,
                                PASTE_COUNTDOWN_BEEP_DURATION_MS,
                            )
                        except Exception:
                            pass
                    time.sleep(0.1)
                    elapsed += 0.1

                # Timeout reached
                logger.info("Paste confirmation timed out after %.0fs.", timeout)
                self._tray_manager.notify(APP_NAME, "Paste timed out (cancelled).")
                self._play_audio_cue(play_cancel_cue)
                return False

            else:
                # Delay mode (no confirmation needed)
                self._tray_manager.notify(
                    APP_NAME,
                    f"Pasting in {delay:.0f}s... (Escape = cancel)\n{preview}",
                )
                logger.info("Paste delayed by %.1fs.", delay)

                elapsed = 0.0
                while elapsed < delay:
                    if self._paste_cancel_event.is_set():
                        self._tray_manager.notify(APP_NAME, "Paste cancelled.")
                        self._play_audio_cue(play_cancel_cue)
                        return False
                    time.sleep(0.1)
                    elapsed += 0.1

                return True

        finally:
            # Always clean up hotkey hooks
            unregister_key_hook(enter_hook)
            self._hotkey_manager.unregister_cancel()

    def _run_pipeline(self, audio_data: bytes, clip_backup: str | None = None) -> None:
        """Execute the STT, summarization, and paste pipeline in a worker thread.

        Includes clipboard backup/restore for clipboard preservation (US-0.2.5).
        All errors are caught and reported via toast notifications.
        Supports pipeline queueing: if queued audio exists after this pipeline
        completes, it is processed before returning to IDLE.

        Args:
            audio_data: WAV audio bytes to transcribe and paste.
            clip_backup: Pre-existing clipboard backup (used by queued pipeline
                to share the original backup). If None, backs up now.
        """
        # Backup clipboard before we overwrite it (US-0.2.5)
        owns_clipboard = clip_backup is None
        if owns_clipboard:
            clip_backup = clipboard_backup()

        try:
            # Step 1: Transcribe -- update tray tooltip for progress feedback
            self._tray_manager.set_processing_step("Transcribing...")
            transcript = self._stt.transcribe(
                audio_data, language=self.config.transcription_language
            )

            if not transcript or not transcript.strip():
                logger.info("Empty transcript. Nothing to paste.")
                self._tray_manager.notify(APP_NAME, "No speech detected.")
                self._set_state(AppState.IDLE)
                return

            # Step 2: Summarize or Prompt (v0.5: voice prompt mode)
            self._tray_manager.set_processing_step("Summarizing...")
            try:
                if self._active_mode in ("prompt", "tts_ask"):
                    logger.info("Prompt mode: sending transcript as prompt to LLM.")
                    summary = self._summarizer.summarize(
                        transcript, system_prompt=PROMPT_SYSTEM_PROMPT
                    )
                else:
                    summary = self._summarizer.summarize(transcript)
            except SummarizerError as e:
                # Graceful fallback: paste raw transcript instead of failing
                logger.warning(
                    "Summarization failed, falling back to raw transcript: %s", e
                )
                summary = transcript
                self._tray_manager.notify(
                    APP_NAME,
                    "Summarization unavailable \u2014 raw transcript pasted.",
                )

            # Handle empty result (e.g., all filler words removed)
            if not summary or not summary.strip():
                logger.info("Empty result after processing. Nothing to paste.")
                self._tray_manager.notify(APP_NAME, "No speech detected.")
                self._set_state(AppState.IDLE)
                return

            # v1.2: Claude Code mode — send transcript to Claude CLI
            if self._active_mode == "claude_code":
                self._tray_manager.set_processing_step("Asking Claude...")
                try:
                    result = self._claude_code.invoke(transcript)
                    response_text = result.text
                except ClaudeCodeNotFoundError:
                    self._tray_manager.notify(
                        APP_NAME,
                        "Claude Code CLI not found.\n"
                        "Install: npm i -g @anthropic-ai/claude-code",
                    )
                    self._set_state(AppState.IDLE)
                    return
                except ClaudeCodeTimeoutError:
                    self._tray_manager.notify(APP_NAME, "Claude Code timed out.")
                    self._set_state(AppState.IDLE)
                    return
                except ClaudeCodeError as e:
                    self._tray_manager.notify(
                        APP_NAME, f"Claude Code error:\n{e}"
                    )
                    self._set_state(AppState.IDLE)
                    return

                if not response_text or not response_text.strip():
                    self._tray_manager.notify(APP_NAME, "Claude returned empty response.")
                    self._set_state(AppState.IDLE)
                    return

                # Route response based on config
                mode = self.config.claude_code_response_mode
                if mode in ("speak", "both"):
                    # Put answer on clipboard for user access
                    clipboard_restore(response_text)
                    clip_backup = None  # Don't restore old clipboard
                    logger.info("Claude answer placed on clipboard.")
                    if self._tts:
                        try:
                            self._tts_orchestrator.synthesize_for_ask(response_text)
                        except TTSError as e:
                            logger.error("TTS error in Claude Code pipeline: %s", e)
                            self._tray_manager.notify(
                                APP_NAME,
                                f"Answer on clipboard.\nTTS failed: {e}",
                            )
                        finally:
                            self._hotkey_manager.unregister_cancel()
                    else:
                        self._tray_manager.notify(
                            APP_NAME,
                            "Answer on clipboard (TTS not configured).",
                        )
                        self._set_state(AppState.IDLE)
                    if mode == "speak":
                        return
                    # "both" falls through to paste below

                if mode in ("paste", "both"):
                    summary = response_text
                    # Falls through to the normal paste flow below
                else:
                    return  # speak-only, already handled

            # v0.6: TTS Ask mode — speak the answer instead of pasting
            elif self._active_mode == "tts_ask" and self._tts:
                # Put answer on clipboard (silently, no paste) using ctypes API
                clipboard_restore(summary)
                logger.info("AI answer placed on clipboard.")

                # Prevent the finally block from overwriting the AI answer
                clip_backup = None

                try:
                    self._tts_orchestrator.synthesize_for_ask(summary)
                except TTSError as e:
                    logger.error("TTS error in Ask+TTS pipeline: %s", e)
                    self._tray_manager.notify(
                        APP_NAME,
                        f"Could not read answer aloud.\n"
                        f"Answer copied to clipboard.\n{e}",
                    )
                finally:
                    self._hotkey_manager.unregister_cancel()
                return

            # Step 3: Confirm/delay, then paste (normal flow)
            if not self._wait_before_paste(summary):
                # User cancelled or timed out — do NOT paste
                return

            self._set_state(AppState.PASTING)
            _paste_shortcut = self._get_effective_paste_shortcut()
            success = paste_text(summary, paste_shortcut=_paste_shortcut)

            if success:
                # v0.9: Auto-Enter after paste (e.g. execute command in terminal)
                if self.config.paste_auto_enter:
                    time.sleep(0.05)
                    send_key("enter")
                    logger.info("Pipeline complete. Text pasted + Enter pressed.")
                else:
                    logger.info("Pipeline complete. Text pasted successfully.")
            else:
                logger.warning("Pipeline complete but paste may have failed.")

        except STTError as e:
            logger.error("STT pipeline error: %s", e)
            self._show_error(f"Transcription error:\n{e}")

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
            # Brief delay to ensure paste has completed before restoring
            time.sleep(0.1)

            # Check for queued audio before returning to IDLE
            queued = self._queued_audio
            self._queued_audio = None

            if queued is not None:
                logger.info(
                    "Processing queued audio (%d bytes)...", len(queued)
                )
                # Process the queued audio, passing the original clipboard
                # backup so it is only restored after the LAST pipeline.
                self._set_state(AppState.PROCESSING)
                self._run_pipeline(queued, clip_backup=clip_backup)
            else:
                # No queued audio — normal cleanup
                clipboard_restore(clip_backup)
                # Restore default recorder (without silence detection)
                self._recorder = AudioRecorder(
                    on_auto_stop=self._on_auto_stop,
                    device=self.config.audio_device_index,
                )
                self._set_state(AppState.IDLE)

    def _shutdown(self) -> None:
        """Clean shutdown of all components."""
        logger.info("Shutting down %s...", APP_NAME)
        self._shutdown_event.set()

        # Stop TTS playback if active (v0.6)
        if self._audio_player.is_playing:
            self._audio_player.stop()

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

        # v0.7: Unload local TTS model to free memory
        if self._tts is not None and hasattr(self._tts, "unload_model"):
            try:
                self._tts.unload_model()
                logger.info("Local TTS model unloaded.")
            except Exception as e:
                logger.warning("Error unloading local TTS model: %s", e)

        # v0.9: Stop Hands-Free mode
        if self._handsfree_active:
            self._stop_handsfree()

        # v0.9: Stop API server
        self._stop_api_server()

        # Unregister hotkeys
        self._hotkey_manager.unregister()

        # v1.3: Stop evdev monitor if running (Wayland)
        # SEC-083: Also clean up UInput virtual keyboard device
        if sys.platform == "linux":
            try:
                from evdev_hotkey import stop_monitor, cleanup_uinput
                stop_monitor()
                cleanup_uinput()
            except ImportError:
                pass

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
            "Starting %s v%s (hotkey=%s, stt=%s, summarization=%s, tts=%s, audio_cues=%s)",
            APP_NAME,
            APP_VERSION,
            self.config.hotkey,
            self.config.stt_backend,
            "on" if self.config.summarization_enabled else "off",
            "on" if self.config.tts_enabled else "off",
            "on" if self.config.audio_cues_enabled else "off",
        )

        # v1.3: On Wayland, check evdev permissions before registering hotkeys
        if (
            sys.platform == "linux"
            and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        ):
            try:
                from evdev_hotkey import check_evdev_permissions
                ok, msg = check_evdev_permissions()
                if ok:
                    logger.info("evdev permission check: %s", msg)
                else:
                    logger.error("evdev permission check failed: %s", msg)
                    raise RuntimeError(msg)
            except ImportError:
                logger.error("evdev library not installed (required for Wayland).")
                raise RuntimeError(
                    "The 'evdev' Python library is required for global hotkeys "
                    "on Wayland sessions but is not installed.\n\n"
                    "Install it with:\n"
                    "  pip install evdev"
                )

        # Register hotkey (keyboard library runs its own listener thread).
        # On some Windows configurations, the keyboard library requires
        # Administrator privileges. If registration fails, we raise so
        # that main() can show a message box to the user.
        try:
            self._hotkey_manager.register(self._on_hotkey)
        except Exception as exc:
            logger.exception("Failed to register hotkey.")
            if (
                sys.platform == "linux"
                and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            ):
                raise RuntimeError(
                    f"Could not register the hotkey ({self.config.hotkey}).\n\n"
                    f"On Wayland, VoicePaste uses evdev to read keyboard input.\n"
                    f"Ensure the 'evdev' library is installed and your user is "
                    f"in the 'input' group:\n"
                    f"  sudo usermod -aG input $USER\n"
                    f"Then log out and back in.\n\n"
                    f"Technical detail: {exc}"
                ) from exc
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

        # v0.6: Register TTS hotkeys (non-fatal if registration fails)
        if self.config.tts_enabled:
            try:
                self._hotkey_manager.register_tts(self._on_tts_hotkey)
            except Exception as exc:
                logger.warning(
                    "Failed to register TTS hotkey '%s': %s",
                    self.config.tts_hotkey, exc,
                )
            try:
                self._hotkey_manager.register_tts_ask(self._on_tts_ask_hotkey)
            except Exception as exc:
                logger.warning(
                    "Failed to register TTS Ask hotkey '%s': %s",
                    self.config.tts_ask_hotkey, exc,
                )

        # v1.2: Register Claude Code hotkey (non-fatal if registration fails)
        if self.config.claude_code_enabled and self._claude_code:
            try:
                self._hotkey_manager.register_claude_code(
                    self._on_claude_code_hotkey
                )
            except Exception as exc:
                logger.warning(
                    "Failed to register Claude Code hotkey '%s': %s",
                    self.config.claude_code_hotkey, exc,
                )

        # v1.3: Register Terminal Mode toggle hotkey (non-fatal)
        try:
            self._hotkey_manager.register_terminal_mode(
                self._toggle_terminal_mode
            )
        except Exception as exc:
            logger.warning(
                "Failed to register Terminal Mode hotkey '%s': %s",
                self.config.terminal_mode_hotkey, exc,
            )

        logger.info(
            "Hotkeys registered: summary='%s', prompt='%s', tts='%s', tts_ask='%s', "
            "claude_code='%s', terminal_mode='%s'. Waiting for user input.",
            self.config.hotkey,
            self.config.prompt_hotkey,
            self.config.tts_hotkey if self.config.tts_enabled else "(disabled)",
            self.config.tts_ask_hotkey if self.config.tts_enabled else "(disabled)",
            self.config.claude_code_hotkey if self.config.claude_code_enabled else "(disabled)",
            self.config.terminal_mode_hotkey,
        )

        # v1.0: Run startup cache eviction on a deferred thread
        if self.config.tts_cache_enabled:
            threading.Thread(
                target=self._tts_cache.evict,
                daemon=True,
                name="cache-evict",
            ).start()

        # v0.9: Start HTTP API server
        self._start_api_server()

        # v0.9: Start Hands-Free mode if enabled
        if self.config.handsfree_enabled:
            self._start_handsfree()

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
        1. Parse --debug/--verbose flags and allocate console if requested.
        2. Set up bootstrap logging.
        3. Acquire single-instance mutex (REQ-S27).
        4. Load and validate configuration.
        5. Reconfigure logging with config settings.
        6. Create and run the application (tray + hotkey).

    Each step that can fail shows a MessageBox before exiting.

    CLI flags:
        --debug: Allocate a console window (for --noconsole builds).
        --verbose: Force log level to DEBUG and enable verbose logging
            for third-party libraries (huggingface_hub, urllib3, requests).
            Useful for diagnosing model download issues.
    """
    # ----------------------------------------------------------------
    # Step 0: --debug/--verbose flags
    # ----------------------------------------------------------------
    verbose_mode = "--verbose" in sys.argv

    if "--debug" in sys.argv or verbose_mode:
        enable_debug_console()

    # ----------------------------------------------------------------
    # Step 1: Bootstrap logging (console + minimal format)
    # ----------------------------------------------------------------
    bootstrap_level = logging.DEBUG if verbose_mode else logging.INFO
    logging.basicConfig(
        level=bootstrap_level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )

    # --verbose: enable DEBUG logging for model download libraries
    if verbose_mode:
        for lib_logger_name in (
            "huggingface_hub",
            "urllib3",
            "requests",
            "filelock",
        ):
            logging.getLogger(lib_logger_name).setLevel(logging.DEBUG)
        logger.info("Verbose mode enabled (--verbose). All loggers set to DEBUG.")

    logger.info("=" * 60)
    logger.info("%s v%s starting up...", APP_NAME, APP_VERSION)
    logger.info("Python %s | frozen=%s", sys.version, getattr(sys, "frozen", False))
    logger.info("Working directory: %s", os.getcwd())
    logger.info("Executable: %s", sys.executable)
    logger.info("Arguments: %s", sys.argv)

    # Log hotkey library version for debugging hotkey issues
    if sys.platform == "win32":
        try:
            import keyboard as _kb_diag
            logger.info("keyboard library version: %s", _kb_diag.version)
        except Exception as e:
            logger.warning("Could not determine keyboard library version: %s", e)
    else:
        _session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
        logger.info("Session type: %s", _session_type)
        if _session_type == "wayland":
            try:
                import evdev as _evdev_diag  # noqa: F401 -- availability check
                try:
                    import importlib.metadata as _meta
                    logger.info("evdev library version: %s", _meta.version("evdev"))
                except Exception:
                    logger.info("evdev: available (version unknown in frozen build)")
            except ImportError:
                logger.debug("evdev not available (Wayland hotkeys will not work)")
        try:
            import pynput as _pynput_diag  # noqa: F401 -- availability check
            try:
                import importlib.metadata as _meta
                logger.info("pynput library version: %s", _meta.version("pynput"))
            except Exception:
                logger.info("pynput: available (version unknown in frozen build)")
        except ImportError:
            logger.debug("pynput not available (X11 hotkeys will not work)")

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
    mutex_handle = acquire_single_instance_lock()
    if mutex_handle is None:
        msg = (
            f"{APP_NAME} is already running.\n\n"
            f"Look for the tray icon in the system tray "
            f"(click the ^ arrow in the taskbar).\n\n"
            f"If the previous instance is stuck, open Task Manager "
            f"and end the 'VoicePaste' process, then try again."
        )
        logger.error("Cannot start: another instance is already running.")
        show_fatal_error(msg)
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
        # --verbose overrides the config.toml log level to DEBUG
        # ------------------------------------------------------------
        setup_logging(config, force_debug=verbose_mode)

        # ------------------------------------------------------------
        # Step 5: Create and run the application
        # ------------------------------------------------------------
        app = VoicePasteApp(config)
        app.run()

    except RuntimeError as exc:
        # VoicePasteApp.run() raises RuntimeError for hotkey failures
        logger.error("Startup error: %s", exc)
        show_fatal_error(str(exc))
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

        show_fatal_error(
            f"{APP_NAME} encountered an unexpected error and must close.\n\n"
            f"Please check the log file for details:\n"
            f"  {log_hint}\n\n"
            f"Error:\n{tb[:500]}"
        )
        sys.exit(1)

    finally:
        # Always release the mutex on shutdown
        if mutex_handle is not None:
            release_single_instance_lock(mutex_handle)


if __name__ == "__main__":
    main()

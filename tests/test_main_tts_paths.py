"""Tests for TTS, hands-free, and uncovered main.py paths.

Targets main.py coverage gaps:
- TTS clipboard readout flow (_on_tts_hotkey, _run_tts_pipeline)
- TTS Ask mode (_on_tts_ask_hotkey, tts_ask pipeline in _run_pipeline)
- Language change from tray menu (_on_language_changed)
- Pipeline queueing (_start_queued_recording, _stop_queued_recording)
- Graceful summarization fallback (SummarizerError -> raw transcript)
- Processing progress feedback (set_processing_step calls)
- Settings hot-reload (_on_settings_saved for TTS, cache, export, etc.)
- TTS cache replay, export pipeline, _get_tts_cache_key, _get_tts_voice_label
- Shutdown path coverage
"""

import time
import threading
import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call, ANY

from constants import APP_NAME, AppState, TTS_MAX_TEXT_LENGTH, SUPPORTED_LANGUAGES


# ---------------------------------------------------------------------------
# Shared fixture: VoicePasteApp with all external deps mocked and TTS enabled
# ---------------------------------------------------------------------------

@pytest.fixture
def tts_app():
    """Create a fully mocked VoicePasteApp with TTS enabled."""
    mock_stt_instance = MagicMock()
    with patch("main.AudioRecorder") as MockRecorder, \
         patch("main.create_stt_backend", return_value=mock_stt_instance), \
         patch("main.CloudLLMSummarizer"), \
         patch("main.PassthroughSummarizer"), \
         patch("main.HotkeyManager") as MockHotkey, \
         patch("main.TrayManager") as MockTray, \
         patch("main.clipboard_backup") as MockClipBackup, \
         patch("main.clipboard_restore") as MockClipRestore, \
         patch("main.paste_text") as MockPaste, \
         patch("main.play_recording_start_cue") as MockStartCue, \
         patch("main.play_recording_stop_cue") as MockStopCue, \
         patch("main.play_cancel_cue") as MockCancelCue, \
         patch("main.play_error_cue") as MockErrorCue, \
         patch("main.create_tts_backend") as MockTTSFactory, \
         patch("main.AudioPlayer") as MockAudioPlayer, \
         patch("main.TTSAudioCache") as MockTTSCache, \
         patch("main.TTSAudioExporter") as MockExporter:

        from config import AppConfig
        from main import VoicePasteApp

        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b"fake-mp3-audio"
        MockTTSFactory.return_value = mock_tts

        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="DEBUG",
            summarization_enabled=True,
            audio_cues_enabled=True,
            tts_enabled=True,
            tts_provider="elevenlabs",
            elevenlabs_api_key="el-test-key",
        )

        app = VoicePasteApp(config)

        # Configure mock recorder
        app._recorder.start.return_value = True
        app._recorder.stop.return_value = b"fake-wav-data"
        app._recorder.is_recording = False
        type(app._recorder).is_recording = PropertyMock(return_value=False)

        # Configure mock STT
        app._stt.transcribe.return_value = "Das ist ein Test."

        # Configure mock summarizer
        app._summarizer.summarize.return_value = "Das ist ein Test."

        # Configure mock paste/clipboard
        MockPaste.return_value = True
        MockClipBackup.return_value = "previous clipboard"

        # Configure mock TTS cache
        app._tts_cache.get.return_value = None  # cache miss by default
        app._tts_cache.list_entries.return_value = []

        # Configure mock audio player
        app._audio_player.is_playing = False
        type(app._audio_player).is_playing = PropertyMock(return_value=False)

        # Store mock references for assertions
        app._mocks = {
            "clip_backup": MockClipBackup,
            "clip_restore": MockClipRestore,
            "paste": MockPaste,
            "start_cue": MockStartCue,
            "stop_cue": MockStopCue,
            "cancel_cue": MockCancelCue,
            "error_cue": MockErrorCue,
            "tts_factory": MockTTSFactory,
            "audio_player_cls": MockAudioPlayer,
            "tts_cache_cls": MockTTSCache,
            "exporter_cls": MockExporter,
        }

        yield app


# ---------------------------------------------------------------------------
# TTS clipboard readout: _on_tts_hotkey
# ---------------------------------------------------------------------------

class TestOnTtsHotkey:
    """Test the TTS clipboard readout hotkey handler."""

    def test_tts_hotkey_reads_clipboard_and_starts_pipeline(self, tts_app):
        """TTS hotkey in IDLE with text on clipboard should start TTS pipeline."""
        tts_app._mocks["clip_backup"].return_value = "Hallo Welt"

        tts_app._on_tts_hotkey()

        # Should transition to PROCESSING (pipeline thread runs async)
        # Give the worker thread time to start and complete
        time.sleep(0.5)

        # TTS synthesize should have been called with the clipboard text
        tts_app._tts.synthesize.assert_called_once_with("Hallo Welt")

    def test_tts_hotkey_stops_playback_when_speaking(self, tts_app):
        """TTS hotkey during SPEAKING should stop playback."""
        tts_app._set_state(AppState.SPEAKING)

        tts_app._on_tts_hotkey()

        tts_app._audio_player.stop.assert_called_once()

    def test_tts_hotkey_ignored_during_recording(self, tts_app):
        """TTS hotkey during RECORDING should be ignored."""
        tts_app._set_state(AppState.RECORDING)

        tts_app._on_tts_hotkey()

        tts_app._tts.synthesize.assert_not_called()

    def test_tts_hotkey_ignored_during_processing(self, tts_app):
        """TTS hotkey during PROCESSING should be ignored."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._on_tts_hotkey()

        tts_app._tts.synthesize.assert_not_called()

    def test_tts_hotkey_error_when_tts_not_configured(self, tts_app):
        """TTS hotkey with no TTS backend should show error."""
        tts_app._tts = None

        tts_app._on_tts_hotkey()

        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "not configured" in notify_text.lower() or "TTS" in notify_text

    def test_tts_hotkey_empty_clipboard_shows_notification(self, tts_app):
        """TTS hotkey with empty clipboard should notify user."""
        tts_app._mocks["clip_backup"].return_value = ""

        tts_app._on_tts_hotkey()

        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "empty" in notify_text.lower()

    def test_tts_hotkey_none_clipboard_shows_notification(self, tts_app):
        """TTS hotkey with None clipboard should notify user."""
        tts_app._mocks["clip_backup"].return_value = None

        tts_app._on_tts_hotkey()

        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "empty" in notify_text.lower()

    def test_tts_hotkey_whitespace_only_clipboard_shows_notification(self, tts_app):
        """TTS hotkey with whitespace-only clipboard should notify user."""
        tts_app._mocks["clip_backup"].return_value = "   \n\t  "

        tts_app._on_tts_hotkey()

        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "empty" in notify_text.lower()

    def test_tts_hotkey_text_too_long_shows_notification(self, tts_app):
        """TTS hotkey with text exceeding max length should notify user."""
        long_text = "A" * (TTS_MAX_TEXT_LENGTH + 1)
        tts_app._mocks["clip_backup"].return_value = long_text

        tts_app._on_tts_hotkey()

        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "too long" in notify_text.lower() or "long" in notify_text.lower()


# ---------------------------------------------------------------------------
# TTS pipeline: _run_tts_pipeline
# ---------------------------------------------------------------------------

class TestRunTtsPipeline:
    """Test the TTS synthesis and playback pipeline."""

    def test_tts_pipeline_synthesizes_and_plays(self, tts_app):
        """TTS pipeline should synthesize text and play audio."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_pipeline("Hallo Welt")

        tts_app._tts.synthesize.assert_called_once_with("Hallo Welt")
        tts_app._audio_player.play.assert_called_once_with(b"fake-mp3-audio")
        assert tts_app.state == AppState.IDLE

    def test_tts_pipeline_uses_cache_hit(self, tts_app):
        """TTS pipeline should use cached audio on cache hit."""
        tts_app._tts_cache.get.return_value = b"cached-audio"
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_pipeline("Hallo Welt")

        # Synthesize should NOT be called on cache hit
        tts_app._tts.synthesize.assert_not_called()
        tts_app._audio_player.play.assert_called_once_with(b"cached-audio")

    def test_tts_pipeline_stores_in_cache_on_miss(self, tts_app):
        """TTS pipeline should store synthesized audio in cache on miss."""
        tts_app._tts_cache.get.return_value = None
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_pipeline("Hallo Welt")

        tts_app._tts_cache.put.assert_called_once()
        call_args = tts_app._tts_cache.put.call_args
        assert call_args[0][1] == b"fake-mp3-audio"

    def test_tts_pipeline_exports_audio(self, tts_app):
        """TTS pipeline should attempt to export audio."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_pipeline("Hallo Welt")

        tts_app._tts_exporter.export.assert_called_once()

    def test_tts_pipeline_registers_cancel_hotkey(self, tts_app):
        """TTS pipeline should register Escape cancel during playback."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_pipeline("Test text")

        tts_app._hotkey_manager.register_cancel.assert_called()
        tts_app._hotkey_manager.unregister_cancel.assert_called()

    def test_tts_pipeline_returns_to_idle_on_tts_error(self, tts_app):
        """TTS pipeline should return to IDLE on TTSError."""
        from tts import TTSError
        tts_app._tts.synthesize.side_effect = TTSError("API down")
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_pipeline("Test text")

        assert tts_app.state == AppState.IDLE
        tts_app._tray_manager.notify.assert_called()

    def test_tts_pipeline_returns_to_idle_on_unexpected_error(self, tts_app):
        """TTS pipeline should return to IDLE on unexpected error."""
        tts_app._tts.synthesize.side_effect = RuntimeError("Unexpected")
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_pipeline("Test text")

        assert tts_app.state == AppState.IDLE

    def test_tts_pipeline_transitions_to_speaking(self, tts_app):
        """TTS pipeline should transition to SPEAKING before playback."""
        states_seen = []

        def capture_state(state):
            states_seen.append(state)

        tts_app._tray_manager.update_state.side_effect = capture_state
        tts_app._set_state(AppState.PROCESSING)
        states_seen.clear()  # Clear the PROCESSING update

        tts_app._run_tts_pipeline("Test text")

        assert AppState.SPEAKING in states_seen
        assert AppState.IDLE in states_seen


# ---------------------------------------------------------------------------
# TTS Ask mode: _on_tts_ask_hotkey + tts_ask pipeline branch
# ---------------------------------------------------------------------------

class TestOnTtsAskHotkey:
    """Test the TTS Ask AI + readout hotkey handler."""

    def test_tts_ask_starts_recording_from_idle(self, tts_app):
        """TTS Ask hotkey in IDLE should start recording in tts_ask mode."""
        tts_app._on_tts_ask_hotkey()

        assert tts_app.state == AppState.RECORDING
        assert tts_app._active_mode == "tts_ask"

    def test_tts_ask_stops_recording_when_recording(self, tts_app):
        """TTS Ask hotkey in RECORDING should stop and process."""
        tts_app._on_tts_ask_hotkey()  # IDLE -> RECORDING
        assert tts_app.state == AppState.RECORDING

        tts_app._on_tts_ask_hotkey()  # RECORDING -> PROCESSING
        # Give pipeline thread time to run
        time.sleep(0.8)

        assert tts_app.state == AppState.IDLE

    def test_tts_ask_stops_playback_when_speaking(self, tts_app):
        """TTS Ask hotkey during SPEAKING should stop playback."""
        tts_app._set_state(AppState.SPEAKING)

        tts_app._on_tts_ask_hotkey()

        tts_app._audio_player.stop.assert_called_once()

    def test_tts_ask_ignored_during_processing(self, tts_app):
        """TTS Ask hotkey during PROCESSING should be ignored."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._on_tts_ask_hotkey()

        # Should still be PROCESSING (no change)
        assert tts_app.state == AppState.PROCESSING

    def test_tts_ask_error_when_tts_not_configured(self, tts_app):
        """TTS Ask hotkey with no TTS backend should show error."""
        tts_app._tts = None

        tts_app._on_tts_ask_hotkey()

        tts_app._tray_manager.notify.assert_called()
        assert tts_app.state == AppState.IDLE


class TestTtsAskPipeline:
    """Test the TTS Ask pipeline branch in _run_pipeline."""

    def test_tts_ask_pipeline_speaks_answer(self, tts_app):
        """TTS Ask pipeline should synthesize and play the LLM answer."""
        tts_app._active_mode = "tts_ask"

        tts_app._on_hotkey()  # Use the main hotkey since mode is set
        # Actually, use _on_tts_ask_hotkey for proper mode setup
        tts_app._set_state(AppState.IDLE)
        tts_app._active_mode = "tts_ask"

        # Start recording manually
        tts_app._start_recording()
        assert tts_app.state == AppState.RECORDING

        # Stop recording and process
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        # Should have called synthesize with the summarizer output
        tts_app._tts.synthesize.assert_called()
        assert tts_app.state == AppState.IDLE

    def test_tts_ask_pipeline_places_answer_on_clipboard(self, tts_app):
        """TTS Ask pipeline should place the answer on the clipboard."""
        tts_app._active_mode = "tts_ask"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        # Should have called clipboard_restore with the LLM answer
        tts_app._mocks["clip_restore"].assert_any_call("Das ist ein Test.")

    def test_tts_ask_pipeline_does_not_paste(self, tts_app):
        """TTS Ask pipeline should NOT auto-paste the answer."""
        tts_app._active_mode = "tts_ask"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        # paste_text should NOT be called in tts_ask mode
        tts_app._mocks["paste"].assert_not_called()

    def test_tts_ask_pipeline_uses_prompt_system_prompt(self, tts_app):
        """TTS Ask pipeline should use the PROMPT_SYSTEM_PROMPT for LLM."""
        from constants import PROMPT_SYSTEM_PROMPT
        tts_app._active_mode = "tts_ask"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        tts_app._summarizer.summarize.assert_called_once_with(
            "Das ist ein Test.",
            system_prompt=PROMPT_SYSTEM_PROMPT,
        )

    def test_tts_ask_pipeline_handles_tts_error(self, tts_app):
        """TTS Ask pipeline should handle TTSError gracefully."""
        from tts import TTSError
        tts_app._active_mode = "tts_ask"
        tts_app._tts.synthesize.side_effect = TTSError("API limit reached")

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        # Should still return to IDLE
        assert tts_app.state == AppState.IDLE
        # Should notify user about the TTS error
        notify_calls = tts_app._tray_manager.notify.call_args_list
        notify_text = " ".join(str(c) for c in notify_calls)
        assert "clipboard" in notify_text.lower() or "aloud" in notify_text.lower()

    def test_tts_ask_pipeline_caches_audio(self, tts_app):
        """TTS Ask pipeline should cache the synthesized audio."""
        tts_app._active_mode = "tts_ask"
        tts_app._tts_cache.get.return_value = None

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        tts_app._tts_cache.put.assert_called()

    def test_tts_ask_pipeline_uses_cache_hit(self, tts_app):
        """TTS Ask pipeline should use cached audio on cache hit."""
        tts_app._active_mode = "tts_ask"
        tts_app._tts_cache.get.return_value = b"cached-ask-audio"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        # Synthesize should NOT be called
        tts_app._tts.synthesize.assert_not_called()
        # Should play the cached audio
        tts_app._audio_player.play.assert_called_once_with(b"cached-ask-audio")


# ---------------------------------------------------------------------------
# Language change from tray: _on_language_changed
# ---------------------------------------------------------------------------

class TestOnLanguageChanged:
    """Test the language change handler."""

    def test_language_change_updates_config(self, tts_app):
        """Changing language should update config.transcription_language."""
        tts_app.config.transcription_language = "de"

        with patch.object(tts_app.config, "save_to_toml"):
            tts_app._on_language_changed("en")

        assert tts_app.config.transcription_language == "en"

    def test_language_change_saves_to_toml(self, tts_app):
        """Changing language should save config to disk."""
        tts_app.config.transcription_language = "de"

        with patch.object(tts_app.config, "save_to_toml") as mock_save:
            tts_app._on_language_changed("en")

        mock_save.assert_called_once()

    def test_language_change_notifies_user(self, tts_app):
        """Changing language should show a notification with the language name."""
        tts_app.config.transcription_language = "de"

        with patch.object(tts_app.config, "save_to_toml"):
            tts_app._on_language_changed("en")

        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "English" in notify_text

    def test_language_change_same_language_is_noop(self, tts_app):
        """Changing to the same language should do nothing."""
        tts_app.config.transcription_language = "de"

        with patch.object(tts_app.config, "save_to_toml") as mock_save:
            tts_app._on_language_changed("de")

        mock_save.assert_not_called()
        tts_app._tray_manager.notify.assert_not_called()

    def test_language_change_to_auto(self, tts_app):
        """Changing to 'auto' should work and show Auto-detect."""
        tts_app.config.transcription_language = "de"

        with patch.object(tts_app.config, "save_to_toml"):
            tts_app._on_language_changed("auto")

        assert tts_app.config.transcription_language == "auto"
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "Auto" in notify_text


# ---------------------------------------------------------------------------
# Pipeline queueing: _start_queued_recording, _stop_queued_recording
# ---------------------------------------------------------------------------

class TestPipelineQueueing:
    """Test pipeline queueing (recording while processing)."""

    def test_queued_recording_starts_during_processing(self, tts_app):
        """Pressing hotkey during PROCESSING should start a queued recording."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recording_during_processing = False

        tts_app._start_queued_recording()

        assert tts_app._recording_during_processing is True
        tts_app._recorder.start.assert_called()

    def test_queued_recording_plays_start_cue(self, tts_app):
        """Starting a queued recording should play the start cue."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._start_queued_recording()

        tts_app._mocks["start_cue"].assert_called()

    def test_queued_recording_registers_cancel(self, tts_app):
        """Starting a queued recording should register Escape cancel."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._start_queued_recording()

        tts_app._hotkey_manager.register_cancel.assert_called()

    def test_queued_recording_updates_tray_tooltip(self, tts_app):
        """Starting a queued recording should update the tray tooltip."""
        tts_app._set_state(AppState.PROCESSING)

        tts_app._start_queued_recording()

        tts_app._tray_manager.set_processing_step.assert_called_with(
            "Recording (queued)..."
        )

    def test_queued_recording_stop_saves_audio(self, tts_app):
        """Stopping a queued recording should save audio to _queued_audio."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recording_during_processing = True
        tts_app._recorder.stop.return_value = b"queued-wav-data"

        tts_app._stop_queued_recording()

        assert tts_app._queued_audio == b"queued-wav-data"
        assert tts_app._recording_during_processing is False

    def test_queued_recording_stop_plays_stop_cue(self, tts_app):
        """Stopping a queued recording should play the stop cue."""
        tts_app._recording_during_processing = True

        tts_app._stop_queued_recording()

        tts_app._mocks["stop_cue"].assert_called()

    def test_queued_recording_stop_no_audio_sets_none(self, tts_app):
        """Stopping a queued recording with no audio should set _queued_audio to None."""
        tts_app._recording_during_processing = True
        tts_app._recorder.stop.return_value = None

        tts_app._stop_queued_recording()

        assert tts_app._queued_audio is None

    def test_queued_recording_failed_start_plays_error(self, tts_app):
        """Failed queued recording start should play error cue."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recorder.start.return_value = False

        tts_app._start_queued_recording()

        tts_app._mocks["error_cue"].assert_called()
        assert tts_app._recording_during_processing is False

    def test_no_stt_prevents_queued_recording(self, tts_app):
        """Queued recording should not start when STT backend is None."""
        tts_app._stt = None
        tts_app._set_state(AppState.PROCESSING)

        tts_app._start_queued_recording()

        tts_app._recorder.start.assert_not_called()
        tts_app._mocks["error_cue"].assert_called()

    def test_hotkey_during_processing_starts_queue(self, tts_app):
        """Pressing main hotkey during PROCESSING should start queued recording."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recording_during_processing = False

        tts_app._on_hotkey()

        assert tts_app._recording_during_processing is True

    def test_hotkey_during_queued_recording_stops_it(self, tts_app):
        """Pressing main hotkey while queued recording is active should stop it."""
        tts_app._set_state(AppState.RECORDING)
        tts_app._recording_during_processing = True

        tts_app._on_hotkey()

        assert tts_app._recording_during_processing is False

    def test_hotkey_when_queue_full_plays_error(self, tts_app):
        """Pressing hotkey when queue is full should play error cue."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recording_during_processing = True

        tts_app._on_hotkey()

        tts_app._mocks["error_cue"].assert_called()

    def test_prompt_hotkey_during_processing_starts_queue(self, tts_app):
        """Pressing prompt hotkey during PROCESSING should start queued recording."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recording_during_processing = False

        tts_app._on_prompt_hotkey()

        assert tts_app._recording_during_processing is True
        assert tts_app._queued_mode == "prompt"


# ---------------------------------------------------------------------------
# Graceful summarization fallback
# ---------------------------------------------------------------------------

class TestSummarizationFallback:
    """Test graceful fallback when summarization fails."""

    def test_summarizer_error_falls_back_to_raw_transcript(self, tts_app):
        """SummarizerError should fall back to raw transcript."""
        from summarizer import SummarizerError
        tts_app._summarizer.summarize.side_effect = SummarizerError("Timeout")
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        # paste_text should have been called with the raw transcript
        tts_app._mocks["paste"].assert_called_once_with(
            "Das ist ein Test.", paste_shortcut="auto"
        )
        assert tts_app.state == AppState.IDLE

    def test_summarizer_error_shows_fallback_notification(self, tts_app):
        """SummarizerError should show a notification about the fallback."""
        from summarizer import SummarizerError
        tts_app._summarizer.summarize.side_effect = SummarizerError("Timeout")
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        notify_calls = tts_app._tray_manager.notify.call_args_list
        notify_text = " ".join(str(c) for c in notify_calls)
        assert "raw transcript" in notify_text.lower() or "unavailable" in notify_text.lower()


# ---------------------------------------------------------------------------
# Processing progress feedback: set_processing_step
# ---------------------------------------------------------------------------

class TestProcessingProgress:
    """Test that processing steps update the tray tooltip."""

    def test_pipeline_sets_transcribing_step(self, tts_app):
        """Pipeline should set 'Transcribing...' progress step."""
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        calls = tts_app._tray_manager.set_processing_step.call_args_list
        step_args = [str(c) for c in calls]
        assert any("Transcribing" in s for s in step_args)

    def test_pipeline_sets_summarizing_step(self, tts_app):
        """Pipeline should set 'Summarizing...' progress step."""
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        calls = tts_app._tray_manager.set_processing_step.call_args_list
        step_args = [str(c) for c in calls]
        assert any("Summarizing" in s for s in step_args)


# ---------------------------------------------------------------------------
# Settings hot-reload: _on_settings_saved
# ---------------------------------------------------------------------------

class TestSettingsHotReload:
    """Test the settings hot-reload handler for various field groups."""

    def test_tts_keys_rebuild_tts_backend(self, tts_app):
        """Changing TTS-related keys should rebuild the TTS backend."""
        original_tts = tts_app._tts

        with patch.object(tts_app, "_rebuild_tts") as mock_rebuild:
            tts_app._on_settings_saved({"tts_enabled": True})

        mock_rebuild.assert_called_once()

    def test_tts_enabled_registers_hotkeys(self, tts_app):
        """Enabling TTS should register TTS hotkeys."""
        tts_app.config.tts_enabled = True
        tts_app._hotkey_manager._tts_registered = False
        tts_app._hotkey_manager._tts_ask_registered = False

        with patch.object(tts_app, "_rebuild_tts"):
            tts_app._on_settings_saved({"tts_enabled": True})

        tts_app._hotkey_manager.register_tts.assert_called()
        tts_app._hotkey_manager.register_tts_ask.assert_called()

    def test_tts_disabled_unregisters_hotkeys(self, tts_app):
        """Disabling TTS should unregister TTS hotkeys."""
        tts_app.config.tts_enabled = False

        with patch.object(tts_app, "_rebuild_tts"):
            tts_app._on_settings_saved({"tts_enabled": False})

        tts_app._hotkey_manager.unregister_tts.assert_called()

    def test_stt_keys_rebuild_stt_backend(self, tts_app):
        """Changing STT-related keys should rebuild the STT backend."""
        with patch("main.create_stt_backend", return_value=MagicMock()) as mock_factory:
            tts_app._on_settings_saved({"stt_backend": "local"})

        mock_factory.assert_called_once_with(tts_app.config)

    def test_stt_rebuild_unloads_old_local_model(self, tts_app):
        """Rebuilding STT should unload the previous local model if present."""
        old_stt = MagicMock()
        old_stt.unload_model = MagicMock()
        tts_app._stt = old_stt

        with patch("main.create_stt_backend", return_value=MagicMock()):
            tts_app._on_settings_saved({"stt_backend": "cloud"})

        old_stt.unload_model.assert_called_once()

    def test_summarizer_keys_rebuild_summarizer(self, tts_app):
        """Changing summarizer-related keys should rebuild the summarizer."""
        with patch.object(tts_app, "_rebuild_summarizer") as mock_rebuild:
            tts_app._on_settings_saved({"summarization_model": "gpt-4o"})

        mock_rebuild.assert_called_once()

    def test_cache_keys_rebuild_cache(self, tts_app):
        """Changing TTS cache keys should rebuild the cache."""
        with patch.object(tts_app, "_create_tts_cache", return_value=MagicMock()) as mock_create:
            tts_app._on_settings_saved({"tts_cache_enabled": False})

        mock_create.assert_called_once()

    def test_export_keys_rebuild_exporter(self, tts_app):
        """Changing TTS export keys should rebuild the exporter."""
        with patch.object(tts_app, "_create_tts_exporter", return_value=MagicMock()) as mock_create:
            tts_app._on_settings_saved({"tts_export_enabled": True})

        mock_create.assert_called_once()

    def test_api_keys_restart_api_server(self, tts_app):
        """Changing API keys should restart the API server."""
        with patch.object(tts_app, "_stop_api_server") as mock_stop, \
             patch.object(tts_app, "_start_api_server") as mock_start:
            tts_app._on_settings_saved({"api_enabled": True})

        mock_stop.assert_called_once()
        mock_start.assert_called_once()

    def test_handsfree_keys_restart_handsfree(self, tts_app):
        """Changing handsfree keys should restart Hands-Free mode if active."""
        tts_app._handsfree_active = True
        tts_app.config.handsfree_enabled = True

        with patch.object(tts_app, "_stop_handsfree") as mock_stop, \
             patch.object(tts_app, "_start_handsfree") as mock_start:
            tts_app._on_settings_saved({"wake_phrase": "Hey Computer"})

        mock_stop.assert_called_once()
        mock_start.assert_called_once()

    def test_settings_saved_notifies_user(self, tts_app):
        """Settings save should notify the user via toast."""
        tts_app._on_settings_saved({"audio_cues_enabled": False})

        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "saved" in notify_text.lower() or "Settings" in notify_text

    def test_unrelated_keys_do_not_rebuild(self, tts_app):
        """Changing an unrelated key should not rebuild any backend."""
        with patch.object(tts_app, "_rebuild_tts") as mock_tts, \
             patch.object(tts_app, "_rebuild_summarizer") as mock_sum, \
             patch("main.create_stt_backend") as mock_stt:
            tts_app._on_settings_saved({"log_level": "DEBUG"})

        mock_tts.assert_not_called()
        mock_sum.assert_not_called()
        mock_stt.assert_not_called()

    def test_tts_provider_change_unloads_old_model(self, tts_app):
        """Changing TTS provider should unload the previous local TTS model."""
        old_tts = MagicMock()
        old_tts.unload_model = MagicMock()
        tts_app._tts = old_tts

        with patch.object(tts_app, "_rebuild_tts"):
            tts_app._on_settings_saved({"tts_provider": "piper"})

        old_tts.unload_model.assert_called_once()


# ---------------------------------------------------------------------------
# TTS cache replay
# ---------------------------------------------------------------------------

class TestTtsCacheReplay:
    """Test the TTS cache replay functionality."""

    def test_replay_starts_playback_from_idle(self, tts_app):
        """Replay should start playback when IDLE and entry exists."""
        tts_app._tts_cache.replay.return_value = b"cached-audio-bytes"

        # Track state transitions to verify SPEAKING was set
        states_seen = []
        original_update = tts_app._tray_manager.update_state
        def capture_state(state):
            states_seen.append(state)
        tts_app._tray_manager.update_state.side_effect = capture_state

        result = tts_app.replay_tts_entry("abc123")

        assert result is True
        # SPEAKING was set (worker thread may have already returned to IDLE)
        assert AppState.SPEAKING in states_seen

    def test_replay_returns_false_when_not_idle(self, tts_app):
        """Replay should return False when not in IDLE state."""
        tts_app._set_state(AppState.PROCESSING)

        result = tts_app.replay_tts_entry("abc123")

        assert result is False

    def test_replay_returns_false_for_missing_entry(self, tts_app):
        """Replay should return False when cache entry is not found."""
        tts_app._tts_cache.replay.return_value = None

        result = tts_app.replay_tts_entry("nonexistent")

        assert result is False
        assert tts_app.state == AppState.IDLE

    def test_replay_plays_cached_audio(self, tts_app):
        """Replay should play the cached audio bytes."""
        tts_app._tts_cache.replay.return_value = b"replay-audio"

        tts_app.replay_tts_entry("entry1")
        time.sleep(0.5)

        tts_app._audio_player.play.assert_called_once_with(b"replay-audio")
        assert tts_app.state == AppState.IDLE


# ---------------------------------------------------------------------------
# TTS cache key and voice label helpers
# ---------------------------------------------------------------------------

class TestTtsCacheKeyHelpers:
    """Test _get_tts_cache_key and _get_tts_voice_label."""

    def test_cache_key_elevenlabs(self, tts_app):
        """ElevenLabs provider should use voice_id in cache key."""
        tts_app.config.tts_provider = "elevenlabs"
        tts_app.config.tts_voice_id = "voice123"

        key = tts_app._get_tts_cache_key("Hello")

        assert key.provider == "elevenlabs"
        assert key.voice_id == "voice123"
        assert key.text == "Hello"

    def test_cache_key_piper(self, tts_app):
        """Piper provider should use tts_local_voice in cache key."""
        tts_app.config.tts_provider = "piper"
        tts_app.config.tts_local_voice = "de_DE-thorsten-medium"

        key = tts_app._get_tts_cache_key("Hello")

        assert key.provider == "piper"
        assert key.voice_id == "de_DE-thorsten-medium"
        assert key.text == "Hello"

    def test_voice_label_piper(self, tts_app):
        """Piper voice label should return the voice name."""
        tts_app.config.tts_provider = "piper"
        tts_app.config.tts_local_voice = "de_DE-thorsten-medium"

        label = tts_app._get_tts_voice_label()

        assert label == "de_DE-thorsten-medium"

    def test_voice_label_elevenlabs_known_preset(self, tts_app):
        """ElevenLabs voice label should return preset name for known voices."""
        tts_app.config.tts_provider = "elevenlabs"
        tts_app.config.tts_voice_id = "pFZP5JQG7iQjIQuC4Bku"  # Lily

        label = tts_app._get_tts_voice_label()

        assert label == "Lily"

    def test_voice_label_elevenlabs_unknown_voice(self, tts_app):
        """ElevenLabs voice label should return voice_id for unknown voices."""
        tts_app.config.tts_provider = "elevenlabs"
        tts_app.config.tts_voice_id = "unknown-custom-voice-id"

        label = tts_app._get_tts_voice_label()

        assert label == "unknown-custom-voice-id"


# ---------------------------------------------------------------------------
# TTS export pipeline (_run_tts_export_pipeline)
# ---------------------------------------------------------------------------

class TestRunTtsExportPipeline:
    """Test the TTS export-only pipeline (API-driven)."""

    def test_export_pipeline_synthesizes_and_exports(self, tts_app):
        """Export pipeline should synthesize and export without playing."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._tts_exporter.export.return_value = "/path/to/exported.mp3"

        tts_app._run_tts_export_pipeline("Export this text")

        tts_app._tts.synthesize.assert_called_once_with("Export this text")
        tts_app._tts_exporter.export.assert_called_once()
        # Should NOT play the audio
        tts_app._audio_player.play.assert_not_called()
        assert tts_app.state == AppState.IDLE

    def test_export_pipeline_uses_cache(self, tts_app):
        """Export pipeline should use cached audio on hit."""
        tts_app._tts_cache.get.return_value = b"cached-export-audio"
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_export_pipeline("Cached text")

        tts_app._tts.synthesize.assert_not_called()
        tts_app._tts_exporter.export.assert_called_once()

    def test_export_pipeline_handles_tts_error(self, tts_app):
        """Export pipeline should handle TTSError gracefully."""
        from tts import TTSError
        tts_app._tts.synthesize.side_effect = TTSError("Quota exceeded")
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_export_pipeline("Error text")

        assert tts_app.state == AppState.IDLE
        tts_app._tray_manager.notify.assert_called()

    def test_export_pipeline_handles_unexpected_error(self, tts_app):
        """Export pipeline should handle unexpected errors gracefully."""
        tts_app._tts.synthesize.side_effect = ValueError("Unexpected")
        tts_app._set_state(AppState.PROCESSING)

        tts_app._run_tts_export_pipeline("Error text")

        assert tts_app.state == AppState.IDLE

    def test_export_pipeline_passes_filename_hint(self, tts_app):
        """Export pipeline should pass filename_hint to exporter."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._tts_exporter.export.return_value = "/path/to/custom.mp3"

        tts_app._run_tts_export_pipeline("Some text", filename_hint="custom_name")

        call_kwargs = tts_app._tts_exporter.export.call_args
        assert "filename_hint" in str(call_kwargs)


# ---------------------------------------------------------------------------
# Cancel handler (_on_cancel) for TTS / queued / awaiting states
# ---------------------------------------------------------------------------

class TestOnCancelExtended:
    """Test cancel handler for SPEAKING, queued recording, and AWAITING_PASTE."""

    def test_cancel_during_speaking_stops_playback(self, tts_app):
        """Cancel during SPEAKING should stop audio playback."""
        tts_app._set_state(AppState.SPEAKING)

        tts_app._on_cancel()

        tts_app._audio_player.stop.assert_called_once()

    def test_cancel_during_awaiting_paste_sets_event(self, tts_app):
        """Cancel during AWAITING_PASTE should set the cancel event."""
        tts_app._set_state(AppState.AWAITING_PASTE)

        tts_app._on_cancel()

        assert tts_app._paste_cancel_event.is_set()

    def test_cancel_queued_recording_during_processing(self, tts_app):
        """Cancel during PROCESSING with queued recording should cancel recording."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recording_during_processing = True

        tts_app._on_cancel()

        tts_app._recorder.stop.assert_called()
        assert tts_app._recording_during_processing is False
        assert tts_app._queued_audio is None

    def test_cancel_outside_valid_states_is_ignored(self, tts_app):
        """Cancel during PASTING should be ignored."""
        tts_app._set_state(AppState.PASTING)

        tts_app._on_cancel()

        tts_app._recorder.stop.assert_not_called()
        tts_app._audio_player.stop.assert_not_called()


# ---------------------------------------------------------------------------
# Shutdown coverage
# ---------------------------------------------------------------------------

class TestShutdown:
    """Test the shutdown path for TTS-related cleanup."""

    def test_shutdown_stops_tts_playback(self, tts_app):
        """Shutdown should stop TTS playback if active."""
        type(tts_app._audio_player).is_playing = PropertyMock(return_value=True)

        tts_app._shutdown()

        tts_app._audio_player.stop.assert_called()

    def test_shutdown_unloads_local_tts_model(self, tts_app):
        """Shutdown should unload local TTS model if present."""
        mock_tts = MagicMock()
        mock_tts.unload_model = MagicMock()
        tts_app._tts = mock_tts

        tts_app._shutdown()

        mock_tts.unload_model.assert_called_once()

    def test_shutdown_unloads_local_stt_model(self, tts_app):
        """Shutdown should unload local STT model if present."""
        mock_stt = MagicMock()
        mock_stt.unload_model = MagicMock()
        tts_app._stt = mock_stt

        tts_app._shutdown()

        mock_stt.unload_model.assert_called_once()

    def test_shutdown_stops_handsfree(self, tts_app):
        """Shutdown should stop Hands-Free mode if active."""
        tts_app._handsfree_active = True

        with patch.object(tts_app, "_stop_handsfree") as mock_stop:
            tts_app._shutdown()

        mock_stop.assert_called_once()

    def test_shutdown_stops_api_server(self, tts_app):
        """Shutdown should stop the API server."""
        with patch.object(tts_app, "_stop_api_server") as mock_stop:
            tts_app._shutdown()

        mock_stop.assert_called_once()

    def test_shutdown_unregisters_hotkeys(self, tts_app):
        """Shutdown should unregister all hotkeys."""
        tts_app._shutdown()

        tts_app._hotkey_manager.unregister.assert_called_once()

    def test_shutdown_stops_tray(self, tts_app):
        """Shutdown should stop the tray manager."""
        tts_app._shutdown()

        tts_app._tray_manager.stop.assert_called_once()

    def test_shutdown_stops_active_recording(self, tts_app):
        """Shutdown should stop recording if active."""
        type(tts_app._recorder).is_recording = PropertyMock(return_value=True)

        tts_app._shutdown()

        tts_app._recorder.stop.assert_called()

    def test_shutdown_handles_model_unload_errors(self, tts_app):
        """Shutdown should handle errors during model unload gracefully."""
        mock_stt = MagicMock()
        mock_stt.unload_model.side_effect = RuntimeError("Unload failed")
        tts_app._stt = mock_stt

        # Should not raise
        tts_app._shutdown()


# ---------------------------------------------------------------------------
# _rebuild_tts and _rebuild_summarizer
# ---------------------------------------------------------------------------

class TestRebuildBackends:
    """Test backend rebuild methods."""

    def test_rebuild_tts_disabled(self, tts_app):
        """Rebuilding TTS when disabled should set _tts to None."""
        tts_app.config.tts_enabled = False

        tts_app._rebuild_tts()

        assert tts_app._tts is None

    def test_rebuild_tts_enabled(self, tts_app):
        """Rebuilding TTS when enabled should create a new backend."""
        tts_app.config.tts_enabled = True
        tts_app.config.elevenlabs_api_key = "el-key"

        with patch("main.create_tts_backend", return_value=MagicMock()) as mock_factory:
            tts_app._rebuild_tts()

        mock_factory.assert_called_once()
        assert tts_app._tts is not None

    def test_rebuild_summarizer_disabled(self, tts_app):
        """Rebuilding summarizer when disabled should use PassthroughSummarizer."""
        tts_app.config.summarization_enabled = False

        with patch("main.PassthroughSummarizer") as MockPassthrough:
            tts_app._rebuild_summarizer()

        MockPassthrough.assert_called_once()

    def test_rebuild_summarizer_no_api_key(self, tts_app):
        """Rebuilding summarizer without API key should use PassthroughSummarizer."""
        tts_app.config.summarization_enabled = True
        tts_app.config.openai_api_key = ""
        tts_app.config.summarization_provider = "openai"

        with patch("main.PassthroughSummarizer") as MockPassthrough:
            tts_app._rebuild_summarizer()

        MockPassthrough.assert_called_once()

    def test_rebuild_summarizer_with_api_key(self, tts_app):
        """Rebuilding summarizer with API key should use CloudLLMSummarizer."""
        tts_app.config.summarization_enabled = True
        tts_app.config.openai_api_key = "sk-test"
        tts_app.config.summarization_provider = "openai"

        with patch("main.CloudLLMSummarizer") as MockCloud:
            tts_app._rebuild_summarizer()

        MockCloud.assert_called_once()


# ---------------------------------------------------------------------------
# Auto-stop handling
# ---------------------------------------------------------------------------

class TestAutoStop:
    """Test auto-stop handler (_on_auto_stop)."""

    def test_auto_stop_during_recording(self, tts_app):
        """Auto-stop during RECORDING should trigger processing."""
        tts_app._on_hotkey()  # IDLE -> RECORDING
        assert tts_app.state == AppState.RECORDING

        tts_app._on_auto_stop()
        time.sleep(0.8)

        assert tts_app.state == AppState.IDLE

    def test_auto_stop_during_queued_recording(self, tts_app):
        """Auto-stop during queued recording should stop the queued recording."""
        tts_app._set_state(AppState.PROCESSING)
        tts_app._recording_during_processing = True
        tts_app._recorder.stop.return_value = b"queued-data"

        tts_app._on_auto_stop()

        assert tts_app._recording_during_processing is False
        tts_app._tray_manager.notify.assert_called()

    def test_auto_stop_outside_recording_is_ignored(self, tts_app):
        """Auto-stop outside RECORDING state should be ignored."""
        tts_app._set_state(AppState.IDLE)

        tts_app._on_auto_stop()

        assert tts_app.state == AppState.IDLE


# ---------------------------------------------------------------------------
# Pipeline error paths (ImportError, RuntimeError, MemoryError)
# ---------------------------------------------------------------------------

class TestPipelineErrorPaths:
    """Test error handling paths in _run_pipeline."""

    def test_import_error_in_pipeline(self, tts_app):
        """ImportError during pipeline should show appropriate error."""
        tts_app._stt.transcribe.side_effect = ImportError("No module named ctranslate2")
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        assert tts_app.state == AppState.IDLE
        tts_app._tray_manager.notify.assert_called()

    def test_dll_import_error_shows_vcredist_hint(self, tts_app):
        """ImportError mentioning DLL should hint at VC++ Redistributable."""
        tts_app._stt.transcribe.side_effect = ImportError(
            "DLL load failed: The specified module could not be found."
        )
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        notify_calls = tts_app._tray_manager.notify.call_args_list
        notify_text = " ".join(str(c) for c in notify_calls)
        assert "Visual C++" in notify_text or "DLL" in notify_text or "library" in notify_text.lower()

    def test_runtime_error_cuda_hint(self, tts_app):
        """RuntimeError mentioning CUDA should hint at CPU fallback."""
        tts_app._stt.transcribe.side_effect = RuntimeError("CUDA error: device-side assert")
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        notify_calls = tts_app._tray_manager.notify.call_args_list
        notify_text = " ".join(str(c) for c in notify_calls)
        assert "GPU" in notify_text or "cpu" in notify_text.lower()

    def test_runtime_error_out_of_memory_hint(self, tts_app):
        """RuntimeError mentioning out of memory should provide guidance."""
        tts_app._stt.transcribe.side_effect = RuntimeError("out of memory")
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        notify_calls = tts_app._tray_manager.notify.call_args_list
        notify_text = " ".join(str(c) for c in notify_calls)
        assert "memory" in notify_text.lower()

    def test_memory_error_in_pipeline(self, tts_app):
        """MemoryError during pipeline should show guidance."""
        tts_app._stt.transcribe.side_effect = MemoryError()
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        assert tts_app.state == AppState.IDLE
        notify_calls = tts_app._tray_manager.notify.call_args_list
        notify_text = " ".join(str(c) for c in notify_calls)
        assert "memory" in notify_text.lower()

    def test_generic_runtime_error_shows_log_hint(self, tts_app):
        """Generic RuntimeError should suggest checking the log file."""
        tts_app._stt.transcribe.side_effect = RuntimeError("Something weird happened")
        tts_app._active_mode = "summary"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(0.8)

        notify_calls = tts_app._tray_manager.notify.call_args_list
        notify_text = " ".join(str(c) for c in notify_calls)
        assert "log" in notify_text.lower()


# ---------------------------------------------------------------------------
# Prompt mode (_on_prompt_hotkey)
# ---------------------------------------------------------------------------

class TestPromptHotkey:
    """Test the Voice Prompt hotkey handler."""

    def test_prompt_hotkey_starts_recording_in_prompt_mode(self, tts_app):
        """Prompt hotkey in IDLE should start recording in prompt mode."""
        tts_app._on_prompt_hotkey()

        assert tts_app.state == AppState.RECORDING
        assert tts_app._active_mode == "prompt"

    def test_prompt_hotkey_stops_recording(self, tts_app):
        """Prompt hotkey in RECORDING should stop and process."""
        tts_app._on_prompt_hotkey()  # IDLE -> RECORDING
        tts_app._on_prompt_hotkey()  # RECORDING -> PROCESSING

        time.sleep(0.8)

        assert tts_app.state == AppState.IDLE

    def test_prompt_hotkey_ignored_during_pasting(self, tts_app):
        """Prompt hotkey during PASTING should be ignored."""
        tts_app._set_state(AppState.PASTING)

        tts_app._on_prompt_hotkey()

        assert tts_app.state == AppState.PASTING


# ---------------------------------------------------------------------------
# Export audio helper (_export_tts_audio)
# ---------------------------------------------------------------------------

class TestExportTtsAudio:
    """Test the fire-and-forget export helper."""

    def test_export_calls_exporter(self, tts_app):
        """Export helper should call exporter with text and audio data."""
        tts_app._tts_exporter.export.return_value = "/path/to/file.mp3"

        tts_app._export_tts_audio("Hello", b"audio-data")

        tts_app._tts_exporter.export.assert_called_once_with("Hello", b"audio-data")

    def test_export_handles_error_gracefully(self, tts_app):
        """Export helper should not raise on exporter errors."""
        tts_app._tts_exporter.export.side_effect = OSError("Disk full")

        # Should not raise
        tts_app._export_tts_audio("Hello", b"audio-data")

    def test_export_handles_none_result(self, tts_app):
        """Export helper should handle None result (export disabled)."""
        tts_app._tts_exporter.export.return_value = None

        # Should not raise
        tts_app._export_tts_audio("Hello", b"audio-data")


# ---------------------------------------------------------------------------
# _start_recording edge cases
# ---------------------------------------------------------------------------

class TestStartRecordingEdgeCases:
    """Test _start_recording with no STT backend."""

    def test_no_stt_cloud_shows_api_key_error(self, tts_app):
        """No STT with cloud backend should show API key error."""
        tts_app._stt = None
        tts_app.config.stt_backend = "cloud"

        tts_app._start_recording()

        assert tts_app.state == AppState.IDLE
        tts_app._tray_manager.notify.assert_called()
        notify_text = str(tts_app._tray_manager.notify.call_args)
        assert "API key" in notify_text or "key" in notify_text.lower()

    def test_no_stt_local_shows_specific_error(self, tts_app):
        """No STT with local backend should show specific guidance."""
        tts_app._stt = None
        tts_app.config.stt_backend = "local"

        tts_app._start_recording()

        assert tts_app.state == AppState.IDLE
        tts_app._tray_manager.notify.assert_called()


# ---------------------------------------------------------------------------
# Queued pipeline processing in _run_pipeline finally block
# ---------------------------------------------------------------------------

class TestQueuedPipelineProcessing:
    """Test that queued audio is processed after the current pipeline."""

    def test_queued_audio_processed_after_current_pipeline(self, tts_app):
        """Queued audio should be processed after the current pipeline completes."""
        tts_app._active_mode = "summary"
        tts_app._queued_audio = b"queued-wav-data"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(1.5)  # Extra time for recursive pipeline

        # STT should have been called at least twice (once for each pipeline)
        assert tts_app._stt.transcribe.call_count >= 2
        assert tts_app.state == AppState.IDLE

    def test_queued_audio_cleared_after_processing(self, tts_app):
        """Queued audio should be cleared after it is processed."""
        tts_app._active_mode = "summary"
        tts_app._queued_audio = b"queued-wav-data"

        tts_app._start_recording()
        tts_app._stop_recording_and_process()
        time.sleep(1.5)

        assert tts_app._queued_audio is None

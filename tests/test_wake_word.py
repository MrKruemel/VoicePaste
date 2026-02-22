"""Tests for the wake word detection module.

Validates:
- Wake phrase text normalization and matching logic
- Match modes: "contains", "startswith", "fuzzy"
- WakeWordDetector initialization and configuration
- Start/stop lifecycle (threading)
- Callback invocation on wake phrase detection
- Cooldown behavior (prevent rapid re-triggering)
- Audio buffer management and memory scrubbing
- Model loading (lazy-load with error handling)
- Transcription pipeline (_transcribe_buffer)
- _check_buffer integration (transcribe -> match -> callback)
- _listen_loop audio processing and VAD logic
- Error handling in all paths
"""

import threading
import time

import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call

from wake_word import (
    WakeWordDetector,
    _normalize_text,
    _fuzzy_match,
    _clear_buffer,
    _FRAME_SIZE,
    _MIN_SPEECH_SECONDS,
    _SPEECH_END_GRACE_SECONDS,
    _DEFAULT_ENERGY_THRESHOLD,
)
from constants import (
    DEFAULT_HANDSFREE_BUFFER_SECONDS,
    DEFAULT_HANDSFREE_COOLDOWN_SECONDS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_WAKE_PHRASE,
    DEFAULT_WAKE_PHRASE_MATCH_MODE,
)


# ---------------------------------------------------------------------------
# Pure function tests: _normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    """Test the _normalize_text helper function."""

    def test_lowercase(self):
        """Text should be lowercased."""
        assert _normalize_text("Hello Cloud") == "hello cloud"

    def test_strip_whitespace(self):
        """Leading/trailing whitespace should be stripped."""
        assert _normalize_text("  hello  ") == "hello"

    def test_collapse_internal_whitespace(self):
        """Multiple internal spaces should collapse to one."""
        assert _normalize_text("hello   cloud") == "hello cloud"

    def test_remove_punctuation(self):
        """Punctuation should be removed."""
        assert _normalize_text("Hello, Cloud!") == "hello cloud"
        assert _normalize_text("it's a test.") == "its a test"

    def test_remove_special_characters(self):
        """Special characters like quotes and dashes should be removed."""
        # Quotes and dashes are non-word/non-space chars; after removal and
        # whitespace collapse the result has no extra spaces.
        assert _normalize_text('"hello" - cloud') == "hello cloud"

    def test_empty_string(self):
        """Empty string should return empty string."""
        assert _normalize_text("") == ""

    def test_only_punctuation(self):
        """String with only punctuation should return empty string."""
        assert _normalize_text("...!!!???") == ""

    def test_german_umlauts_preserved(self):
        """German umlauts should be preserved (they are word characters)."""
        assert _normalize_text("Hallo Wolke") == "hallo wolke"
        # Umlauts are \w characters in Python regex
        assert _normalize_text("ubung") == "ubung"

    def test_mixed_whitespace_and_punctuation(self):
        """Combination of extra whitespace and punctuation."""
        result = _normalize_text("  Hello,  Cloud!  How are you?  ")
        assert result == "hello cloud how are you"

    def test_tabs_and_newlines(self):
        """Tabs and newlines count as whitespace and should collapse."""
        assert _normalize_text("hello\t\ncloud") == "hello cloud"


# ---------------------------------------------------------------------------
# Pure function tests: _fuzzy_match
# ---------------------------------------------------------------------------


class TestFuzzyMatch:
    """Test the _fuzzy_match helper function."""

    def test_exact_match(self):
        """All tokens present should match (100% >= 70%)."""
        assert _fuzzy_match(["hello", "cloud"], ["hello", "cloud"]) is True

    def test_all_tokens_in_longer_transcript(self):
        """All phrase tokens in a longer transcript should match."""
        assert _fuzzy_match(
            ["hello", "cloud"],
            ["say", "hello", "cloud", "please"],
        ) is True

    def test_70_percent_threshold_met(self):
        """70% of tokens present should match."""
        # 3 tokens, need 3*0.7 = 2.1, so need 3 matches for 3-token phrase
        # Actually: 2/3 = 0.667 < 0.7, so 2 of 3 is NOT enough
        assert _fuzzy_match(
            ["hello", "dear", "cloud"],
            ["hello", "cloud"],
        ) is False
        # But 7 of 10 = 0.7, exactly at threshold
        phrase = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        transcript = ["a", "b", "c", "d", "e", "f", "g"]
        assert _fuzzy_match(phrase, transcript) is True

    def test_below_70_percent_threshold(self):
        """Below 70% should not match."""
        # 1 of 3 = 0.33 < 0.7
        assert _fuzzy_match(
            ["hello", "dear", "cloud"],
            ["hello"],
        ) is False

    def test_no_tokens_match(self):
        """No matching tokens should not match."""
        assert _fuzzy_match(["hello", "cloud"], ["goodbye", "sun"]) is False

    def test_empty_phrase_tokens(self):
        """Empty phrase tokens list should return False (avoid division by zero)."""
        assert _fuzzy_match([], ["hello", "cloud"]) is False

    def test_empty_transcript_tokens(self):
        """Empty transcript tokens should return False."""
        assert _fuzzy_match(["hello", "cloud"], []) is False

    def test_both_empty(self):
        """Both empty should return False (empty phrase)."""
        assert _fuzzy_match([], []) is False

    def test_single_token_phrase(self):
        """Single token phrase: 1/1 = 100% >= 70%."""
        assert _fuzzy_match(["hello"], ["hello", "world"]) is True

    def test_single_token_phrase_no_match(self):
        """Single token phrase with no match: 0/1 = 0% < 70%."""
        assert _fuzzy_match(["hello"], ["world"]) is False

    def test_duplicate_tokens_in_transcript(self):
        """Duplicate tokens in transcript should not count extra."""
        # "hello" appears once in phrase, once (or more) in transcript
        # sum counts 1 for each phrase token found in transcript
        assert _fuzzy_match(["hello", "cloud"], ["hello", "hello"]) is False

    def test_exactly_at_boundary(self):
        """Test the exact 70% boundary: 7/10 = 0.70."""
        phrase = list("abcdefghij")  # 10 single-char tokens
        transcript = list("abcdefg")  # 7 matching
        assert _fuzzy_match(phrase, transcript) is True

        # 6/10 = 0.60 < 0.70
        transcript_below = list("abcdef")
        assert _fuzzy_match(phrase, transcript_below) is False


# ---------------------------------------------------------------------------
# Pure function tests: _clear_buffer
# ---------------------------------------------------------------------------


class TestClearBuffer:
    """Test the _clear_buffer memory scrubbing function."""

    def test_clears_list(self):
        """Buffer list should be empty after clearing."""
        buf = [np.ones(10, dtype=np.int16), np.ones(20, dtype=np.int16)]
        _clear_buffer(buf)
        assert len(buf) == 0

    def test_zeros_arrays_before_clearing(self):
        """Arrays should be zeroed before removal (REQ-S10 memory scrub)."""
        arr1 = np.ones(10, dtype=np.int16)
        arr2 = np.full(5, 500, dtype=np.int16)
        buf = [arr1, arr2]
        _clear_buffer(buf)
        # After clearing, the original arrays should have been zeroed
        # (buf is now empty, but we still hold references to the arrays)
        assert np.all(arr1 == 0)
        assert np.all(arr2 == 0)

    def test_empty_buffer(self):
        """Clearing an empty buffer should not raise."""
        buf = []
        _clear_buffer(buf)
        assert len(buf) == 0


# ---------------------------------------------------------------------------
# WakeWordDetector initialization
# ---------------------------------------------------------------------------


class TestWakeWordDetectorInit:
    """Test WakeWordDetector constructor and default configuration."""

    def test_default_configuration(self):
        """Detector should use constants defaults when no args given."""
        detector = WakeWordDetector()
        assert detector._wake_phrase == DEFAULT_WAKE_PHRASE
        assert detector._energy_threshold == _DEFAULT_ENERGY_THRESHOLD
        assert detector._buffer_duration == DEFAULT_HANDSFREE_BUFFER_SECONDS
        assert detector._cooldown == DEFAULT_HANDSFREE_COOLDOWN_SECONDS
        assert detector._match_mode == DEFAULT_WAKE_PHRASE_MATCH_MODE
        assert detector._language is None
        assert detector._on_detected is None
        assert detector._should_listen is None
        assert detector._model is None
        assert detector._running is False
        assert detector._thread is None

    def test_custom_configuration(self):
        """Detector should accept all custom parameters."""
        callback = MagicMock()
        listen_fn = MagicMock(return_value=True)
        detector = WakeWordDetector(
            wake_phrase="Hey Computer",
            on_detected=callback,
            energy_threshold=500.0,
            buffer_duration_seconds=5.0,
            cooldown_seconds=2.0,
            match_mode="fuzzy",
            language="de",
            should_listen=listen_fn,
        )
        assert detector._wake_phrase == "Hey Computer"
        assert detector._wake_phrase_normalized == "hey computer"
        assert detector._wake_phrase_tokens == ["hey", "computer"]
        assert detector._on_detected is callback
        assert detector._energy_threshold == 500.0
        assert detector._buffer_duration == 5.0
        assert detector._cooldown == 2.0
        assert detector._match_mode == "fuzzy"
        assert detector._language == "de"
        assert detector._should_listen is listen_fn

    def test_wake_phrase_normalization_at_init(self):
        """Wake phrase should be normalized and tokenized at init time."""
        detector = WakeWordDetector(wake_phrase="  Hello,  Cloud!  ")
        assert detector._wake_phrase_normalized == "hello cloud"
        assert detector._wake_phrase_tokens == ["hello", "cloud"]

    def test_is_running_initially_false(self):
        """is_running property should be False before start()."""
        detector = WakeWordDetector()
        assert detector.is_running is False


# ---------------------------------------------------------------------------
# Wake phrase matching (_matches_wake_phrase)
# ---------------------------------------------------------------------------


class TestMatchesWakePhrase:
    """Test _matches_wake_phrase with all match modes."""

    def _make_detector(self, mode="contains", phrase="Hello Cloud"):
        return WakeWordDetector(wake_phrase=phrase, match_mode=mode)

    # --- contains mode ---

    def test_contains_exact_match(self):
        """Exact phrase should match in contains mode."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("Hello Cloud") is True

    def test_contains_phrase_within_longer_text(self):
        """Phrase embedded in longer text should match."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("I said Hello Cloud just now") is True

    def test_contains_case_insensitive(self):
        """Matching should be case-insensitive."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("HELLO CLOUD") is True

    def test_contains_with_punctuation(self):
        """Punctuation in transcript should not prevent matching."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("Hello, Cloud!") is True

    def test_contains_no_match(self):
        """Non-matching text should return False."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("Goodbye Sun") is False

    def test_contains_partial_match(self):
        """Only partial phrase should not match."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("Hello") is False

    def test_contains_empty_transcript(self):
        """Empty transcript should return False."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("") is False

    def test_contains_none_like_empty(self):
        """Whitespace-only transcript normalizes to empty, should return False."""
        d = self._make_detector("contains")
        assert d._matches_wake_phrase("   ") is False

    # --- startswith mode ---

    def test_startswith_exact_match(self):
        """Exact phrase at start should match."""
        d = self._make_detector("startswith")
        assert d._matches_wake_phrase("Hello Cloud") is True

    def test_startswith_phrase_at_beginning(self):
        """Phrase at beginning of longer text should match."""
        d = self._make_detector("startswith")
        assert d._matches_wake_phrase("Hello Cloud, please do something") is True

    def test_startswith_phrase_not_at_beginning(self):
        """Phrase not at beginning should not match in startswith mode."""
        d = self._make_detector("startswith")
        assert d._matches_wake_phrase("I said Hello Cloud") is False

    def test_startswith_empty_transcript(self):
        """Empty transcript should return False."""
        d = self._make_detector("startswith")
        assert d._matches_wake_phrase("") is False

    # --- fuzzy mode ---

    def test_fuzzy_all_tokens_present(self):
        """All wake phrase tokens present should match."""
        d = self._make_detector("fuzzy")
        assert d._matches_wake_phrase("Hello Cloud") is True

    def test_fuzzy_tokens_in_longer_text(self):
        """All tokens scattered in longer text should match."""
        d = self._make_detector("fuzzy")
        assert d._matches_wake_phrase("Well hello there, Cloud!") is True

    def test_fuzzy_below_threshold(self):
        """With only 1 of 2 tokens (50% < 70%), should not match."""
        d = self._make_detector("fuzzy")
        assert d._matches_wake_phrase("Hello World") is False

    def test_fuzzy_empty_transcript(self):
        """Empty transcript should return False."""
        d = self._make_detector("fuzzy")
        assert d._matches_wake_phrase("") is False

    def test_fuzzy_with_many_tokens(self):
        """Fuzzy match with a longer wake phrase (need 70% of tokens)."""
        d = self._make_detector("fuzzy", phrase="one two three four five")
        # 4 of 5 = 80% >= 70%
        assert d._matches_wake_phrase("one two three four") is True
        # 3 of 5 = 60% < 70%
        assert d._matches_wake_phrase("one two three") is False

    # --- default/fallback mode ---

    def test_unknown_mode_falls_back_to_contains(self):
        """Unknown match mode should fall back to contains behavior."""
        d = self._make_detector("unknown_mode")
        assert d._matches_wake_phrase("Hello Cloud") is True
        assert d._matches_wake_phrase("Goodbye") is False


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


class TestModelLoading:
    """Test _load_model lazy loading behavior.

    Note: _load_model() imports faster_whisper and model_manager locally
    (inside the method body), so they are NOT module-level attributes of
    wake_word. We must use patch.dict("sys.modules", ...) to intercept
    those local imports.
    """

    def test_load_model_success(self):
        """Model should be loaded via faster-whisper WhisperModel."""
        mock_model_instance = MagicMock()
        mock_whisper_cls = MagicMock(return_value=mock_model_instance)
        mock_fw = MagicMock(WhisperModel=mock_whisper_cls)
        mock_mm = MagicMock()
        mock_mm.get_model_path.return_value = "/fake/path/tiny"

        detector = WakeWordDetector()

        with patch.dict("sys.modules", {
            "faster_whisper": mock_fw,
            "model_manager": mock_mm,
        }):
            result = detector._load_model()

        assert result is True
        assert detector._model is mock_model_instance

    def test_load_model_already_loaded(self):
        """If model is already loaded, _load_model returns True immediately."""
        detector = WakeWordDetector()
        existing_model = MagicMock()
        detector._model = existing_model

        result = detector._load_model()

        assert result is True
        # Model should not have been replaced
        assert detector._model is existing_model

    def test_load_model_import_error(self):
        """If faster-whisper is not installed, should return False."""
        detector = WakeWordDetector()

        with patch("builtins.__import__", side_effect=_selective_import_error("faster_whisper")):
            result = detector._load_model()

        assert result is False
        assert detector._model is None

    def test_load_model_path_none_downloads(self):
        """If get_model_path returns None, should try download_model."""
        mock_whisper_cls = MagicMock()
        mock_fw = MagicMock(WhisperModel=mock_whisper_cls)
        mock_mm = MagicMock()
        mock_mm.get_model_path.return_value = None
        mock_mm.download_model.return_value = "/downloaded/path"

        detector = WakeWordDetector()

        with patch.dict("sys.modules", {
            "faster_whisper": mock_fw,
            "model_manager": mock_mm,
        }):
            result = detector._load_model()

        assert result is True
        mock_mm.download_model.assert_called_once_with("base")

    def test_load_model_download_fails(self):
        """If both get_model_path and download_model return None, should fail."""
        mock_mm = MagicMock()
        mock_mm.get_model_path.return_value = None
        mock_mm.download_model.return_value = None

        detector = WakeWordDetector()

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(),
            "model_manager": mock_mm,
        }):
            result = detector._load_model()

        assert result is False
        assert detector._model is None

    def test_load_model_exception(self):
        """Generic exception during model loading should return False."""
        detector = WakeWordDetector()

        mock_fw = MagicMock()
        mock_fw.WhisperModel.side_effect = RuntimeError("GPU init failed")
        mock_mm = MagicMock()
        mock_mm.get_model_path.return_value = "/fake/path"

        with patch.dict("sys.modules", {
            "faster_whisper": mock_fw,
            "model_manager": mock_mm,
        }):
            result = detector._load_model()

        assert result is False
        assert detector._model is None


def _selective_import_error(blocked_module):
    """Create an __import__ side_effect that only blocks a specific module."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _import(name, *args, **kwargs):
        if name == blocked_module:
            raise ImportError(f"No module named '{blocked_module}'")
        return real_import(name, *args, **kwargs)

    return _import


# ---------------------------------------------------------------------------
# Transcription (_transcribe_buffer)
# ---------------------------------------------------------------------------


class TestTranscribeBuffer:
    """Test _transcribe_buffer method."""

    def test_transcribe_no_model(self):
        """If model is None, should return empty string."""
        detector = WakeWordDetector()
        assert detector._model is None
        audio = np.zeros(1600, dtype=np.int16)
        result = detector._transcribe_buffer(audio)
        assert result == ""

    def test_transcribe_returns_joined_segments(self):
        """Should join all segment texts with spaces."""
        detector = WakeWordDetector(language="de")
        mock_model = MagicMock()

        seg1 = MagicMock()
        seg1.text = "Hello"
        seg2 = MagicMock()
        seg2.text = " Cloud"
        mock_model.transcribe.return_value = (iter([seg1, seg2]), MagicMock())
        detector._model = mock_model

        audio = np.ones(3200, dtype=np.int16) * 1000
        result = detector._transcribe_buffer(audio)

        assert result == "Hello  Cloud"
        # Verify transcribe was called with correct parameters
        call_kwargs = mock_model.transcribe.call_args
        assert call_kwargs.kwargs["beam_size"] == 1
        assert call_kwargs.kwargs["language"] == "de"
        assert call_kwargs.kwargs["vad_filter"] is False
        assert call_kwargs.kwargs["without_timestamps"] is True

    def test_transcribe_converts_int16_to_float32(self):
        """Audio should be converted from int16 to float32 [-1.0, 1.0]."""
        detector = WakeWordDetector()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([]), MagicMock())
        detector._model = mock_model

        # Create audio at max int16 value
        audio = np.full(1600, 32767, dtype=np.int16)
        detector._transcribe_buffer(audio)

        # Check the audio passed to transcribe
        call_args = mock_model.transcribe.call_args[0][0]
        assert call_args.dtype == np.float32
        # 32767 / 32768.0 should be close to 1.0
        assert abs(call_args[0] - (32767 / 32768.0)) < 0.001

    def test_transcribe_empty_segments(self):
        """If transcription returns no segments, should return empty string."""
        detector = WakeWordDetector()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([]), MagicMock())
        detector._model = mock_model

        audio = np.zeros(1600, dtype=np.int16)
        result = detector._transcribe_buffer(audio)
        assert result == ""

    def test_transcribe_exception_returns_empty(self):
        """Exception during transcription should return empty string, not raise."""
        detector = WakeWordDetector()
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("STT crash")
        detector._model = mock_model

        audio = np.zeros(1600, dtype=np.int16)
        result = detector._transcribe_buffer(audio)
        assert result == ""


# ---------------------------------------------------------------------------
# _check_buffer (transcribe -> match -> callback)
# ---------------------------------------------------------------------------


class TestCheckBuffer:
    """Test _check_buffer integration: transcribe, match, callback."""

    def _make_detector_with_model(self, match_mode="contains", phrase="Hello Cloud"):
        callback = MagicMock()
        detector = WakeWordDetector(
            wake_phrase=phrase,
            on_detected=callback,
            match_mode=match_mode,
            cooldown_seconds=0.0,  # No cooldown delay in tests
        )
        mock_model = MagicMock()
        detector._model = mock_model
        # Override _stop_event.wait to be a no-op (avoid cooldown delay)
        detector._stop_event = MagicMock()
        detector._stop_event.wait = MagicMock()
        detector._stop_event.is_set = MagicMock(return_value=False)
        return detector, callback, mock_model

    def test_check_buffer_empty_buffer(self):
        """Empty buffer should return immediately without transcribing."""
        detector, callback, mock_model = self._make_detector_with_model()
        detector._check_buffer([])
        mock_model.transcribe.assert_not_called()
        callback.assert_not_called()

    def test_check_buffer_match_fires_callback(self):
        """When transcript matches wake phrase, callback should fire."""
        detector, callback, mock_model = self._make_detector_with_model()

        seg = MagicMock()
        seg.text = "Hello Cloud"
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())

        buf = [np.ones(1600, dtype=np.int16)]
        detector._check_buffer(buf)

        callback.assert_called_once()

    def test_check_buffer_no_match_no_callback(self):
        """When transcript does not match, callback should not fire."""
        detector, callback, mock_model = self._make_detector_with_model()

        seg = MagicMock()
        seg.text = "something else"
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())

        buf = [np.ones(1600, dtype=np.int16)]
        detector._check_buffer(buf)

        callback.assert_not_called()

    def test_check_buffer_empty_transcript_no_callback(self):
        """Empty transcription should not fire callback."""
        detector, callback, mock_model = self._make_detector_with_model()

        mock_model.transcribe.return_value = (iter([]), MagicMock())

        buf = [np.ones(1600, dtype=np.int16)]
        detector._check_buffer(buf)

        callback.assert_not_called()

    def test_check_buffer_cooldown_triggered(self):
        """After a match, cooldown should be triggered via _stop_event.wait."""
        detector, callback, mock_model = self._make_detector_with_model()
        detector._cooldown = 5.0

        seg = MagicMock()
        seg.text = "Hello Cloud"
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())

        buf = [np.ones(1600, dtype=np.int16)]
        detector._check_buffer(buf)

        detector._stop_event.wait.assert_called_once_with(5.0)

    def test_check_buffer_callback_exception_does_not_propagate(self):
        """Exception in callback should be caught and logged, not propagated."""
        detector, callback, mock_model = self._make_detector_with_model()
        callback.side_effect = RuntimeError("Callback crashed")

        seg = MagicMock()
        seg.text = "Hello Cloud"
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())

        buf = [np.ones(1600, dtype=np.int16)]
        # Should not raise
        detector._check_buffer(buf)
        callback.assert_called_once()

    def test_check_buffer_no_callback_set(self):
        """If on_detected is None, match should not raise."""
        detector = WakeWordDetector(
            wake_phrase="Hello Cloud",
            on_detected=None,
            cooldown_seconds=0.0,
        )
        mock_model = MagicMock()
        detector._model = mock_model
        detector._stop_event = MagicMock()
        detector._stop_event.wait = MagicMock()

        seg = MagicMock()
        seg.text = "Hello Cloud"
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())

        buf = [np.ones(1600, dtype=np.int16)]
        # Should not raise even though no callback
        detector._check_buffer(buf)

    def test_check_buffer_concatenates_multiple_frames(self):
        """Multiple frames in the buffer should be concatenated before transcription."""
        detector, callback, mock_model = self._make_detector_with_model()

        mock_model.transcribe.return_value = (iter([]), MagicMock())

        frame1 = np.ones(1600, dtype=np.int16)
        frame2 = np.ones(800, dtype=np.int16) * 2
        buf = [frame1, frame2]
        detector._check_buffer(buf)

        # The audio passed to transcribe should be the concatenation
        call_args = mock_model.transcribe.call_args[0][0]
        assert len(call_args) == 2400  # 1600 + 800


# ---------------------------------------------------------------------------
# Start / stop lifecycle
# ---------------------------------------------------------------------------


class TestStartStopLifecycle:
    """Test WakeWordDetector start and stop lifecycle."""

    def test_start_when_model_load_fails(self):
        """start() should return False if model cannot be loaded."""
        detector = WakeWordDetector()
        with patch.object(detector, "_load_model", return_value=False):
            result = detector.start()
        assert result is False
        assert detector.is_running is False

    def test_start_success(self):
        """start() should spawn a listener thread and set running=True."""
        detector = WakeWordDetector()
        with patch.object(detector, "_load_model", return_value=True), \
             patch.object(detector, "_listen_loop"):
            result = detector.start()
            # Give thread a moment to start
            time.sleep(0.1)

        assert result is True
        assert detector.is_running is True
        assert detector._thread is not None
        assert detector._thread.daemon is True
        assert detector._thread.name == "wake-word-listener"

        # Cleanup
        detector.stop()

    def test_start_when_already_running(self):
        """start() when already running should return True without spawning a new thread."""
        detector = WakeWordDetector()
        detector._running = True
        original_thread = MagicMock()
        detector._thread = original_thread

        result = detector.start()

        assert result is True
        assert detector._thread is original_thread

    def test_stop_when_running(self):
        """stop() should signal the thread to stop and join it."""
        detector = WakeWordDetector()
        detector._running = True
        mock_thread = MagicMock()
        detector._thread = mock_thread

        detector.stop()

        assert detector._stop_event.is_set()
        mock_thread.join.assert_called_once_with(timeout=3.0)
        assert detector._thread is None
        assert detector.is_running is False

    def test_stop_when_not_running(self):
        """stop() when not running should be a no-op."""
        detector = WakeWordDetector()
        detector._running = False

        # Should not raise
        detector.stop()
        assert detector.is_running is False

    def test_unload_model(self):
        """unload_model() should delete the model and set to None."""
        detector = WakeWordDetector()
        detector._model = MagicMock()

        detector.unload_model()

        assert detector._model is None

    def test_unload_model_when_none(self):
        """unload_model() when model is already None should be a no-op."""
        detector = WakeWordDetector()
        assert detector._model is None

        # Should not raise
        detector.unload_model()
        assert detector._model is None


# ---------------------------------------------------------------------------
# _listen_loop
# ---------------------------------------------------------------------------


class TestListenLoop:
    """Test the _listen_loop audio processing and VAD logic.

    These tests mock sounddevice.InputStream and control the audio frames
    returned by stream.read() to simulate speech patterns.
    """

    def _make_detector_for_loop(self, **kwargs):
        """Create a detector configured for listen loop testing."""
        defaults = dict(
            wake_phrase="Hello Cloud",
            on_detected=MagicMock(),
            energy_threshold=300.0,
            cooldown_seconds=0.0,
            match_mode="contains",
        )
        defaults.update(kwargs)
        detector = WakeWordDetector(**defaults)
        # Pre-set model to avoid needing _load_model
        detector._model = MagicMock()
        return detector

    def _make_silent_frame(self):
        """Create a frame with energy below threshold (silence)."""
        return np.zeros((_FRAME_SIZE, 1), dtype=np.int16)

    def _make_speech_frame(self, amplitude=1000):
        """Create a frame with energy above threshold (speech)."""
        return np.full((_FRAME_SIZE, 1), amplitude, dtype=np.int16)

    @patch("wake_word.sd.InputStream")
    def test_listen_loop_stream_open_failure(self, mock_input_stream_cls):
        """If InputStream fails to open, loop should exit and set running=False."""
        mock_input_stream_cls.side_effect = RuntimeError("No audio device")

        detector = self._make_detector_for_loop()
        detector._running = True

        detector._listen_loop()

        assert detector._running is False

    @patch("wake_word.sd.InputStream")
    def test_listen_loop_stops_on_event(self, mock_input_stream_cls):
        """Loop should exit when _stop_event is set."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        detector = self._make_detector_for_loop()
        detector._stop_event.set()  # Stop immediately

        detector._listen_loop()

        mock_stream.start.assert_called_once()
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()

    @patch("wake_word.sd.InputStream")
    def test_listen_loop_should_listen_false_discards_frames(self, mock_input_stream_cls):
        """When should_listen returns False, frames are drained and discarded."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        call_count = 0

        def controlled_should_listen():
            nonlocal call_count
            call_count += 1
            return False

        detector = self._make_detector_for_loop(should_listen=controlled_should_listen)

        # After a few iterations, stop
        iteration = [0]
        original_is_set = detector._stop_event.is_set

        def stop_after_3():
            iteration[0] += 1
            if iteration[0] > 3:
                return True
            return False

        detector._stop_event.is_set = stop_after_3

        detector._listen_loop()

        # stream.read should have been called (draining), but _check_buffer should not
        assert mock_stream.read.call_count > 0

    @patch("wake_word.time.monotonic")
    @patch("wake_word.sd.InputStream")
    def test_listen_loop_speech_detection_triggers_check(self, mock_input_stream_cls, mock_monotonic):
        """Speech frames followed by silence should trigger _check_buffer."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        detector = self._make_detector_for_loop()

        # time.monotonic() is NOT called once per frame -- it is called only at
        # specific points: once when speech starts (speech_start_time), and once
        # per silence frame (to check grace period). We tie the monotonic return
        # value to the frame read index so that it reflects the actual elapsed
        # audio time regardless of when/how often monotonic is called.
        speech_frame = self._make_speech_frame()
        silent_frame = self._make_silent_frame()

        frame_sequence = []
        # 10 speech frames (1.0s of speech)
        for _ in range(10):
            frame_sequence.append((speech_frame.copy(), False))
        # 7 silent frames to exceed grace period (0.7s > 0.5s)
        for _ in range(7):
            frame_sequence.append((silent_frame.copy(), False))

        read_idx = [0]

        def mock_read(size):
            idx = read_idx[0]
            if idx < len(frame_sequence):
                read_idx[0] += 1
                return frame_sequence[idx]
            detector._stop_event.set()
            return (silent_frame.copy(), False)

        mock_stream.read = mock_read

        # Return time based on current frame index (each frame = 0.1s)
        mock_monotonic.side_effect = lambda: read_idx[0] * 0.1

        with patch.object(detector, "_check_buffer") as mock_check:
            detector._listen_loop()
            assert mock_check.call_count >= 1

    @patch("wake_word.time.monotonic")
    @patch("wake_word.sd.InputStream")
    def test_listen_loop_short_speech_ignored(self, mock_input_stream_cls, mock_monotonic):
        """Speech shorter than _MIN_SPEECH_SECONDS should not trigger _check_buffer."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        detector = self._make_detector_for_loop()

        speech_frame = self._make_speech_frame()
        silent_frame = self._make_silent_frame()

        # Only 1 speech frame. With frame-index-based monotonic:
        #   speech_start_time = monotonic() at idx=1 -> 0.1s
        #   silence_since set at idx=2 -> 0.2s
        #   Grace expires when (now - 0.2) >= 0.5 -> now >= 0.7 -> idx=7
        #   speech_duration = 0.7 - 0.1 = 0.6s < 0.8s -> too short, ignored
        frame_sequence = []
        frame_sequence.append((speech_frame.copy(), False))
        # 8 silence frames to exceed grace period and end the segment
        for _ in range(8):
            frame_sequence.append((silent_frame.copy(), False))

        read_idx = [0]

        def mock_read(size):
            idx = read_idx[0]
            if idx < len(frame_sequence):
                read_idx[0] += 1
                return frame_sequence[idx]
            detector._stop_event.set()
            return (silent_frame.copy(), False)

        mock_stream.read = mock_read

        # Tie monotonic to frame index so timing reflects audio duration
        mock_monotonic.side_effect = lambda: read_idx[0] * 0.1

        with patch.object(detector, "_check_buffer") as mock_check:
            detector._listen_loop()
            mock_check.assert_not_called()

    @patch("wake_word.sd.InputStream")
    def test_listen_loop_buffer_max_duration_triggers_check(self, mock_input_stream_cls):
        """Buffer exceeding max duration should trigger _check_buffer."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        detector = self._make_detector_for_loop(buffer_duration_seconds=0.5)

        speech_frame = self._make_speech_frame()
        silent_frame = self._make_silent_frame()

        # 0.5s buffer = 5 frames. Send 6 continuous speech frames to exceed it.
        frame_sequence = []
        for _ in range(6):
            frame_sequence.append((speech_frame.copy(), False))

        read_idx = [0]

        def mock_read(size):
            idx = read_idx[0]
            if idx < len(frame_sequence):
                read_idx[0] += 1
                return frame_sequence[idx]
            detector._stop_event.set()
            return (silent_frame.copy(), False)

        mock_stream.read = mock_read

        with patch.object(detector, "_check_buffer") as mock_check:
            detector._listen_loop()
            assert mock_check.call_count >= 1

    @patch("wake_word.sd.InputStream")
    def test_listen_loop_stream_read_exception(self, mock_input_stream_cls):
        """Exception during stream.read should break the loop cleanly."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream
        mock_stream.read.side_effect = OSError("Device disconnected")

        detector = self._make_detector_for_loop()

        # Should not raise
        detector._listen_loop()

        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()

    @patch("wake_word.sd.InputStream")
    def test_listen_loop_stream_closed_on_exit(self, mock_input_stream_cls):
        """Stream should be stopped and closed even on normal exit."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        detector = self._make_detector_for_loop()
        detector._stop_event.set()

        detector._listen_loop()

        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()

    @patch("wake_word.sd.InputStream")
    def test_listen_loop_overflow_logged(self, mock_input_stream_cls):
        """Overflow flag should be handled gracefully (logged, not crash)."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        silent_frame = self._make_silent_frame()

        call_count = [0]

        def mock_read(size):
            call_count[0] += 1
            if call_count[0] == 1:
                # First read: overflow
                return (silent_frame.copy(), True)
            # Stop after first iteration
            detector._stop_event.set()
            return (silent_frame.copy(), False)

        mock_stream.read = mock_read
        detector = self._make_detector_for_loop()

        # Should not raise on overflow
        detector._listen_loop()

    @patch("wake_word.time.monotonic")
    @patch("wake_word.sd.InputStream")
    def test_listen_loop_grace_period_bridges_pauses(self, mock_input_stream_cls, mock_monotonic):
        """Brief silence during speech should not end the segment (grace period)."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        detector = self._make_detector_for_loop()

        speech_frame = self._make_speech_frame()
        silent_frame = self._make_silent_frame()

        # Pattern: 5 speech + 3 silent (0.3s < 0.5s grace) + 5 speech + 7 silent (end)
        # The 3-frame silence gap is within the 0.5s grace period, so it should
        # be bridged. The trailing 7-frame silence (0.7s > 0.5s) ends the segment.
        frame_sequence = []
        for _ in range(5):
            frame_sequence.append((speech_frame.copy(), False))
        for _ in range(3):
            frame_sequence.append((silent_frame.copy(), False))
        for _ in range(5):
            frame_sequence.append((speech_frame.copy(), False))
        for _ in range(7):
            frame_sequence.append((silent_frame.copy(), False))

        read_idx = [0]

        def mock_read(size):
            idx = read_idx[0]
            if idx < len(frame_sequence):
                read_idx[0] += 1
                return frame_sequence[idx]
            detector._stop_event.set()
            return (silent_frame.copy(), False)

        mock_stream.read = mock_read

        # Tie monotonic to frame index so timing reflects audio duration
        mock_monotonic.side_effect = lambda: read_idx[0] * 0.1

        # We must capture the buffer contents inside a side_effect, because
        # _listen_loop calls _clear_buffer(speech_buffer) right after
        # _check_buffer(speech_buffer), which empties the same list reference.
        captured_buffers = []

        def capture_check_buffer(buf):
            captured_buffers.append(list(buf))

        with patch.object(detector, "_check_buffer", side_effect=capture_check_buffer) as mock_check:
            detector._listen_loop()
            # Should be called once (the entire segment, not split by the brief pause)
            assert mock_check.call_count == 1
            # The captured buffer should include all speech and bridged silence frames.
            # 5 speech + 3 silent (bridged) + 5 speech = 13 frames minimum,
            # plus some trailing silence frames appended before grace expires.
            assert len(captured_buffers[0]) >= 13


# ---------------------------------------------------------------------------
# Integration: start -> listen -> detect -> callback -> stop
# ---------------------------------------------------------------------------


class TestIntegrationLifecycle:
    """Integration test: full start-listen-detect-stop cycle with mocked audio."""

    @pytest.mark.timeout(5)
    @patch("wake_word.time.monotonic")
    @patch("wake_word.sd.InputStream")
    def test_full_detection_cycle(self, mock_input_stream_cls, mock_monotonic):
        """Full cycle: start -> speech detected -> wake phrase matched -> callback -> stop."""
        mock_stream = MagicMock()
        mock_input_stream_cls.return_value = mock_stream

        detected_event = threading.Event()
        callback = MagicMock(side_effect=lambda: detected_event.set())

        detector = WakeWordDetector(
            wake_phrase="Hello Cloud",
            on_detected=callback,
            energy_threshold=300.0,
            cooldown_seconds=0.1,
            match_mode="contains",
        )

        # Setup model mock -- use a fresh iterator each time transcribe is called
        mock_model = MagicMock()

        def make_transcribe_result(*args, **kwargs):
            seg = MagicMock()
            seg.text = "Hello Cloud"
            return (iter([seg]), MagicMock())

        mock_model.transcribe.side_effect = make_transcribe_result
        detector._model = mock_model

        # Audio frames: speech for 1s then silence
        speech_frame = np.full((_FRAME_SIZE, 1), 1000, dtype=np.int16)
        silent_frame = np.zeros((_FRAME_SIZE, 1), dtype=np.int16)

        frame_sequence = []
        for _ in range(10):
            frame_sequence.append((speech_frame.copy(), False))
        # 7 silence frames to exceed grace period
        for _ in range(7):
            frame_sequence.append((silent_frame.copy(), False))

        read_idx = [0]
        read_lock = threading.Lock()

        def mock_read(size):
            with read_lock:
                idx = read_idx[0]
                if idx < len(frame_sequence):
                    read_idx[0] += 1
                    return frame_sequence[idx]
            # Keep returning silence until stopped
            if not detector._stop_event.is_set():
                time.sleep(0.05)
            return (silent_frame.copy(), False)

        mock_stream.read = mock_read

        # Tie monotonic to frame index (thread-safe via read_lock already held by read)
        def frame_based_monotonic():
            with read_lock:
                return read_idx[0] * 0.1

        mock_monotonic.side_effect = frame_based_monotonic

        # Bypass _load_model
        with patch.object(detector, "_load_model", return_value=True):
            result = detector.start()
            assert result is True

            # Wait for detection or timeout
            detected = detected_event.wait(timeout=3.0)
            assert detected, "Wake word was not detected within timeout"

            callback.assert_called_once()

            detector.stop()
            assert detector.is_running is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_matches_wake_phrase_only_punctuation_transcript(self):
        """Transcript that is all punctuation normalizes to empty."""
        detector = WakeWordDetector(wake_phrase="Hello Cloud")
        assert detector._matches_wake_phrase("...!!!") is False

    def test_matches_wake_phrase_unicode(self):
        """Unicode characters in wake phrase and transcript."""
        detector = WakeWordDetector(wake_phrase="Hallo Welt", match_mode="contains")
        assert detector._matches_wake_phrase("Hallo Welt!") is True

    def test_matches_wake_phrase_german_text(self):
        """German text with special characters."""
        detector = WakeWordDetector(wake_phrase="Hallo Computer", match_mode="contains")
        assert detector._matches_wake_phrase("Hallo, Computer! Wie geht es?") is True

    def test_detector_multiple_start_stop_cycles(self):
        """Detector should support multiple start/stop cycles."""
        detector = WakeWordDetector()

        with patch.object(detector, "_load_model", return_value=True), \
             patch.object(detector, "_listen_loop"):
            # First cycle
            assert detector.start() is True
            assert detector.is_running is True
            detector.stop()
            assert detector.is_running is False

            # Second cycle
            assert detector.start() is True
            assert detector.is_running is True
            detector.stop()
            assert detector.is_running is False

    def test_stop_event_cleared_on_restart(self):
        """_stop_event should be cleared when starting again."""
        detector = WakeWordDetector()

        with patch.object(detector, "_load_model", return_value=True), \
             patch.object(detector, "_listen_loop"):
            detector.start()
            detector.stop()
            assert detector._stop_event.is_set()

            # After starting again, stop event should be cleared
            detector.start()
            assert not detector._stop_event.is_set()
            detector.stop()

    def test_is_running_property(self):
        """is_running should reflect internal _running state."""
        detector = WakeWordDetector()
        assert detector.is_running is False
        detector._running = True
        assert detector.is_running is True
        detector._running = False
        assert detector.is_running is False

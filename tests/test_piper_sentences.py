"""Tests for Piper TTS sentence splitting and silence generation."""

import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestSplitSentences:
    """Tests for PiperLocalTTS._split_sentences()."""

    @staticmethod
    def _split(text: str) -> list[str]:
        """Helper: call _split_sentences on PiperLocalTTS."""
        from local_tts import PiperLocalTTS
        return PiperLocalTTS._split_sentences(text)

    def test_single_sentence(self):
        """Single sentence returns a single-element list."""
        result = self._split("Das ist ein Test")
        assert result == ["Das ist ein Test"]

    def test_two_sentences(self):
        """Two sentences separated by period + space."""
        result = self._split("Erster Satz. Zweiter Satz.")
        assert result == ["Erster Satz.", "Zweiter Satz."]

    def test_exclamation_mark(self):
        """Splits on exclamation mark."""
        result = self._split("Hallo! Wie geht es dir?")
        assert result == ["Hallo!", "Wie geht es dir?"]

    def test_question_mark(self):
        """Splits on question mark."""
        result = self._split("Was ist das? Ich weiß es nicht.")
        assert result == ["Was ist das?", "Ich weiß es nicht."]

    def test_semicolon(self):
        """Splits on semicolon."""
        result = self._split("Teil eins; Teil zwei.")
        assert result == ["Teil eins;", "Teil zwei."]

    def test_multiple_sentences(self):
        """Three or more real sentences (with spaces)."""
        result = self._split(
            "Das ist eins. Das ist zwei. Das ist drei. Das ist vier."
        )
        assert len(result) == 4

    def test_german_abbreviation_coalesced(self):
        """Short fragments like 'z.B.' are coalesced with next segment."""
        result = self._split("z.B. in Berlin ist es kalt.")
        # "z.B." is < 15 chars, should be merged with next part
        assert len(result) == 1
        assert "z.B." in result[0]

    def test_dr_abbreviation_coalesced(self):
        """Short 'Dr.' fragment is coalesced."""
        result = self._split("Dr. Müller hat angerufen.")
        assert len(result) == 1
        assert result[0] == "Dr. Müller hat angerufen."

    def test_empty_string(self):
        """Empty string returns empty list."""
        assert self._split("") == []

    def test_whitespace_only(self):
        """Whitespace-only string returns empty list."""
        assert self._split("   ") == []

    def test_no_punctuation(self):
        """Text without sentence-ending punctuation returns as-is."""
        result = self._split("Dies ist ein langer Text ohne Punkt")
        assert result == ["Dies ist ein langer Text ohne Punkt"]

    def test_preserves_trailing_period(self):
        """Trailing period stays with the sentence."""
        result = self._split("Hallo Welt.")
        assert result == ["Hallo Welt."]

    def test_multiple_spaces(self):
        """Extra whitespace between sentences is handled."""
        result = self._split("Satz eins.   Satz zwei.")
        assert len(result) == 2
        assert result[0] == "Satz eins."

    def test_sentence_with_spaces_not_coalesced(self):
        """Fragments containing spaces are real sentences, not abbreviations."""
        result = self._split("Das ist genug. Und das auch.")
        assert len(result) == 2

    def test_short_sentence_not_coalesced(self):
        """Short sentences ending with ! or ? are not coalesced."""
        result = self._split("Nein! Das geht nicht.")
        assert len(result) == 2
        assert result[0] == "Nein!"


class TestGenerateSilence:
    """Tests for PiperLocalTTS._generate_silence()."""

    @staticmethod
    def _silence(sample_rate: int, duration_ms: int) -> np.ndarray:
        from local_tts import PiperLocalTTS
        return PiperLocalTTS._generate_silence(sample_rate, duration_ms)

    def test_correct_length(self):
        """350ms at 22050 Hz = 7717 samples."""
        result = self._silence(22050, 350)
        expected = int(22050 * 350 / 1000)
        assert len(result) == expected

    def test_all_zeros(self):
        """Silence is all zeros."""
        result = self._silence(22050, 100)
        assert np.all(result == 0.0)

    def test_float32_dtype(self):
        """Output is float32."""
        result = self._silence(22050, 100)
        assert result.dtype == np.float32

    def test_zero_duration(self):
        """Zero duration produces empty array."""
        result = self._silence(22050, 0)
        assert len(result) == 0

    def test_one_dimensional(self):
        """Output is 1-D array."""
        result = self._silence(22050, 200)
        assert result.ndim == 1


class TestSentenceLevelSynthesis:
    """Tests for the sentence-level synthesis path in PiperLocalTTS.synthesize()."""

    def _make_backend(self, sentence_pause_ms=350, speed=1.0):
        """Create a PiperLocalTTS with mocked internals."""
        with patch("local_tts.is_espeakng_available", return_value=True):
            from local_tts import PiperLocalTTS
            backend = PiperLocalTTS(
                voice_name="de_DE-thorsten-medium",
                speed=speed,
                sentence_pause_ms=sentence_pause_ms,
            )

        # Mock the model as loaded
        backend._loaded = True
        backend._session = MagicMock()
        backend._config = {"espeak": {"voice": "de"}}
        backend._phoneme_id_map = {
            "^": [1], "$": [2], "_": [0],
            "a": [3], "b": [4], " ": [5],
        }
        backend._sample_rate = 22050
        backend._inference_params = {}
        backend._session_input_names = ["input", "input_lengths", "scales"]

        # Mock phonemizer
        backend._phonemizer = MagicMock()
        backend._phonemizer.phonemize.return_value = "ab ab"

        # Mock ONNX inference to return known PCM
        def fake_infer(phoneme_ids):
            return np.ones(1000, dtype=np.float32) * 0.5

        backend._infer = MagicMock(side_effect=fake_infer)

        return backend

    def test_single_sentence_uses_original_path(self):
        """Single sentence doesn't split."""
        backend = self._make_backend()
        wav = backend.synthesize("Ein Satz")
        # Should call _infer once (single segment)
        assert backend._infer.call_count == 1

    def test_multi_sentence_calls_infer_per_sentence(self):
        """Multiple sentences call _infer once per sentence."""
        backend = self._make_backend()
        wav = backend.synthesize("Satz eins. Satz zwei. Satz drei.")
        assert backend._infer.call_count == 3

    def test_multi_sentence_audio_longer_than_single(self):
        """Multi-sentence WAV is longer due to silence gaps."""
        backend_multi = self._make_backend(sentence_pause_ms=350)
        backend_single = self._make_backend(sentence_pause_ms=0)

        text = "Satz eins. Satz zwei."
        wav_multi = backend_multi.synthesize(text)
        wav_single = backend_single.synthesize(text)

        # Multi should be larger (contains silence gaps)
        assert len(wav_multi) > len(wav_single)

    def test_pause_disabled_uses_single_path(self):
        """sentence_pause_ms=0 uses the single-shot path."""
        backend = self._make_backend(sentence_pause_ms=0)
        wav = backend.synthesize("Satz eins. Satz zwei.")
        # With pause disabled, should call _infer once (full text)
        assert backend._infer.call_count == 1

    def test_returns_valid_wav(self):
        """Output starts with WAV RIFF header."""
        backend = self._make_backend()
        wav = backend.synthesize("Satz eins. Satz zwei.")
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"

    def test_sentence_pause_stored(self):
        """sentence_pause_ms is stored on the instance."""
        backend = self._make_backend(sentence_pause_ms=500)
        assert backend._sentence_pause_ms == 500


class TestSentencePauseConfig:
    """Tests for sentence_pause_ms configuration loading."""

    def test_default_constant_exists(self):
        """DEFAULT_TTS_SENTENCE_PAUSE_MS exists in constants."""
        from constants import DEFAULT_TTS_SENTENCE_PAUSE_MS
        assert DEFAULT_TTS_SENTENCE_PAUSE_MS == 350

    def test_config_field_default(self):
        """AppConfig has tts_sentence_pause_ms with correct default."""
        from constants import DEFAULT_TTS_SENTENCE_PAUSE_MS
        from config import AppConfig
        cfg = AppConfig()
        assert cfg.tts_sentence_pause_ms == DEFAULT_TTS_SENTENCE_PAUSE_MS

    def test_factory_accepts_sentence_pause_ms(self):
        """create_tts_backend accepts sentence_pause_ms parameter."""
        import inspect
        from tts import create_tts_backend
        sig = inspect.signature(create_tts_backend)
        assert "sentence_pause_ms" in sig.parameters

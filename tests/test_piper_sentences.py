"""Tests for Piper TTS sentence/clause splitting, normalization, and silence generation."""

import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestSplitSentences:
    """Tests for PiperLocalTTS._split_sentences() (legacy API)."""

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


class TestSplitClauses:
    """Tests for PiperLocalTTS._split_clauses()."""

    @staticmethod
    def _split(text: str, pause_ms: int = 350) -> list[tuple[str, int]]:
        from local_tts import PiperLocalTTS
        return PiperLocalTTS._split_clauses(text, sentence_pause_ms=pause_ms)

    def test_single_clause(self):
        """Single clause returns one element with pause=0."""
        result = self._split("Das ist ein Test")
        assert len(result) == 1
        assert result[0][0] == "Das ist ein Test"
        assert result[0][1] == 0

    def test_two_sentences(self):
        """Two sentences get sentence-level pause between them."""
        result = self._split("Erster Satz. Zweiter Satz.")
        assert len(result) == 2
        assert result[0][1] == 350  # sentence pause
        assert result[1][1] == 0    # last element

    def test_comma_with_conjunction_splits(self):
        """Comma followed by a conjunction splits into clauses."""
        result = self._split("Das ist gut, aber es fehlt noch etwas.")
        assert len(result) >= 2
        # First clause should end with comma
        assert result[0][0].endswith(",")
        assert result[0][1] == 150  # clause-boundary pause

    def test_comma_without_conjunction_no_split(self):
        """Comma without conjunction does NOT split (preserves lists)."""
        result = self._split("Berlin, Hamburg und München")
        assert len(result) == 1

    def test_semicolon_gets_300ms(self):
        """Semicolons get 300ms pause."""
        result = self._split("Teil eins; Teil zwei")
        assert len(result) == 2
        assert result[0][1] == 300

    def test_colon_gets_250ms(self):
        """Colons get 250ms pause."""
        result = self._split("Die Antwort: zweiundvierzig")
        assert len(result) == 2
        assert result[0][1] == 250

    def test_em_dash_gets_200ms(self):
        """Em/en dashes get 200ms pause."""
        result = self._split("Das Ergebnis \u2014 erstaunlich gut")
        assert len(result) == 2
        assert result[0][1] == 200

    def test_empty_string(self):
        """Empty string returns empty list."""
        assert self._split("") == []

    def test_last_element_always_zero_pause(self):
        """Last element always has pause_after_ms=0."""
        result = self._split("Satz eins. Satz zwei. Satz drei.")
        assert result[-1][1] == 0

    def test_german_conjunction_weil(self):
        """German conjunction 'weil' triggers clause split."""
        result = self._split("Ich bin müde, weil ich schlecht geschlafen habe.")
        assert len(result) >= 2
        assert result[0][1] == 150

    def test_english_conjunction_because(self):
        """English conjunction 'because' triggers clause split."""
        result = self._split("I stayed home, because it was raining.")
        assert len(result) >= 2
        assert result[0][1] == 150

    def test_abbreviation_not_split(self):
        """Abbreviation-like text (z.B.) is not split mid-abbreviation."""
        result = self._split("z.B. in Berlin")
        # Should be a single clause (abbreviation coalesced)
        assert len(result) == 1

    def test_custom_sentence_pause(self):
        """Custom sentence_pause_ms is used for sentence boundaries."""
        result = self._split("Satz eins. Satz zwei.", pause_ms=500)
        assert result[0][1] == 500


class TestNormalizeForTts:
    """Tests for PiperLocalTTS._normalize_for_tts()."""

    @staticmethod
    def _normalize(text: str, language: str = "de") -> str:
        from local_tts import PiperLocalTTS
        return PiperLocalTTS._normalize_for_tts(text, language)

    def test_german_zb(self):
        """z.B. is expanded to 'zum Beispiel'."""
        result = self._normalize("z.B. in Berlin", "de")
        assert "zum Beispiel" in result
        assert "z.B." not in result

    def test_german_dh(self):
        """d.h. is expanded to 'das heißt'."""
        result = self._normalize("d.h. es funktioniert", "de")
        assert "das heißt" in result

    def test_german_usw(self):
        """usw. is expanded to 'und so weiter'."""
        result = self._normalize("und usw.", "de")
        assert "und so weiter" in result

    def test_german_bzw(self):
        """bzw. is expanded to 'beziehungsweise'."""
        result = self._normalize("A bzw. B", "de")
        assert "beziehungsweise" in result

    def test_german_dr(self):
        """Dr. is expanded to 'Doktor'."""
        result = self._normalize("Dr. Müller", "de")
        assert "Doktor" in result

    def test_german_euro(self):
        """Euro sign is expanded."""
        result = self._normalize("42€", "de")
        assert "Euro" in result

    def test_german_percent(self):
        """Percent sign is expanded."""
        result = self._normalize("42%", "de")
        assert "Prozent" in result

    def test_english_eg(self):
        """e.g. is expanded to 'for example'."""
        result = self._normalize("e.g. this one", "en")
        assert "for example" in result

    def test_english_ie(self):
        """i.e. is expanded to 'that is'."""
        result = self._normalize("i.e. the best", "en")
        assert "that is" in result

    def test_english_mr(self):
        """Mr. is expanded to 'Mister'."""
        result = self._normalize("Mr. Smith", "en")
        assert "Mister" in result

    def test_english_percent(self):
        """Percent sign is expanded in English."""
        result = self._normalize("42%", "en")
        assert "percent" in result

    def test_ellipsis_replaced(self):
        """Ellipsis is replaced with period."""
        result = self._normalize("Hmm... okay", "de")
        assert "..." not in result
        assert "." in result

    def test_empty_text(self):
        """Empty text returns empty."""
        assert self._normalize("", "de") == ""

    def test_language_code_with_region(self):
        """Language code like 'de-DE' is handled correctly."""
        result = self._normalize("z.B. hier", "de-DE")
        assert "zum Beispiel" in result

    def test_no_double_spaces(self):
        """No double spaces in output."""
        result = self._normalize("  a  b  c  ", "de")
        assert "  " not in result

    def test_german_ca(self):
        """ca. is expanded to 'circa'."""
        result = self._normalize("ca. 42 Grad", "de")
        assert "circa" in result

    def test_german_etc(self):
        """etc. is expanded to 'et cetera'."""
        result = self._normalize("A, B, etc.", "de")
        assert "et cetera" in result


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
    """Tests for the clause-level synthesis path in PiperLocalTTS.synthesize()."""

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
        def fake_infer(phoneme_ids, speaker_id=None):
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

    def test_factory_accepts_noise_params(self):
        """create_tts_backend accepts noise_scale and noise_w parameters."""
        import inspect
        from tts import create_tts_backend
        sig = inspect.signature(create_tts_backend)
        assert "noise_scale" in sig.parameters
        assert "noise_w" in sig.parameters


class TestNoiseParams:
    """Tests for noise_scale and noise_w config and constructor."""

    def test_config_defaults(self):
        """AppConfig has correct defaults for noise params."""
        from config import AppConfig
        from constants import DEFAULT_TTS_NOISE_SCALE, DEFAULT_TTS_NOISE_W
        cfg = AppConfig()
        assert cfg.tts_noise_scale == DEFAULT_TTS_NOISE_SCALE
        assert cfg.tts_noise_w == DEFAULT_TTS_NOISE_W

    def test_constructor_stores_noise_params(self):
        """PiperLocalTTS stores noise_scale and noise_w."""
        from local_tts import PiperLocalTTS
        tts = PiperLocalTTS(
            voice_name="test-voice",
            noise_scale=0.75,
            noise_w=0.9,
        )
        assert tts._noise_scale == 0.75
        assert tts._noise_w == 0.9

    def test_constructor_defaults_to_none(self):
        """PiperLocalTTS defaults noise params to None."""
        from local_tts import PiperLocalTTS
        tts = PiperLocalTTS(voice_name="test-voice")
        assert tts._noise_scale is None
        assert tts._noise_w is None


class TestPreprocessConstants:
    """Tests for TTS preprocessing constants."""

    def test_presets_dict_exists(self):
        """TTS_PREPROCESS_PRESETS exists and has expected keys."""
        from constants import TTS_PREPROCESS_PRESETS
        assert "clean" in TTS_PREPROCESS_PRESETS
        assert "concise" in TTS_PREPROCESS_PRESETS
        assert "professional" in TTS_PREPROCESS_PRESETS
        assert "bullets_to_prose" in TTS_PREPROCESS_PRESETS

    def test_presets_have_label_and_prompt(self):
        """Each preset has 'label' and 'prompt' keys."""
        from constants import TTS_PREPROCESS_PRESETS
        for key, info in TTS_PREPROCESS_PRESETS.items():
            assert "label" in info, f"Missing 'label' in preset '{key}'"
            assert "prompt" in info, f"Missing 'prompt' in preset '{key}'"
            assert len(info["prompt"]) > 20, f"Prompt too short in preset '{key}'"

    def test_default_prompt_exists(self):
        """TTS_PREPROCESS_DEFAULT_PROMPT exists and is non-empty."""
        from constants import TTS_PREPROCESS_DEFAULT_PROMPT
        assert len(TTS_PREPROCESS_DEFAULT_PROMPT) > 20

    def test_config_defaults(self):
        """AppConfig has correct defaults for preprocess fields."""
        from config import AppConfig
        cfg = AppConfig()
        assert cfg.tts_preprocess_with_llm is False
        assert cfg.tts_preprocess_prompt == ""

    def test_clause_conjunctions_exist(self):
        """Conjunction sets exist in constants."""
        from constants import CLAUSE_CONJUNCTIONS_DE, CLAUSE_CONJUNCTIONS_EN
        assert "aber" in CLAUSE_CONJUNCTIONS_DE
        assert "weil" in CLAUSE_CONJUNCTIONS_DE
        assert "but" in CLAUSE_CONJUNCTIONS_EN
        assert "because" in CLAUSE_CONJUNCTIONS_EN

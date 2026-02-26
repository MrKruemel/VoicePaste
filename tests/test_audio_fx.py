"""Tests for the audio_fx module (Piper TTS post-processing effects).

Tests cover:
- Individual effects (pitch shift, formant shift, EQ, reverb)
- AudioFXConfig bypass logic and cache suffix generation
- apply_effects() chain ordering and composition
- Edge cases: empty audio, very short audio, silent audio
- Parameter clamping and boundary values
"""

import numpy as np
import pytest
import sys
import os

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from audio_fx import (
    AudioFXConfig,
    apply_effects,
    eq_shelf,
    formant_shift,
    pitch_shift,
    reverb_vectorized,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_RATE = 22050


@pytest.fixture
def sine_440() -> np.ndarray:
    """Generate a 1-second 440 Hz sine wave at 22050 Hz sample rate."""
    t = np.linspace(0, 1.0, SAMPLE_RATE, endpoint=False, dtype=np.float32)
    return 0.5 * np.sin(2 * np.pi * 440 * t)


@pytest.fixture
def short_audio() -> np.ndarray:
    """Generate a very short audio segment (100 samples)."""
    return np.random.randn(100).astype(np.float32) * 0.3


@pytest.fixture
def silence() -> np.ndarray:
    """Generate 1 second of silence."""
    return np.zeros(SAMPLE_RATE, dtype=np.float32)


# ---------------------------------------------------------------------------
# AudioFXConfig tests
# ---------------------------------------------------------------------------


class TestAudioFXConfig:
    """Tests for AudioFXConfig dataclass."""

    def test_default_is_bypass(self) -> None:
        """Default config should be bypass (no effects)."""
        config = AudioFXConfig()
        assert config.is_bypass is True

    def test_non_default_pitch_not_bypass(self) -> None:
        config = AudioFXConfig(pitch_semitones=2.0)
        assert config.is_bypass is False

    def test_non_default_formant_not_bypass(self) -> None:
        config = AudioFXConfig(formant_shift=0.9)
        assert config.is_bypass is False

    def test_non_default_bass_not_bypass(self) -> None:
        config = AudioFXConfig(bass_db=3.0)
        assert config.is_bypass is False

    def test_non_default_treble_not_bypass(self) -> None:
        config = AudioFXConfig(treble_db=-2.0)
        assert config.is_bypass is False

    def test_non_default_reverb_not_bypass(self) -> None:
        config = AudioFXConfig(reverb_mix=0.1)
        assert config.is_bypass is False

    def test_cache_suffix_bypass_is_empty(self) -> None:
        """Bypass config produces empty cache suffix."""
        config = AudioFXConfig()
        assert config.to_cache_suffix() == ""

    def test_cache_suffix_pitch_only(self) -> None:
        config = AudioFXConfig(pitch_semitones=2.0)
        suffix = config.to_cache_suffix()
        assert suffix.startswith("fx:")
        assert "p2.0" in suffix

    def test_cache_suffix_all_params(self) -> None:
        config = AudioFXConfig(
            pitch_semitones=-3.0,
            formant_shift=0.85,
            bass_db=6.0,
            treble_db=-4.0,
            reverb_mix=0.2,
        )
        suffix = config.to_cache_suffix()
        assert "fx:" in suffix
        assert "p-3.0" in suffix
        assert "f0.85" in suffix
        assert "b6.0" in suffix
        assert "t-4.0" in suffix
        assert "r0.20" in suffix

    def test_cache_suffix_only_includes_non_defaults(self) -> None:
        """Only non-default parameters appear in the suffix."""
        config = AudioFXConfig(bass_db=3.0)
        suffix = config.to_cache_suffix()
        assert suffix == "fx:b3.0"  # Only bass is non-default

    def test_frozen_dataclass(self) -> None:
        """AudioFXConfig should be immutable (frozen)."""
        config = AudioFXConfig()
        with pytest.raises(AttributeError):
            config.pitch_semitones = 5.0  # type: ignore


# ---------------------------------------------------------------------------
# Pitch shift tests
# ---------------------------------------------------------------------------


class TestPitchShift:
    """Tests for the phase vocoder pitch shift."""

    def test_zero_shift_returns_same(self, sine_440: np.ndarray) -> None:
        """Zero semitones should return the input unchanged."""
        result = pitch_shift(sine_440, SAMPLE_RATE, semitones=0.0)
        assert result is sine_440  # identity, not a copy

    def test_output_length_matches_input(self, sine_440: np.ndarray) -> None:
        """Pitch shift should preserve audio length."""
        result = pitch_shift(sine_440, SAMPLE_RATE, semitones=3.0)
        assert len(result) == len(sine_440)

    def test_positive_shift_raises_frequency(self, sine_440: np.ndarray) -> None:
        """Positive semitones should increase dominant frequency."""
        result = pitch_shift(sine_440, SAMPLE_RATE, semitones=6.0)
        # Check via FFT: peak should be at a higher frequency
        orig_fft = np.abs(np.fft.rfft(sine_440))
        shifted_fft = np.abs(np.fft.rfft(result))
        orig_peak_bin = np.argmax(orig_fft)
        shifted_peak_bin = np.argmax(shifted_fft)
        orig_freq = orig_peak_bin * SAMPLE_RATE / len(sine_440)
        shifted_freq = shifted_peak_bin * SAMPLE_RATE / len(result)
        # +6 semitones should raise 440 Hz to ~622 Hz.
        # Phase vocoder is approximate, so allow generous tolerance.
        assert shifted_freq > orig_freq * 1.2
        assert shifted_freq < orig_freq * 2.0

    def test_negative_shift_lowers_frequency(self, sine_440: np.ndarray) -> None:
        """Negative semitones should decrease dominant frequency."""
        result = pitch_shift(sine_440, SAMPLE_RATE, semitones=-6.0)
        orig_fft = np.abs(np.fft.rfft(sine_440))
        shifted_fft = np.abs(np.fft.rfft(result))
        orig_peak_bin = np.argmax(orig_fft)
        shifted_peak_bin = np.argmax(shifted_fft)
        orig_freq = orig_peak_bin * SAMPLE_RATE / len(sine_440)
        shifted_freq = shifted_peak_bin * SAMPLE_RATE / len(result)
        # -6 semitones should lower 440 Hz to ~311 Hz.
        assert shifted_freq < orig_freq * 0.9
        assert shifted_freq > orig_freq * 0.5

    def test_short_audio_unchanged(self) -> None:
        """Audio shorter than 256 samples should be returned unchanged."""
        short = np.random.randn(100).astype(np.float32)
        result = pitch_shift(short, SAMPLE_RATE, semitones=3.0)
        assert result is short

    def test_output_is_float32(self, sine_440: np.ndarray) -> None:
        result = pitch_shift(sine_440, SAMPLE_RATE, semitones=2.0)
        assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# Formant shift tests
# ---------------------------------------------------------------------------


class TestFormantShift:
    """Tests for cepstral formant shifting."""

    def test_unity_shift_returns_same(self, sine_440: np.ndarray) -> None:
        """Shift factor 1.0 should return the input unchanged."""
        result = formant_shift(sine_440, SAMPLE_RATE, shift_factor=1.0)
        assert result is sine_440

    def test_output_length_matches_input(self, sine_440: np.ndarray) -> None:
        result = formant_shift(sine_440, SAMPLE_RATE, shift_factor=0.8)
        assert len(result) == len(sine_440)

    def test_shift_modifies_spectrum(self, sine_440: np.ndarray) -> None:
        """Formant shift should measurably alter the spectral envelope."""
        result = formant_shift(sine_440, SAMPLE_RATE, shift_factor=0.7)
        # The result should differ from the original
        diff = np.mean(np.abs(result - sine_440))
        assert diff > 0.01

    def test_upward_shift(self, sine_440: np.ndarray) -> None:
        """Factor > 1.0 should shift formants up."""
        result = formant_shift(sine_440, SAMPLE_RATE, shift_factor=1.3)
        assert len(result) == len(sine_440)
        # Should be different from original
        assert not np.allclose(result, sine_440, atol=0.01)

    def test_short_audio_unchanged(self) -> None:
        short = np.random.randn(100).astype(np.float32)
        result = formant_shift(short, SAMPLE_RATE, shift_factor=0.8)
        assert result is short


# ---------------------------------------------------------------------------
# EQ shelf tests
# ---------------------------------------------------------------------------


class TestEQShelf:
    """Tests for the FFT shelf equalizer."""

    def test_flat_eq_returns_same(self, sine_440: np.ndarray) -> None:
        """0 dB bass and treble should return the input unchanged."""
        result = eq_shelf(sine_440, SAMPLE_RATE, bass_db=0.0, treble_db=0.0)
        assert result is sine_440

    def test_bass_boost_increases_low_freq_energy(self) -> None:
        """Bass boost should increase energy below 200 Hz."""
        # Generate a broadband signal (white noise)
        rng = np.random.RandomState(42)
        noise = rng.randn(SAMPLE_RATE).astype(np.float32) * 0.3
        result = eq_shelf(noise, SAMPLE_RATE, bass_db=12.0, treble_db=0.0)

        # Compare energy in low frequencies (0-200 Hz)
        n_fft = len(noise)
        orig_fft = np.abs(np.fft.rfft(noise))
        result_fft = np.abs(np.fft.rfft(result))

        # Bin for 200 Hz
        bin_200 = int(200 * n_fft / SAMPLE_RATE)
        orig_bass_energy = np.sum(orig_fft[:bin_200] ** 2)
        result_bass_energy = np.sum(result_fft[:bin_200] ** 2)

        assert result_bass_energy > orig_bass_energy * 1.5

    def test_treble_boost_increases_high_freq_energy(self) -> None:
        """Treble boost should increase energy above 3000 Hz."""
        rng = np.random.RandomState(42)
        noise = rng.randn(SAMPLE_RATE).astype(np.float32) * 0.3
        result = eq_shelf(noise, SAMPLE_RATE, bass_db=0.0, treble_db=12.0)

        n_fft = len(noise)
        orig_fft = np.abs(np.fft.rfft(noise))
        result_fft = np.abs(np.fft.rfft(result))

        bin_3000 = int(3000 * n_fft / SAMPLE_RATE)
        orig_treble_energy = np.sum(orig_fft[bin_3000:] ** 2)
        result_treble_energy = np.sum(result_fft[bin_3000:] ** 2)

        assert result_treble_energy > orig_treble_energy * 1.5

    def test_bass_cut_decreases_low_freq_energy(self) -> None:
        """Bass cut should decrease energy below 200 Hz."""
        rng = np.random.RandomState(42)
        noise = rng.randn(SAMPLE_RATE).astype(np.float32) * 0.3
        result = eq_shelf(noise, SAMPLE_RATE, bass_db=-12.0, treble_db=0.0)

        n_fft = len(noise)
        orig_fft = np.abs(np.fft.rfft(noise))
        result_fft = np.abs(np.fft.rfft(result))

        bin_200 = int(200 * n_fft / SAMPLE_RATE)
        orig_bass_energy = np.sum(orig_fft[:bin_200] ** 2)
        result_bass_energy = np.sum(result_fft[:bin_200] ** 2)

        assert result_bass_energy < orig_bass_energy * 0.5

    def test_output_length_matches_input(self, sine_440: np.ndarray) -> None:
        result = eq_shelf(sine_440, SAMPLE_RATE, bass_db=6.0, treble_db=-3.0)
        assert len(result) == len(sine_440)

    def test_very_short_audio(self) -> None:
        """Audio shorter than 64 samples should be returned unchanged."""
        short = np.random.randn(32).astype(np.float32)
        result = eq_shelf(short, SAMPLE_RATE, bass_db=6.0, treble_db=6.0)
        assert result is short


# ---------------------------------------------------------------------------
# Reverb tests
# ---------------------------------------------------------------------------


class TestReverb:
    """Tests for the FFT convolution reverb."""

    def test_zero_mix_returns_same(self, sine_440: np.ndarray) -> None:
        """Mix = 0.0 should return the input unchanged."""
        result = reverb_vectorized(sine_440, SAMPLE_RATE, mix=0.0)
        assert result is sine_440

    def test_output_length_matches_input(self, sine_440: np.ndarray) -> None:
        result = reverb_vectorized(sine_440, SAMPLE_RATE, mix=0.3)
        assert len(result) == len(sine_440)

    def test_reverb_adds_energy_to_silence_after_signal(self) -> None:
        """Reverb should add a tail: energy continues after the dry signal ends."""
        # Create a short burst followed by silence
        burst = np.zeros(SAMPLE_RATE, dtype=np.float32)
        burst[:1000] = 0.5 * np.sin(
            2 * np.pi * 440 * np.arange(1000) / SAMPLE_RATE
        ).astype(np.float32)

        result = reverb_vectorized(burst, SAMPLE_RATE, mix=0.4)

        # The tail (after the burst ends) should have some energy
        tail_energy = np.mean(result[2000:5000] ** 2)
        orig_tail_energy = np.mean(burst[2000:5000] ** 2)
        assert tail_energy > orig_tail_energy + 1e-6

    def test_mix_clamped_to_half(self, sine_440: np.ndarray) -> None:
        """Mix values above 0.5 should be clamped."""
        # Should not crash with mix > 0.5
        result = reverb_vectorized(sine_440, SAMPLE_RATE, mix=0.8)
        assert len(result) == len(sine_440)

    def test_very_short_audio(self) -> None:
        short = np.random.randn(32).astype(np.float32)
        result = reverb_vectorized(short, SAMPLE_RATE, mix=0.3)
        assert result is short

    def test_silence_stays_silent(self, silence: np.ndarray) -> None:
        """Reverb on silence should produce near-silence."""
        result = reverb_vectorized(silence, SAMPLE_RATE, mix=0.3)
        assert np.max(np.abs(result)) < 0.01


# ---------------------------------------------------------------------------
# apply_effects() chain tests
# ---------------------------------------------------------------------------


class TestApplyEffects:
    """Tests for the full effects chain."""

    def test_bypass_returns_identity(self, sine_440: np.ndarray) -> None:
        """Default config should return the exact same array (no copy)."""
        config = AudioFXConfig()
        result = apply_effects(sine_440, SAMPLE_RATE, config)
        assert result is sine_440

    def test_single_effect(self, sine_440: np.ndarray) -> None:
        """Enabling only one effect should work."""
        config = AudioFXConfig(bass_db=6.0)
        result = apply_effects(sine_440, SAMPLE_RATE, config)
        assert len(result) == len(sine_440)
        assert not np.array_equal(result, sine_440)

    def test_all_effects(self, sine_440: np.ndarray) -> None:
        """All effects active should produce output without errors."""
        config = AudioFXConfig(
            pitch_semitones=2.0,
            formant_shift=0.9,
            bass_db=3.0,
            treble_db=-2.0,
            reverb_mix=0.15,
        )
        result = apply_effects(sine_440, SAMPLE_RATE, config)
        assert len(result) == len(sine_440)
        assert result.dtype == np.float32

    def test_empty_audio(self) -> None:
        """Empty array should return empty array."""
        config = AudioFXConfig(pitch_semitones=2.0)
        result = apply_effects(np.array([], dtype=np.float32), SAMPLE_RATE, config)
        assert len(result) == 0

    def test_rejects_multidimensional(self) -> None:
        """Should raise ValueError for non-1D input."""
        config = AudioFXConfig(bass_db=3.0)
        audio_2d = np.zeros((2, 100), dtype=np.float32)
        with pytest.raises(ValueError, match="1-D"):
            apply_effects(audio_2d, SAMPLE_RATE, config)

    def test_chain_order_pitch_before_eq(self, sine_440: np.ndarray) -> None:
        """Pitch shift should be applied before EQ."""
        # Shift a 440 Hz sine up by 6 semitones (to ~622 Hz).
        # Then apply treble boost at 3000 Hz.
        # Verify the peak frequency is raised from 440 Hz.
        config = AudioFXConfig(pitch_semitones=6.0, treble_db=6.0)
        result = apply_effects(sine_440, SAMPLE_RATE, config)
        fft = np.abs(np.fft.rfft(result))
        peak_bin = np.argmax(fft)
        peak_freq = peak_bin * SAMPLE_RATE / len(result)
        # Should be significantly above 440 Hz (phase vocoder is
        # approximate, so use generous tolerance)
        assert peak_freq > 550
        assert peak_freq < 800

    def test_output_bounded(self, sine_440: np.ndarray) -> None:
        """Output should be clipped to [-1, 1] after RMS normalization."""
        config = AudioFXConfig(
            bass_db=12.0,
            treble_db=12.0,
            reverb_mix=0.5,
        )
        result = apply_effects(sine_440, SAMPLE_RATE, config)
        assert np.max(np.abs(result)) <= 1.0

    def test_rms_normalization_preserves_loudness(self, sine_440: np.ndarray) -> None:
        """Output RMS should be close to input RMS after FX processing."""
        config = AudioFXConfig(
            pitch_semitones=2.0,
            formant_shift=0.9,
        )
        orig_rms = np.sqrt(np.mean(sine_440 ** 2))
        result = apply_effects(sine_440, SAMPLE_RATE, config)
        result_rms = np.sqrt(np.mean(result ** 2))
        # Allow 20% tolerance (clipping can reduce RMS slightly)
        assert result_rms > orig_rms * 0.8
        assert result_rms < orig_rms * 1.2

    def test_silence_stays_silent(self) -> None:
        """Silent input should stay silent after FX (no amplification of noise)."""
        silent = np.zeros(SAMPLE_RATE, dtype=np.float32)
        config = AudioFXConfig(pitch_semitones=3.0, bass_db=6.0)
        result = apply_effects(silent, SAMPLE_RATE, config)
        assert np.max(np.abs(result)) < 1e-6

    def test_rms_gain_capped(self) -> None:
        """RMS normalization gain should not exceed 4x (near-silence guard)."""
        # Create very quiet audio that will be heavily attenuated by EQ cuts
        quiet = np.full(SAMPLE_RATE, 0.001, dtype=np.float32)
        config = AudioFXConfig(bass_db=-12.0, treble_db=-12.0)
        result = apply_effects(quiet, SAMPLE_RATE, config)
        # Even with heavy cuts, output should not be more than 4x the
        # processed amplitude (gain is capped)
        assert np.max(np.abs(result)) < 0.01 * 4.0 + 0.001


# ---------------------------------------------------------------------------
# Performance / regression tests
# ---------------------------------------------------------------------------


class TestPerformance:
    """Lightweight performance sanity checks."""

    def test_bypass_is_fast(self, sine_440: np.ndarray) -> None:
        """Bypass should complete in microseconds (no processing)."""
        import time
        config = AudioFXConfig()
        t0 = time.monotonic()
        for _ in range(1000):
            apply_effects(sine_440, SAMPLE_RATE, config)
        elapsed = time.monotonic() - t0
        # 1000 calls should take < 100ms (they're just a property check)
        assert elapsed < 0.1

    def test_single_effect_reasonable_time(self, sine_440: np.ndarray) -> None:
        """A single effect on 1 second of audio should take < 500ms."""
        import time
        config = AudioFXConfig(pitch_semitones=3.0)
        t0 = time.monotonic()
        apply_effects(sine_440, SAMPLE_RATE, config)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5

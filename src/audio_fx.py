"""Audio effects post-processing for Piper local TTS.

Applies optional audio effects to float32 PCM audio between VITS model
inference and int16 conversion. All effects are implemented in pure numpy
with no additional dependencies.

Effects chain order (optimized for quality):
    1. Pitch shift   -- phase vocoder, changes fundamental frequency
    2. Formant shift -- cepstral envelope manipulation, changes voice depth
    3. Bass EQ       -- FFT shelf filter at 200 Hz
    4. Treble EQ     -- FFT shelf filter at 3000 Hz
    5. Reverb        -- multi-tap comb filter, adds spaciousness

The chain order matters:
    - Pitch shift runs first because subsequent effects should operate on
      the already-transposed signal.
    - Formant shift runs after pitch shift so it can adjust the spectral
      envelope independently of the fundamental frequency.
    - EQ runs after tonal shaping to fine-tune the final timbre.
    - Reverb runs last because its reflections should carry the fully
      processed signal, not the raw one.

Usage:
    from audio_fx import apply_effects, AudioFXConfig

    config = AudioFXConfig(pitch_semitones=2.0, bass_db=3.0)
    processed = apply_effects(pcm_float32, sample_rate=22050, config=config)

Thread safety:
    All functions are stateless and operate on input arrays without side
    effects. Safe to call from any thread.

Performance:
    Typical processing time for 5 seconds of 22050 Hz audio:
    - No effects (bypass): <0.01 ms (no copy)
    - All effects active: ~15-40 ms (depending on audio length)
    The bypass check ensures zero overhead when all parameters are defaults.

v1.1: Initial implementation.
"""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioFXConfig:
    """Configuration for the audio effects chain.

    All parameters have neutral defaults that produce no audible change.
    When all values are at their defaults, apply_effects() returns the
    input array unchanged (zero-copy bypass).

    Attributes:
        pitch_semitones: Pitch shift in semitones. Range: -6.0 to +6.0.
            Positive values raise pitch, negative values lower it.
            Default 0.0 (no shift).
        formant_shift: Formant (vocal tract resonance) shift factor.
            Range: 0.7 to 1.4. Values < 1.0 deepen the voice (longer
            vocal tract), values > 1.0 raise formants (shorter vocal
            tract). Default 1.0 (no shift).
        bass_db: Bass shelf gain in decibels at 200 Hz.
            Range: -12.0 to +12.0. Default 0.0 (flat).
        treble_db: Treble shelf gain in decibels at 3000 Hz.
            Range: -12.0 to +12.0. Default 0.0 (flat).
        reverb_mix: Reverb wet/dry mix. Range: 0.0 to 0.5.
            0.0 = fully dry (no reverb), 0.5 = equal wet and dry.
            Default 0.0 (no reverb).
    """

    pitch_semitones: float = 0.0
    formant_shift: float = 1.0
    bass_db: float = 0.0
    treble_db: float = 0.0
    reverb_mix: float = 0.0

    @property
    def is_bypass(self) -> bool:
        """True if all parameters are at their neutral defaults.

        When True, apply_effects() returns the input unchanged.
        Uses tolerance-based comparison to guard against float
        precision issues from TOML round-trips or Spinbox widgets.
        """
        eps = 1e-6
        return (
            abs(self.pitch_semitones) < eps
            and abs(self.formant_shift - 1.0) < eps
            and abs(self.bass_db) < eps
            and abs(self.treble_db) < eps
            and abs(self.reverb_mix) < eps
        )

    def to_cache_suffix(self) -> str:
        """Return a string encoding non-default params for cache key use.

        Returns an empty string when all params are defaults (bypass),
        so the cache key is unchanged for unprocessed audio.

        Returns:
            String like "fx:p2.0f0.9b3.0t-2.0r0.2" or "" if bypass.
        """
        if self.is_bypass:
            return ""
        parts: list[str] = []
        if self.pitch_semitones != 0.0:
            parts.append(f"p{self.pitch_semitones:.1f}")
        if self.formant_shift != 1.0:
            parts.append(f"f{self.formant_shift:.2f}")
        if self.bass_db != 0.0:
            parts.append(f"b{self.bass_db:.1f}")
        if self.treble_db != 0.0:
            parts.append(f"t{self.treble_db:.1f}")
        if self.reverb_mix != 0.0:
            parts.append(f"r{self.reverb_mix:.2f}")
        return "fx:" + "".join(parts)


# ---------------------------------------------------------------------------
# Individual effects
# ---------------------------------------------------------------------------


def pitch_shift(
    audio: np.ndarray, sample_rate: int, semitones: float,
) -> np.ndarray:
    """Shift pitch by the given number of semitones using a phase vocoder.

    Uses STFT-based time stretching followed by linear interpolation
    resampling. This preserves duration while changing pitch.

    The phase vocoder works by:
    1. Computing the STFT of the input.
    2. Stretching/compressing the STFT in time (changing tempo without
       changing pitch).
    3. Reconstructing via inverse STFT with overlap-add.
    4. Resampling the result to restore original duration while
       shifting pitch.

    Args:
        audio: 1-D float32 PCM array.
        sample_rate: Sample rate in Hz (used for documentation, not
            directly in the algorithm since we work in sample domain).
        semitones: Pitch shift in semitones (-6.0 to +6.0).

    Returns:
        Pitch-shifted float32 PCM array of the same length as input.
    """
    if semitones == 0.0 or len(audio) < 256:
        return audio

    # Pitch ratio: +12 semitones = 2x frequency
    ratio = 2.0 ** (semitones / 12.0)

    # Phase vocoder parameters
    # Window size must be power of 2 for FFT efficiency.
    # 2048 at 22050 Hz gives ~93ms windows -- good for speech.
    win_size = 2048
    hop_size = win_size // 4  # 75% overlap for quality
    # To raise pitch: first time-stretch to make audio LONGER (slower),
    # then resample to original length which compresses waveform cycles
    # (raising frequency). The stretch factor is the pitch ratio itself.
    stretch_factor = ratio

    # Step 1: Time-stretch via phase vocoder (change duration, keep pitch)
    stretched = _phase_vocoder(audio, stretch_factor, win_size, hop_size)

    # Step 2: Resample to original length (changes pitch, restores duration)
    target_len = len(audio)
    if len(stretched) == target_len:
        return stretched

    # Linear interpolation resampling
    indices = np.linspace(0, len(stretched) - 1, target_len)
    result = np.interp(indices, np.arange(len(stretched)), stretched)

    return result.astype(np.float32)


def _phase_vocoder(
    audio: np.ndarray,
    stretch_factor: float,
    win_size: int,
    hop_size: int,
) -> np.ndarray:
    """Phase vocoder for time-stretching without pitch change.

    Implements the classic phase vocoder algorithm:
    1. Compute STFT frames.
    2. Resample frame indices (time axis) by stretch_factor.
    3. Accumulate phase increments to maintain phase coherence.
    4. Reconstruct via inverse FFT and overlap-add.

    Args:
        audio: 1-D float32 PCM input.
        stretch_factor: >1.0 = slower (longer), <1.0 = faster (shorter).
        win_size: FFT window size (must be even).
        hop_size: Analysis hop size in samples.

    Returns:
        Time-stretched float32 PCM array.
    """
    # Pad audio to complete the last window
    pad_len = win_size - (len(audio) % hop_size)
    audio_padded = np.pad(audio, (0, pad_len), mode="constant")

    # Hann window for analysis and synthesis
    window = np.hanning(win_size).astype(np.float32)

    # STFT: compute all frames
    n_frames = 1 + (len(audio_padded) - win_size) // hop_size
    if n_frames < 2:
        return audio

    # Pre-compute all STFT frames
    stft = np.zeros((n_frames, win_size // 2 + 1), dtype=np.complex64)
    for i in range(n_frames):
        start = i * hop_size
        frame = audio_padded[start:start + win_size] * window
        stft[i] = np.fft.rfft(frame)

    # Synthesis parameters
    syn_hop = hop_size  # synthesis hop = analysis hop (overlap-add)
    # Number of output frames after stretching
    n_out_frames = int(np.ceil(n_frames * stretch_factor))
    if n_out_frames < 2:
        return audio

    # Output buffer
    out_len = (n_out_frames - 1) * syn_hop + win_size
    output = np.zeros(out_len, dtype=np.float32)
    window_sum = np.zeros(out_len, dtype=np.float32)

    # Phase accumulator
    phase_advance = np.linspace(0, np.pi * hop_size, win_size // 2 + 1)
    phase_accum = np.angle(stft[0]) if n_frames > 0 else np.zeros(win_size // 2 + 1)

    for i in range(n_out_frames):
        # Map output frame index back to (fractional) input frame index
        src_idx = i / stretch_factor
        src_frame = int(src_idx)
        frac = src_idx - src_frame

        # Clamp to valid range
        src_frame = min(src_frame, n_frames - 1)
        next_frame = min(src_frame + 1, n_frames - 1)

        # Interpolate magnitude, advance phase
        mag0 = np.abs(stft[src_frame])
        mag1 = np.abs(stft[next_frame])
        mag = (1.0 - frac) * mag0 + frac * mag1

        # Phase difference between consecutive source frames
        if src_frame < n_frames - 1:
            dp = np.angle(stft[next_frame]) - np.angle(stft[src_frame])
            dp -= phase_advance
            # Wrap to [-pi, pi]
            dp = dp - 2.0 * np.pi * np.round(dp / (2.0 * np.pi))
            dp += phase_advance
        else:
            dp = phase_advance

        if i > 0:
            phase_accum += dp
        # else: keep initial phase from frame 0

        # Reconstruct complex spectrum
        spectrum = mag * np.exp(1j * phase_accum)

        # Inverse FFT
        frame = np.fft.irfft(spectrum, n=win_size).astype(np.float32)
        frame *= window

        # Overlap-add
        start = i * syn_hop
        end = start + win_size
        output[start:end] += frame
        window_sum[start:end] += window * window

    # Normalize by window sum to compensate overlap
    nonzero = window_sum > 1e-8
    output[nonzero] /= window_sum[nonzero]

    return output


def formant_shift(
    audio: np.ndarray, sample_rate: int, shift_factor: float,
) -> np.ndarray:
    """Shift formants (vocal tract resonances) without changing pitch.

    Uses cepstral envelope manipulation:
    1. Compute the cepstrum (inverse FFT of log-magnitude spectrum).
    2. Separate the spectral envelope (low quefrency) from the fine
       structure (high quefrency, which encodes pitch).
    3. Stretch/compress the envelope by shift_factor.
    4. Recombine and reconstruct.

    Values < 1.0 shift formants down (deeper, larger vocal tract).
    Values > 1.0 shift formants up (higher, smaller vocal tract).

    Args:
        audio: 1-D float32 PCM array.
        sample_rate: Sample rate in Hz.
        shift_factor: Formant shift factor (0.7 to 1.4).

    Returns:
        Formant-shifted float32 PCM array of the same length.
    """
    if shift_factor == 1.0 or len(audio) < 256:
        return audio

    win_size = 2048
    hop_size = win_size // 4
    n_fft = win_size

    # Pad audio
    pad_len = win_size - (len(audio) % hop_size)
    audio_padded = np.pad(audio, (0, pad_len), mode="constant")

    window = np.hanning(win_size).astype(np.float32)

    n_frames = 1 + (len(audio_padded) - win_size) // hop_size
    if n_frames < 1:
        return audio

    # Output buffer
    out_len = len(audio_padded)
    output = np.zeros(out_len, dtype=np.float32)
    window_sum = np.zeros(out_len, dtype=np.float32)

    # Cepstral lifter cutoff: separates envelope from fine structure.
    # For 22050 Hz, a cutoff of ~30 captures formant information
    # without pitch harmonics. Scale with sample rate.
    lifter_cutoff = max(20, int(sample_rate / 700))

    half_n = n_fft // 2 + 1

    for i in range(n_frames):
        start = i * hop_size
        frame = audio_padded[start:start + win_size] * window

        # Forward FFT
        spectrum = np.fft.rfft(frame, n=n_fft)
        log_mag = np.log(np.abs(spectrum) + 1e-10)
        phase = np.angle(spectrum)

        # Compute real cepstrum (inverse FFT of log magnitude)
        cepstrum = np.fft.irfft(log_mag, n=n_fft)

        # Extract spectral envelope (low quefrency)
        envelope_ceps = np.zeros_like(cepstrum)
        envelope_ceps[:lifter_cutoff] = cepstrum[:lifter_cutoff]
        envelope_ceps[-lifter_cutoff + 1:] = cepstrum[-lifter_cutoff + 1:]

        # Convert envelope back to frequency domain
        envelope_log = np.fft.rfft(envelope_ceps, n=n_fft).real

        # Fine structure = original - envelope
        fine_log = log_mag - envelope_log

        # Stretch the envelope by resampling in frequency
        freq_indices = np.arange(half_n)
        shifted_indices = freq_indices / shift_factor
        # Clamp to valid range
        shifted_indices = np.clip(shifted_indices, 0, half_n - 1)
        shifted_envelope = np.interp(shifted_indices, freq_indices, envelope_log)

        # Recombine: shifted envelope + original fine structure
        new_log_mag = shifted_envelope + fine_log
        new_mag = np.exp(new_log_mag)

        # Reconstruct
        new_spectrum = new_mag * np.exp(1j * phase)
        new_frame = np.fft.irfft(new_spectrum, n=n_fft).astype(np.float32)
        new_frame = new_frame[:win_size] * window

        # Overlap-add
        output[start:start + win_size] += new_frame
        window_sum[start:start + win_size] += window * window

    # Normalize
    nonzero = window_sum > 1e-8
    output[nonzero] /= window_sum[nonzero]

    return output[:len(audio)]


def eq_shelf(
    audio: np.ndarray,
    sample_rate: int,
    bass_db: float = 0.0,
    treble_db: float = 0.0,
    bass_freq: float = 200.0,
    treble_freq: float = 3000.0,
) -> np.ndarray:
    """Apply bass and treble shelf EQ using FFT filtering.

    Processes audio in overlapping blocks to avoid edge artifacts.
    Bass shelf applies gain below bass_freq with a smooth rolloff.
    Treble shelf applies gain above treble_freq with a smooth rolloff.

    Both shelves use a sigmoid-shaped transition curve (0.5 octave
    width) for a smooth, natural-sounding response.

    Args:
        audio: 1-D float32 PCM array.
        sample_rate: Sample rate in Hz.
        bass_db: Bass shelf gain in dB (-12 to +12). 0 = flat.
        treble_db: Treble shelf gain in dB (-12 to +12). 0 = flat.
        bass_freq: Bass shelf corner frequency in Hz. Default 200.
        treble_freq: Treble shelf corner frequency in Hz. Default 3000.

    Returns:
        EQ'd float32 PCM array of the same length.
    """
    if bass_db == 0.0 and treble_db == 0.0:
        return audio
    if len(audio) < 64:
        return audio

    # Process in blocks for efficiency (FFT is O(n log n))
    block_size = 4096
    hop = block_size // 2
    n_fft = block_size

    # Build the gain curve once (it depends only on sample rate and params)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    gain = np.ones(len(freqs), dtype=np.float32)

    if bass_db != 0.0:
        bass_linear = 10.0 ** (bass_db / 20.0)
        # Sigmoid transition: full gain below bass_freq, unity above
        # Width parameter controls transition steepness (in log-freq space)
        log_f = np.log2(np.maximum(freqs, 1.0))
        log_bass = np.log2(bass_freq)
        # Smooth sigmoid: gain transitions over ~0.5 octave
        sigmoid = 1.0 / (1.0 + np.exp(8.0 * (log_f - log_bass)))
        gain *= 1.0 + (bass_linear - 1.0) * sigmoid

    if treble_db != 0.0:
        treble_linear = 10.0 ** (treble_db / 20.0)
        log_f = np.log2(np.maximum(freqs, 1.0))
        log_treble = np.log2(treble_freq)
        sigmoid = 1.0 / (1.0 + np.exp(-8.0 * (log_f - log_treble)))
        gain *= 1.0 + (treble_linear - 1.0) * sigmoid

    # Overlap-add processing
    window = np.hanning(block_size).astype(np.float32)
    pad_len = block_size - (len(audio) % hop)
    audio_padded = np.pad(audio, (0, pad_len), mode="constant")

    output = np.zeros(len(audio_padded), dtype=np.float32)
    window_sum = np.zeros(len(audio_padded), dtype=np.float32)

    n_blocks = 1 + (len(audio_padded) - block_size) // hop
    for i in range(n_blocks):
        start = i * hop
        frame = audio_padded[start:start + block_size] * window

        spectrum = np.fft.rfft(frame)
        spectrum *= gain
        frame_out = np.fft.irfft(spectrum, n=block_size).astype(np.float32)
        frame_out *= window

        output[start:start + block_size] += frame_out
        window_sum[start:start + block_size] += window * window

    nonzero = window_sum > 1e-8
    output[nonzero] /= window_sum[nonzero]

    return output[:len(audio)]


def reverb(
    audio: np.ndarray, sample_rate: int, mix: float,
) -> np.ndarray:
    """Add reverb using a multi-tap comb filter network.

    Simulates a small room using four parallel comb filters with
    prime-number delay lengths (to avoid resonance buildup) fed
    through two allpass filters for diffusion. The result is mixed
    with the dry signal according to the mix parameter.

    This is a Schroeder reverberator -- simple but effective for
    adding spaciousness to speech without heavy computation.

    Args:
        audio: 1-D float32 PCM array.
        sample_rate: Sample rate in Hz.
        mix: Wet/dry mix ratio (0.0 to 0.5). 0.0 = fully dry.

    Returns:
        Reverb'd float32 PCM array of the same length.
    """
    if mix <= 0.0 or len(audio) < 64:
        return audio

    # Clamp mix to safe range
    mix = min(mix, 0.5)

    # Comb filter delays in milliseconds (prime-ish values to avoid
    # metallic resonance). Scaled from a ~20ms-50ms range suitable
    # for a small/medium room effect on speech.
    comb_delays_ms = [23.0, 29.0, 37.0, 43.0]
    comb_feedback = 0.6  # Lower feedback = shorter decay, less muddy

    # Allpass filter delays for diffusion
    allpass_delays_ms = [5.0, 1.7]
    allpass_feedback = 0.5

    # Convert delays to samples
    comb_delays = [int(d * sample_rate / 1000.0) for d in comb_delays_ms]
    allpass_delays = [int(d * sample_rate / 1000.0) for d in allpass_delays_ms]

    n = len(audio)

    # Run parallel comb filters and sum
    wet = np.zeros(n, dtype=np.float32)

    for delay in comb_delays:
        if delay <= 0 or delay >= n:
            continue
        buf = np.zeros(n, dtype=np.float32)
        for i in range(n):
            feedback_sample = buf[i - delay] if i >= delay else 0.0
            buf[i] = audio[i] + comb_feedback * feedback_sample
        wet += buf

    # Normalize comb filter sum
    n_combs = len([d for d in comb_delays if 0 < d < n])
    if n_combs > 0:
        wet /= n_combs

    # Series allpass filters for diffusion
    for delay in allpass_delays:
        if delay <= 0 or delay >= n:
            continue
        buf = np.zeros(n, dtype=np.float32)
        for i in range(n):
            delayed = buf[i - delay] if i >= delay else 0.0
            buf[i] = -allpass_feedback * wet[i] + delayed
            # The allpass output replaces wet for the next stage
            if i >= delay:
                buf[i] += allpass_feedback * buf[i - delay]
        wet = buf + allpass_feedback * wet

    # Mix dry and wet
    output = (1.0 - mix) * audio + mix * wet

    return output.astype(np.float32)


def reverb_vectorized(
    audio: np.ndarray, sample_rate: int, mix: float,
) -> np.ndarray:
    """Add reverb using FFT-based convolution with a synthetic impulse response.

    This is a vectorized alternative to the sample-by-sample comb filter.
    Generates a synthetic impulse response and convolves via FFT. Much
    faster for longer audio (>1 second).

    Args:
        audio: 1-D float32 PCM array.
        sample_rate: Sample rate in Hz.
        mix: Wet/dry mix ratio (0.0 to 0.5).

    Returns:
        Reverb'd float32 PCM array of the same length as input.
    """
    if mix <= 0.0 or len(audio) < 64:
        return audio

    mix = min(mix, 0.5)

    # Generate synthetic impulse response: exponentially decaying noise.
    # RT60 (time for reverb to decay 60 dB) scales with mix amount.
    # Lower mix -> shorter reverb tail -> less muddy speech.
    rt60_ms = 150.0 + mix * 500.0  # 150ms at mix=0, 400ms at mix=0.5
    ir_len = int(rt60_ms * sample_rate / 1000.0)
    ir_len = min(ir_len, sample_rate)  # Cap at 1 second

    # Create impulse response: filtered noise with exponential decay
    rng = np.random.RandomState(42)  # deterministic for reproducibility
    ir = rng.randn(ir_len).astype(np.float32)

    # Exponential decay envelope
    decay = np.exp(-6.9 * np.arange(ir_len) / ir_len)  # -60 dB at end
    ir *= decay.astype(np.float32)

    # Lowpass the IR slightly to soften the reverb (remove harsh HF)
    # Simple method: running average over a small window
    kernel_size = max(3, int(sample_rate / 8000))
    if kernel_size > 1:
        kernel = np.ones(kernel_size, dtype=np.float32) / kernel_size
        ir = np.convolve(ir, kernel, mode="same")

    # Normalize IR energy
    ir_energy = np.sqrt(np.sum(ir * ir))
    if ir_energy > 1e-8:
        ir /= ir_energy

    # FFT convolution for speed
    fft_size = 1
    while fft_size < len(audio) + ir_len - 1:
        fft_size *= 2

    audio_fft = np.fft.rfft(audio, n=fft_size)
    ir_fft = np.fft.rfft(ir, n=fft_size)
    wet = np.fft.irfft(audio_fft * ir_fft, n=fft_size).astype(np.float32)

    # Trim to original length
    wet = wet[:len(audio)]

    # Match wet level to dry level for perceptual consistency
    dry_rms = np.sqrt(np.mean(audio * audio) + 1e-10)
    wet_rms = np.sqrt(np.mean(wet * wet) + 1e-10)
    if wet_rms > 1e-10:
        wet *= dry_rms / wet_rms

    # Mix
    output = (1.0 - mix) * audio + mix * wet

    return output.astype(np.float32)


# ---------------------------------------------------------------------------
# Effect chain
# ---------------------------------------------------------------------------


def apply_effects(
    audio: np.ndarray,
    sample_rate: int,
    config: AudioFXConfig,
) -> np.ndarray:
    """Apply the full audio effects chain to float32 PCM audio.

    Processes effects in the optimal order for speech audio:
        1. Pitch shift
        2. Formant shift
        3. Bass + Treble EQ (combined in one call)
        4. Reverb

    If all config parameters are at their neutral defaults, returns the
    input array unchanged with zero overhead (no copy, no processing).

    Args:
        audio: 1-D float32 PCM array. Values should be in [-1.0, 1.0]
            range (Piper VITS output). Will not be modified in place.
        sample_rate: Audio sample rate in Hz (typically 22050 for Piper).
        config: AudioFXConfig with effect parameters.

    Returns:
        Processed float32 PCM array. Same length as input (except for
        minor rounding differences from pitch shift, which are
        compensated by resampling).

    Raises:
        ValueError: If audio is not 1-D or has zero length.
    """
    # Fast bypass: no effects configured
    if config.is_bypass:
        return audio

    # Validate input
    if audio.ndim != 1:
        raise ValueError(
            f"audio_fx expects 1-D array, got {audio.ndim}-D"
        )
    if len(audio) == 0:
        return audio

    import time
    t0 = time.monotonic()
    result = audio

    # 1. Pitch shift
    if config.pitch_semitones != 0.0:
        result = pitch_shift(result, sample_rate, config.pitch_semitones)

    # 2. Formant shift
    if config.formant_shift != 1.0:
        result = formant_shift(result, sample_rate, config.formant_shift)

    # 3. Bass + Treble EQ (single pass)
    if config.bass_db != 0.0 or config.treble_db != 0.0:
        result = eq_shelf(
            result, sample_rate,
            bass_db=config.bass_db,
            treble_db=config.treble_db,
        )

    # 4. Reverb (use vectorized FFT convolution for performance)
    if config.reverb_mix > 0.0:
        result = reverb_vectorized(result, sample_rate, config.reverb_mix)

    # 5. Loudness normalization: restore RMS to match the original signal.
    # Each effect in the chain can alter the amplitude (phase vocoder
    # overlap-add, cepstral manipulation, EQ cuts, reverb wet/dry mix).
    # Without this step the output is noticeably quieter.
    orig_rms = np.sqrt(np.mean(audio * audio) + 1e-10)
    result_rms = np.sqrt(np.mean(result * result) + 1e-10)
    rms_gain = 1.0
    if result_rms > 1e-10 and orig_rms > 1e-10:
        rms_gain = orig_rms / result_rms
        # Clamp gain to avoid extreme amplification from near-silence
        rms_gain = min(rms_gain, 4.0)
        result = result * rms_gain
        # Clip to [-1, 1] to prevent distortion from boosted peaks
        result = np.clip(result, -1.0, 1.0)

    elapsed_ms = (time.monotonic() - t0) * 1000
    audio_duration = len(audio) / sample_rate

    logger.debug(
        "Audio FX applied in %.1f ms (%.1fs audio at %d Hz, "
        "loudness gain=%.2fx): "
        "pitch=%.1f semi, formant=%.2f, bass=%.1f dB, "
        "treble=%.1f dB, reverb=%.2f",
        elapsed_ms,
        audio_duration,
        sample_rate,
        rms_gain,
        config.pitch_semitones,
        config.formant_shift,
        config.bass_db,
        config.treble_db,
        config.reverb_mix,
    )

    return result

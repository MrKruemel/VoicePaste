# Architecture Decision Record: v0.6 -- TTS, Overlay UI, Context Menu

**Date**: 2026-02-18
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.5.0
**Target Version**: 0.6.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Component Diagram](#2-component-diagram)
3. [TTS Abstraction Design](#3-tts-abstraction-design)
4. [Threading Model Updates](#4-threading-model-updates)
5. [State Machine Extensions](#5-state-machine-extensions)
6. [Overlay Window Technical Approach](#6-overlay-window-technical-approach)
7. [Context Menu Registration Approach](#7-context-menu-registration-approach)
8. [Config Schema Extensions](#8-config-schema-extensions)
9. [Build Impact Analysis](#9-build-impact-analysis)
10. [Implementation Plan and File Changes](#10-implementation-plan-and-file-changes)
11. [Risk Mitigation Strategies](#11-risk-mitigation-strategies)
12. [Key Trade-offs Summary](#12-key-trade-offs-summary)
13. [Phased Delivery Plan](#13-phased-delivery-plan)

---

## 1. Executive Summary

v0.6 adds three major capabilities to Voice Paste:

1. **Text-to-Speech (TTS)**: Speak any text aloud via ElevenLabs API (primary) or
   future local providers. Integrates with existing summarization pipeline and enables
   new "read aloud" workflows.

2. **Floating Overlay UI**: A small always-on-top toolbar with buttons for Record,
   AI Query, Clipboard-to-TTS, and Ask AI+TTS. Supplements (does not replace) the
   existing system tray and hotkey workflow.

3. **Windows Context Menu Integration**: Right-click "Read aloud with Voice Paste" on
   selected text in Explorer, browsers, and text editors. Communicates with the
   running application via a named pipe.

These features are additive. The existing hotkey+tray workflow continues to work
unchanged. Each feature can be independently enabled or disabled via config.

---

## 2. Component Diagram

### v0.6 Architecture (additions in brackets)

```
+-------------------------------------------------------------------------+
|                        main.py (Entry Point)                             |
+-------------------------------------------------------------------------+
|                                                                          |
|  +------------------+     +------------------+                           |
|  |   Config Loader  |     |   Logger Setup   |                           |
|  |  (config.toml)   |     | (voice-paste.log)|                           |
|  +------------------+     +------------------+                           |
|                                                                          |
|  +------------------+     +------------------+                           |
|  |  Hotkey Manager  |---->|  State Machine   |                           |
|  | (keyboard lib)   |     |  (AppState enum) |                           |
|  +------------------+     +-----+------------+                           |
|                                 |                                        |
|                     +-----------+-----------+                            |
|                     |                       |                            |
|               +-----v------+         +------v-------+                    |
|               |   Audio     |         |   Paste      |                   |
|               |   Recorder  |         |   Manager    |                   |
|               | (sounddevice)|        | (clipboard + |                   |
|               +-----+------+         |  Ctrl+V sim) |                   |
|                     |                 +--------------+                    |
|               +-----v------+                                             |
|               | STT Backend |  <-- Factory + Protocol                    |
|               +-----+------+                                             |
|                     |                                                    |
|          +----------+----------+                                         |
|          |                     |                                         |
|   +------v-------+   +--------v--------+                                 |
|   | Cloud Whisper|   | Local Whisper   |                                 |
|   | (OpenAI API) |   | (faster-whisper)|                                 |
|   +--------------+   +-----------------+                                 |
|                                                                          |
|               +-----------------+                                        |
|               | Summarizer      |  <-- Factory + Protocol                |
|               +-------+---------+                                        |
|                       |                                                  |
|            +----------+----------+                                       |
|            |          |          |                                        |
|   +--------v--+  +----v----+  +--v--------+                              |
|   | OpenAI    |  |OpenRouter|  | Ollama   |                              |
|   +-----------+  +----------+  +----------+                              |
|                                                                          |
|  [NEW] +------------------+                                              |
|        | TTS Backend      |  <-- Protocol + Factory                      |
|        +-------+----------+                                              |
|                |                                                         |
|      +---------+---------+                                               |
|      |                   |                                               |
|  +---v-----------+  +----v-----------+                                   |
|  | ElevenLabs   |  | (Future: Edge  |                                   |
|  | Cloud TTS    |  |  TTS / local)  |                                   |
|  +---+-----------+  +----------------+                                   |
|      |                                                                   |
|  +---v-----------+                                                       |
|  | Audio Playback|  (sounddevice output stream)                          |
|  +---------------+                                                       |
|                                                                          |
|  [NEW] +------------------+                                              |
|        | Overlay Window   |  (tkinter Toplevel, own thread)              |
|        | - Record btn     |                                              |
|        | - AI Query btn   |                                              |
|        | - Clipboard->TTS |                                              |
|        | - Ask AI+TTS btn |                                              |
|        +------------------+                                              |
|                                                                          |
|  [NEW] +------------------+                                              |
|        | Named Pipe Server|  (IPC for context menu)                      |
|        | \\.\pipe\VoicePaste                                             |
|        +------------------+                                              |
|                                                                          |
|  [NEW] +------------------+                                              |
|        | Context Menu     |  (registry installer + uninstaller)          |
|        | Registration     |                                              |
|        +------------------+                                              |
|                                                                          |
|  +------------------+     +------------------+                           |
|  |  Settings Dialog |     |  System Tray     |                           |
|  |  (tkinter, v0.3+)|    |  (pystray)       |                           |
|  +------------------+     +------------------+                           |
|                                                                          |
|  +------------------+     +------------------+                           |
|  |  Keyring Store   |     | Notifications    |                           |
|  +------------------+     +------------------+                           |
+--------------------------------------------------------------------------+
```

### Data Flow for New Features

```
TTS Playback Flow:
  Text (from clipboard / summarizer / LLM response)
    --> TTSBackend.synthesize(text) --> bytes (MP3/PCM)
    --> AudioPlayback.play(bytes)   --> speaker output
    --> SPEAKING state              --> IDLE when done

Context Menu Flow:
  User selects text in any app --> right-click "Read aloud"
    --> Windows launches: VoicePaste.exe --tts-pipe "<text>"
    --> Cli stub connects to \\.\pipe\VoicePaste
    --> Sends JSON command: {"action": "tts", "text": "..."}
    --> Running instance receives --> TTS pipeline plays audio

Overlay Button Flows:
  [Record]           --> same as Ctrl+Alt+R hotkey
  [AI Query]         --> same as Ctrl+Alt+A hotkey
  [Clipboard->TTS]   --> read clipboard text --> TTS pipeline
  [Ask AI+TTS]       --> Ctrl+Alt+A flow, but output goes to TTS instead of paste
```

---

## 3. TTS Abstraction Design

### 3.1 Decision: Protocol-based TTS Backend

Following the established STT and Summarizer patterns, TTS uses a Python Protocol
for backend abstraction.

**New file: `src/tts.py`**

```python
"""Text-to-Speech backend abstraction and implementations.

Provides a Protocol for TTS backends, an ElevenLabs cloud implementation,
and a factory function for backend selection.
"""

import io
import logging
import threading
from typing import Protocol, Iterator, Optional

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """Raised when text-to-speech synthesis fails."""
    pass


class TTSBackend(Protocol):
    """Protocol for text-to-speech backends.

    Implementations must convert text to audio bytes.
    """

    def synthesize(self, text: str, language: str = "de") -> bytes:
        """Convert text to audio bytes.

        Args:
            text: Text to speak.
            language: Language hint for the TTS engine.

        Returns:
            Audio data as bytes (format depends on implementation;
            MP3 for ElevenLabs, PCM for local).

        Raises:
            TTSError: If synthesis fails.
        """
        ...

    def synthesize_stream(
        self, text: str, language: str = "de"
    ) -> Iterator[bytes]:
        """Stream audio chunks for long text (optional).

        Default implementation calls synthesize() and yields
        the full result as a single chunk.

        Args:
            text: Text to speak.
            language: Language hint.

        Yields:
            Audio data chunks.

        Raises:
            TTSError: If synthesis fails.
        """
        ...

    def stop(self) -> None:
        """Stop any in-progress synthesis and playback.

        Implementations must handle being called when nothing is playing.
        """
        ...


class ElevenLabsTTS:
    """ElevenLabs cloud TTS implementation.

    Uses the elevenlabs Python SDK for synthesis and returns raw audio
    bytes. Audio playback is handled separately by the AudioPlayback
    module so that playback can be cancelled independently of synthesis.

    The elevenlabs SDK is imported lazily to keep it optional.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = "pFZP5JQG7iQjIQuC4Bku",
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "mp3_44100_128",
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._output_format = output_format
        self._client = None  # Lazy init
        self._cancel = threading.Event()

    def _get_client(self):
        """Lazily initialize the ElevenLabs client."""
        if self._client is None:
            try:
                from elevenlabs.client import ElevenLabs
                self._client = ElevenLabs(api_key=self._api_key)
            except ImportError:
                raise TTSError(
                    "elevenlabs package not installed. "
                    "Install with: pip install elevenlabs"
                )
        return self._client

    def synthesize(self, text: str, language: str = "de") -> bytes:
        """Synthesize text to MP3 audio bytes via ElevenLabs API."""
        self._cancel.clear()

        client = self._get_client()
        try:
            audio_iter = client.text_to_speech.convert(
                text=text,
                voice_id=self._voice_id,
                model_id=self._model_id,
                output_format=self._output_format,
            )
            # Collect all chunks into a single bytes object
            chunks = []
            for chunk in audio_iter:
                if self._cancel.is_set():
                    raise TTSError("TTS synthesis cancelled.")
                chunks.append(chunk)

            return b"".join(chunks)

        except TTSError:
            raise
        except Exception as e:
            raise TTSError(f"ElevenLabs API error: {e}") from e

    def synthesize_stream(
        self, text: str, language: str = "de"
    ) -> Iterator[bytes]:
        """Stream audio chunks from ElevenLabs API."""
        self._cancel.clear()

        client = self._get_client()
        try:
            audio_iter = client.text_to_speech.convert(
                text=text,
                voice_id=self._voice_id,
                model_id=self._model_id,
                output_format=self._output_format,
            )
            for chunk in audio_iter:
                if self._cancel.is_set():
                    return
                yield chunk

        except Exception as e:
            raise TTSError(f"ElevenLabs streaming error: {e}") from e

    def stop(self) -> None:
        """Cancel any in-progress synthesis."""
        self._cancel.set()


def create_tts_backend(config: "AppConfig") -> "TTSBackend | None":
    """Factory function to create a TTS backend based on configuration.

    Args:
        config: Application configuration with TTS settings.

    Returns:
        A TTSBackend implementation, or None if TTS is not configured.
    """
    if not config.tts_enabled:
        logger.info("TTS disabled in configuration.")
        return None

    if config.tts_provider == "elevenlabs":
        api_key = config.tts_api_key
        if not api_key:
            logger.warning("No ElevenLabs API key configured for TTS.")
            return None

        return ElevenLabsTTS(
            api_key=api_key,
            voice_id=config.tts_voice_id,
            model_id=config.tts_model_id,
            output_format=config.tts_output_format,
        )

    logger.warning("Unknown TTS provider: %s", config.tts_provider)
    return None
```

### 3.2 Decision: Audio Playback via sounddevice (not elevenlabs.play)

**Rationale:**

The `elevenlabs.play()` helper internally uses either `mpv`, `ffmpeg`, or
`sounddevice`. It is a convenience wrapper with several problems:

1. **No cancel support**: Once `play()` starts, it blocks until done. There is no
   way to interrupt mid-playback.
2. **External dependency**: `mpv` or `ffmpeg` must be on PATH; not guaranteed in
   a PyInstaller bundle.
3. **No volume control**: Fixed system volume.
4. **Duplicate dependency**: We already bundle sounddevice for recording.

**Decision**: Use sounddevice's `OutputStream` for playback. Decode MP3 to PCM in
memory using the `miniaudio` library (pure C, small, PyInstaller-friendly) or
`pydub` (requires ffmpeg). Simplest path: Use `miniaudio` which is a single
compiled extension.

**New file: `src/audio_playback.py`**

```python
"""Audio playback for TTS output.

Plays MP3 or PCM audio through the default output device using sounddevice.
Supports cancellation mid-playback.

Uses miniaudio for MP3 decoding (small C library, no ffmpeg needed).
"""

import io
import logging
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Plays audio bytes through the system speakers.

    Supports MP3 and WAV formats. Uses miniaudio for decoding and
    sounddevice for output. Thread-safe; playback runs in a background
    thread and can be cancelled at any time.
    """

    def __init__(self) -> None:
        self._playing = False
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._playback_thread: Optional[threading.Thread] = None

    @property
    def is_playing(self) -> bool:
        """Whether audio is currently playing."""
        return self._playing

    def play_mp3(self, mp3_data: bytes) -> None:
        """Decode MP3 and play through speakers. Non-blocking.

        Spawns a background thread for playback.

        Args:
            mp3_data: MP3 audio bytes.
        """
        with self._lock:
            if self._playing:
                self.stop()

            self._cancel.clear()
            self._playing = True

            self._playback_thread = threading.Thread(
                target=self._play_mp3_worker,
                args=(mp3_data,),
                daemon=True,
                name="tts-playback",
            )
            self._playback_thread.start()

    def _play_mp3_worker(self, mp3_data: bytes) -> None:
        """Background thread: decode and play MP3 audio."""
        try:
            import miniaudio

            decoded = miniaudio.decode(mp3_data)
            samples = np.frombuffer(
                decoded.samples, dtype=np.int16
            ).reshape(-1, decoded.nchannels)

            # Play in chunks for cancel responsiveness
            chunk_size = decoded.sample_rate // 4  # 250ms chunks
            stream = sd.OutputStream(
                samplerate=decoded.sample_rate,
                channels=decoded.nchannels,
                dtype="int16",
            )
            stream.start()

            try:
                for i in range(0, len(samples), chunk_size):
                    if self._cancel.is_set():
                        logger.info("TTS playback cancelled.")
                        break
                    chunk = samples[i : i + chunk_size]
                    stream.write(chunk)
            finally:
                stream.stop()
                stream.close()

        except ImportError:
            logger.error(
                "miniaudio not installed. TTS playback unavailable. "
                "Install with: pip install miniaudio"
            )
        except Exception:
            logger.exception("TTS playback error.")
        finally:
            self._playing = False

    def stop(self) -> None:
        """Stop any in-progress playback."""
        self._cancel.set()
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=1.0)
        self._playing = False

    def play_stream(self, audio_chunks, sample_rate: int = 44100,
                    channels: int = 1) -> None:
        """Play a stream of audio chunks in real-time.

        For streaming TTS where chunks arrive as the API generates them.

        Args:
            audio_chunks: Iterator of MP3 chunk bytes.
            sample_rate: Sample rate of the decoded audio.
            channels: Number of audio channels.
        """
        # Implementation deferred to v0.6.1 -- streaming adds complexity
        # (partial MP3 frame decoding) that is not needed for MVP.
        # For MVP, collect all chunks and play as a single buffer.
        all_data = b"".join(audio_chunks)
        self.play_mp3(all_data)
```

### 3.3 Decision: ElevenLabs as Primary (not only) TTS Provider

**Rationale:**

| Option | Quality | Cost | Latency | Binary Impact | Offline |
|--------|---------|------|---------|---------------|---------|
| **ElevenLabs** | Excellent | $0.15/1k chars (Starter) | 300ms-1s | +~2 MB (SDK) | No |
| Azure Cognitive TTS | Very Good | $4/1M chars | 200-500ms | +~5 MB (SDK) | No |
| pyttsx3 (SAPI5) | Poor-Fair | Free | ~50ms | +~0.1 MB | Yes |
| Edge TTS (edge-tts) | Good | Free* | 500ms-2s | +~1 MB | No |
| Coqui/XTTS local | Good | Free | 2-10s | +500 MB | Yes |

**Decision**: ElevenLabs as primary cloud provider. The Protocol pattern allows
adding edge-tts (free, decent quality) or pyttsx3 (offline, low quality) later
without changing the core architecture.

**Why not pyttsx3 as default?** The Windows SAPI5 voices (David, Zira) sound
robotic in German. ElevenLabs provides natural-sounding multilingual voices. For
a tool focused on German, voice quality matters.

**Why not edge-tts?** It uses an undocumented Microsoft API endpoint that may
break without notice. Not suitable as a primary backend, but could be a secondary
free option in v0.7.

**Future local option (v0.7+)**: pyttsx3 as a zero-cost, zero-latency fallback
for users who want offline TTS. Quality warning in UI.

### 3.4 Anti-feedback Loop: TTS During Recording

**Problem**: If TTS is playing through speakers while the microphone is recording,
the TTS audio will be captured and transcribed, creating a feedback loop.

**Solution**: The state machine prevents this by design:

1. TTS playback sets state to SPEAKING.
2. The SPEAKING state blocks transitions to RECORDING (hotkey press is rejected
   with a notification: "Stop playback first").
3. The user can cancel TTS playback (Escape or overlay Stop button), which
   returns to IDLE, then start recording.
4. Alternatively: mute the microphone during TTS playback (complex, fragile --
   rejected in favor of the simpler state guard).

---

## 4. Threading Model Updates

### Current Threading Model (v0.5)

```
Main Thread:     pystray event loop (system tray, Win32 message pump)
Thread 1:        keyboard hotkey listener (daemon)
Thread 2:        Pipeline worker (record->STT->summarize->paste, per session)
Thread 3:        Settings dialog tkinter loop (on demand)
```

### v0.6 Threading Model

```
Main Thread:     pystray event loop (system tray, Win32 message pump)
Thread 1:        keyboard hotkey listener (daemon)
Thread 2:        Pipeline worker (record->STT->summarize->paste, per session)
Thread 3:        Settings dialog tkinter loop (on demand, singleton)
Thread 4: [NEW]  Overlay window tkinter loop (persistent, own Tk root)
Thread 5: [NEW]  TTS playback (daemon, spawned per playback, via AudioPlayer)
Thread 6: [NEW]  Named pipe IPC server (daemon, persistent)
```

### Key Threading Decisions

**1. Overlay window gets its own tkinter thread (Thread 4)**

The overlay is a persistent floating window. It cannot share a Tk root with the
Settings dialog because:
- The Settings dialog is modal and transient (created/destroyed on demand).
- The overlay is persistent for the entire application lifetime.
- Two Tk roots on the same thread would require manual mainloop multiplexing.

The overlay thread creates its own `tk.Tk()` root (hidden, `withdraw()`), builds
a `tk.Toplevel` for the overlay, and runs `mainloop()`. When the overlay is
hidden/shown, it uses `withdraw()`/`deiconify()` -- no thread restarts needed.

**2. TTS playback thread is per-session (Thread 5)**

Same pattern as the pipeline worker: spawned as a daemon thread when TTS starts,
exits when playback finishes or is cancelled. `AudioPlayer` manages the lifecycle.

**3. Named pipe server is persistent (Thread 6)**

The IPC server runs for the entire application lifetime, listening for commands
from context menu invocations. It is a simple blocking read loop on a daemon
thread.

### Thread Safety Guarantees

| Shared Resource | Protection | Threads Accessing |
|----------------|------------|-------------------|
| AppState | `_state_lock` (Lock) | All threads via `_set_state()` |
| AppConfig | GIL (reference swap) | Main, Pipeline, Settings, Overlay |
| AudioPlayer._playing | `_lock` (Lock) | Pipeline, Overlay, Pipe |
| Overlay UI widgets | Only accessed from Thread 4 | Thread 4 only |
| Settings UI widgets | Only accessed from Thread 3 | Thread 3 only |

**Cross-thread communication pattern (Overlay -> Main)**:

The overlay buttons trigger actions that must be dispatched to the correct thread.
The pattern is:

1. Overlay button callback runs on Thread 4 (tkinter thread).
2. Callback posts an action to a `queue.Queue` (thread-safe).
3. Pipeline worker or a dispatcher on the keyboard thread picks up the action.

In practice, the simplest approach: overlay button callbacks directly call
`VoicePasteApp` methods that are already thread-safe (they just check state and
spawn pipeline threads). The GIL protects the state checks, and `_state_lock`
protects transitions. This is exactly how hotkey callbacks work today.

---

## 5. State Machine Extensions

### Current State Machine (v0.5)

```
IDLE --> RECORDING --> PROCESSING --> PASTING --> IDLE
                  \-> (CANCELLED) -> IDLE
```

### v0.6 Extended State Machine

```
                            +-------+
                   +------->| IDLE  |<------------------------+
                   |        +--+----+                         |
                   |           |                              |
                   |      Hotkey / Overlay                    |
                   |      Record btn                          |
                   |           |                              |
                   |        +--v--------+                     |
                   |        | RECORDING |---Escape-->(cancel) |
                   |        +--+--------+                     |
                   |           |                              |
                   |      Hotkey press                        |
                   |           |                              |
                   |        +--v---------+                    |
                   |        | PROCESSING |                    |
                   |        +--+---------+                    |
                   |           |                              |
                   |      STT + summarize                     |
                   |      complete                            |
                   |           |                              |
                   |     +-----+-------+                      |
                   |     |             |                      |
                   |  +--v-----+   +--v------+                |
                   |  | PASTING|   | SPEAKING| [NEW]          |
                   |  +--+-----+   +--+------+                |
                   |     |            |                        |
                   |     +-----+------+                        |
                   |           |                               |
                   +-----------+                               |
                                                              |
              Context menu / Overlay                           |
              "Clipboard->TTS"                                 |
                    |                                          |
              +-----v---------+                                |
              | SPEAKING      |----Escape / Stop btn-----------+
              +---------------+
```

### New States

**SPEAKING**: TTS audio is being played through the speakers.

- Entry: After TTS synthesis completes, or directly from IDLE for clipboard-to-TTS.
- Exit: Playback finishes naturally, or user cancels (Escape / overlay Stop button).
- Behavior: Tray icon shows blue (new color). Hotkey presses for recording are
  rejected. Overlay shows a "Stop" button replacing the TTS button.

### Updated AppState Enum

```python
class AppState(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    PASTING = "pasting"
    SPEAKING = "speaking"     # NEW: TTS playback in progress
```

### New Tray Icon Color

```python
_STATE_COLORS: dict[AppState, tuple[int, int, int]] = {
    AppState.IDLE: (220, 220, 230),       # Light silver-white
    AppState.RECORDING: (230, 50, 50),    # Bright red
    AppState.PROCESSING: (240, 200, 40),  # Bright yellow/amber
    AppState.PASTING: (50, 200, 80),      # Bright green
    AppState.SPEAKING: (80, 140, 240),    # Bright blue (NEW)
}
```

### State Transition Table (Complete)

| From State | Trigger | To State | Action |
|------------|---------|----------|--------|
| IDLE | Summary hotkey | RECORDING | Start mic capture |
| IDLE | Prompt hotkey | RECORDING | Start mic capture (prompt mode) |
| IDLE | Overlay Record | RECORDING | Start mic capture |
| IDLE | Overlay AI Query | RECORDING | Start mic capture (prompt mode) |
| IDLE | Overlay Clipboard->TTS | SPEAKING | Read clipboard, synthesize, play |
| IDLE | Pipe "tts" command | SPEAKING | Synthesize received text, play |
| RECORDING | Hotkey press | PROCESSING | Stop recording, run pipeline |
| RECORDING | Escape | IDLE | Cancel, discard audio |
| PROCESSING | Pipeline success | PASTING | Paste text at cursor |
| PROCESSING | Pipeline success + TTS flag | SPEAKING | Synthesize and play |
| PROCESSING | Pipeline error | IDLE | Show error notification |
| PASTING | Paste complete | IDLE | Restore clipboard |
| SPEAKING | Playback finished | IDLE | -- |
| SPEAKING | Escape / Stop btn | IDLE | Cancel playback |
| SPEAKING | Hotkey press | **REJECTED** | Notify: "Stop playback first" |
| SPEAKING | Pipe "tts" command | **REJECTED** | Already speaking |

### TTS-augmented Pipeline Modes

The `_active_mode` field (currently `"summary"` or `"prompt"`) gains two new values:

- `"tts"`: Record -> Transcribe -> TTS (speak the transcription itself)
- `"ask_tts"`: Record -> Transcribe -> LLM Prompt -> TTS (speak the LLM answer)

These are triggered from overlay buttons only (no dedicated hotkeys in v0.6).

---

## 6. Overlay Window Technical Approach

### 6.1 Decision: tkinter Toplevel on Dedicated Thread

**Options evaluated:**

| Approach | Pros | Cons |
|----------|------|------|
| **tkinter Toplevel** | Already in codebase, dark theme ready, no new deps | Limited widgets, not modern-looking |
| Win32 CreateWindowEx (ctypes) | Full native control, minimal deps | Massive complexity, manual event loop, paint handling |
| PyQt5/PySide6 | Professional look, rich widgets | 50+ MB binary increase, overkill for 4 buttons |
| wxPython | Native look, moderate size | 20+ MB, new dependency |
| Web overlay (webview) | Modern UI via HTML/CSS | Heavy, separate process, IPC needed |

**Decision**: tkinter Toplevel. Rationale:

1. **Zero additional dependencies**: tkinter is bundled with Python. sv_ttk dark
   theme is already in the build.
2. **Proven threading model**: The Settings dialog already uses tkinter on a
   dedicated thread. The overlay follows the same pattern.
3. **Minimal binary impact**: Zero additional bytes.
4. **Good enough**: A toolbar with 4-5 buttons does not require Qt-level widget
   richness. sv_ttk's dark theme makes it visually acceptable.

### 6.2 Overlay Window Specification

```
+---------------------------------------------------+
|  [drag handle]  VP Overlay                    [_] |
+---------------------------------------------------+
| [mic] Record  [ai] Ask AI  [tts] Read  [x] Close |
+---------------------------------------------------+
```

**Properties:**
- Always-on-top: `wm_attributes("-topmost", True)`
- No taskbar entry: `wm_overrideredirect(True)` (removes title bar), then custom
  drag handle painted in canvas.
- Dark theme: Matches existing sv_ttk dark palette.
- Movable: Custom drag via `<ButtonPress-1>`, `<B1-Motion>` on the handle area.
- Position persistence: Save last position to config.toml `[overlay]` section.
- Minimizable: A small "minimize" button hides the overlay via `withdraw()`.
  Tray menu gains "Show Overlay" / "Hide Overlay" toggle.
- Size: ~350x45 pixels. Fixed, not resizable.

**Buttons:**

| Button | Icon | Action | State Guard |
|--------|------|--------|-------------|
| Record | Microphone icon (red when recording) | Toggle recording (same as Ctrl+Alt+R) | Disabled during PROCESSING/SPEAKING |
| Ask AI | Chat bubble icon | Toggle recording in prompt mode (same as Ctrl+Alt+A) | Disabled during PROCESSING/SPEAKING |
| Read Aloud | Speaker icon | Read clipboard text via TTS | Disabled during RECORDING/PROCESSING/SPEAKING |
| Stop | Square icon | Stop current TTS playback | Only visible during SPEAKING |

**New file: `src/overlay.py`**

The overlay runs on its own thread with its own Tk root. Key implementation details:

- `_overlay_lock` singleton guard (like `_settings_lock` in settings_dialog.py).
- State updates arrive via `after()` polling or `event_generate()` from other threads.
- Button callbacks call `VoicePasteApp` methods directly (thread-safe via state lock).
- State-aware button enable/disable: overlay polls `get_state()` callback every 250ms.

### 6.3 Overlay <-> Settings Dialog Coexistence

Both use tkinter on separate threads. Each has its own Tk root and mainloop.
This is safe because:

- CPython's tkinter uses a per-interpreter Tcl lock (the GIL serializes Tcl calls).
- Each Tk root creates its own Tcl interpreter.
- No shared Tcl variables or widgets between the two.

**Tested pattern**: Multiple Tk() instances on separate threads is a known working
pattern in CPython tkinter (documented in the Tcl/Tk manual as "multi-threaded
embedding"). The existing Settings dialog already validates this alongside the
pystray main thread.

---

## 7. Context Menu Registration Approach

### 7.1 Decision: Registry-Based Shell Extension + Named Pipe IPC

**Options evaluated:**

| Approach | Complexity | Admin Required | Reliability |
|----------|------------|----------------|-------------|
| **Registry shell extension** | Low | No (HKCU) | High |
| COM server (IContextMenu) | Very High | No (HKCU) | Very High |
| PowerShell profile hook | Low | No | Low (only PowerShell) |
| AutoHotkey script | Low | No | Medium (separate process) |

**Decision**: Registry-based entry in `HKCU\Software\Classes\*\shell\VoicePaste`
with a named pipe for IPC. Rationale:

1. **No admin rights**: HKCU registry writes require no elevation.
2. **Universal**: Works in Explorer, notepad, and any app that supports the Windows
   shell context menu.
3. **Simple**: A few registry keys vs. hundreds of lines for a COM server.
4. **Clean uninstall**: Remove the registry key and everything is gone.

### 7.2 Registry Structure

```
HKCU\Software\Classes\*\shell\VoicePasteReadAloud
    (Default) = "Read aloud with Voice Paste"
    Icon = "C:\path\to\VoicePaste.exe,0"
    \command
        (Default) = "C:\path\to\VoicePaste.exe" --context-menu-tts
```

**Key insight**: The context menu command does NOT pass the selected text as a
command-line argument. The reason: Windows shell context menu `\command` entries
for `*\shell\` (all files) receive the file path as `%1`, not selected text.
Selected text in a text field is not available through shell verbs.

**Revised approach**: The context menu entry triggers a two-step process:

1. VoicePaste.exe is invoked with `--context-menu-tts`.
2. The stub checks if VoicePaste is already running (via named pipe probe).
3. If running: reads the clipboard (user must Ctrl+C first), sends text via pipe.
4. If not running: starts VoicePaste, then sends via pipe.

**Alternative**: Register a global hotkey for "Read selected text" (e.g., Ctrl+Alt+T)
that reads the current selection via clipboard simulation (Ctrl+C, read clipboard,
send to TTS, restore clipboard). This is simpler and more reliable than the shell
context menu.

**Revised decision**: Implement BOTH:

1. **Hotkey-based** (Ctrl+Alt+T): Copy selection, send to TTS. Works in every app.
   Lower friction, no context menu needed. Implemented first.
2. **Shell context menu** (optional): Installer/uninstaller in Settings dialog.
   For users who prefer the right-click workflow.

### 7.3 Named Pipe IPC Protocol

**New file: `src/ipc.py`**

```python
"""Named pipe IPC server for inter-process communication.

Allows external processes (context menu, CLI) to send commands to the
running VoicePaste instance.

Pipe name: \\.\pipe\VoicePasteIPC
Protocol: JSON over newline-delimited messages.
"""

PIPE_NAME = r"\\.\pipe\VoicePasteIPC"
```

**Message format:**

```json
{"action": "tts", "text": "Text to speak aloud."}
{"action": "ping"}
```

**Response format:**

```json
{"status": "ok"}
{"status": "error", "message": "TTS not configured."}
{"status": "busy", "message": "Already speaking."}
```

The pipe server uses `win32pipe` (via ctypes) or the `multiprocessing.connection`
module (simpler, stdlib). Decision: `multiprocessing.connection.Listener` with
an `"AF_PIPE"` family for simplicity. This avoids raw ctypes Win32 pipe API.

**Note**: `multiprocessing.connection.Listener` supports `AF_PIPE` on Windows and
handles authentication (via `authkey`). We use a fixed authkey derived from the
pipe name to prevent unauthorized connections.

### 7.4 Context Menu Registration UI

In the Settings dialog, under a new "Integration" section:

```
[x] Show context menu entry "Read aloud with Voice Paste"
    [Install]  [Uninstall]
    Status: Installed / Not installed
```

**New file: `src/context_menu.py`**

```python
"""Windows shell context menu registration and unregistration.

Adds/removes the 'Read aloud with Voice Paste' entry from the
right-click context menu via HKCU registry.
"""

import logging
import sys
import winreg
from pathlib import Path

logger = logging.getLogger(__name__)

_REG_KEY = r"Software\Classes\*\shell\VoicePasteReadAloud"
_REG_COMMAND_KEY = _REG_KEY + r"\command"


def install_context_menu() -> bool:
    """Install the context menu entry in HKCU registry."""
    # ...


def uninstall_context_menu() -> bool:
    """Remove the context menu entry from HKCU registry."""
    # ...


def is_context_menu_installed() -> bool:
    """Check if the context menu entry exists."""
    # ...
```

---

## 8. Config Schema Extensions

### 8.1 New config.toml Sections

```toml
# === Existing sections (unchanged) ===

[hotkey]
combination = "ctrl+alt+r"
prompt_combination = "ctrl+alt+a"
tts_combination = "ctrl+alt+t"         # NEW: read selection aloud

[transcription]
# ... (unchanged)

[summarization]
# ... (unchanged)

# === NEW sections ===

[tts]
# Enable text-to-speech functionality (default: false)
enabled = false
# TTS provider: "elevenlabs" (default, requires API key)
provider = "elevenlabs"
# ElevenLabs voice ID (default: Lily -- multilingual, natural)
voice_id = "pFZP5JQG7iQjIQuC4Bku"
# ElevenLabs model (eleven_flash_v2_5 = fastest, eleven_multilingual_v2 = highest quality)
model_id = "eleven_flash_v2_5"
# Output format for audio playback
output_format = "mp3_44100_128"

[overlay]
# Show floating overlay toolbar on startup (default: false)
enabled = false
# Last position (auto-saved, do not edit manually)
position_x = -1
position_y = -1

[integration]
# Shell context menu: "Read aloud with Voice Paste" (default: false)
context_menu_installed = false
```

### 8.2 New AppConfig Fields

```python
@dataclass
class AppConfig:
    # ... existing fields ...

    # --- v0.6: TTS fields ---
    tts_enabled: bool = False
    tts_provider: str = "elevenlabs"
    tts_voice_id: str = DEFAULT_TTS_VOICE_ID
    tts_model_id: str = DEFAULT_TTS_MODEL_ID
    tts_output_format: str = DEFAULT_TTS_OUTPUT_FORMAT
    tts_api_key: str = ""  # Stored in keyring as "elevenlabs_api_key"

    # --- v0.6: TTS hotkey ---
    tts_hotkey: str = DEFAULT_TTS_HOTKEY

    # --- v0.6: Overlay fields ---
    overlay_enabled: bool = False
    overlay_position_x: int = -1
    overlay_position_y: int = -1

    # --- v0.6: Integration ---
    context_menu_installed: bool = False
```

### 8.3 New Keyring Entry

```
VoicePaste:elevenlabs_api_key
```

### 8.4 New Constants

**Additions to `src/constants.py`:**

```python
# --- v0.6: TTS configuration ---
DEFAULT_TTS_HOTKEY = "ctrl+alt+t"
DEFAULT_TTS_VOICE_ID = "pFZP5JQG7iQjIQuC4Bku"  # Lily (multilingual)
DEFAULT_TTS_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_TTS_OUTPUT_FORMAT = "mp3_44100_128"
TTS_PROVIDERS = ("elevenlabs",)
DEFAULT_TTS_PROVIDER = "elevenlabs"
KEYRING_ELEVENLABS_KEY = "elevenlabs_api_key"

# TTS state color
# (added to _STATE_COLORS in tray.py)
```

---

## 9. Build Impact Analysis

### 9.1 New Dependencies

| Dependency | Purpose | Size | Required | PyInstaller |
|------------|---------|------|----------|-------------|
| `elevenlabs` | TTS API client | ~2 MB (+ httpx already bundled) | Optional (TTS feature) | Good |
| `miniaudio` | MP3 decoding for playback | ~0.5 MB (.pyd C extension) | Required if TTS enabled | Good (single .pyd) |
| `websockets` | Transitive dep of elevenlabs (streaming) | ~0.5 MB | With elevenlabs | Good |

**Note**: The `elevenlabs` SDK is a thin httpx-based wrapper. Since we already
bundle `httpx`, `httpcore`, `certifi`, etc. for the `openai` SDK, the incremental
size is small (the SDK code itself, not its transitive deps).

### 9.2 Binary Size Impact

| Component | Current Size | v0.6 Addition |
|-----------|-------------|---------------|
| Cloud-only .exe | ~50-60 MB | +3-5 MB (elevenlabs + miniaudio) |
| With local STT | ~150-200 MB | +3-5 MB (same) |

**Verdict**: The binary size increase is minimal and acceptable.

### 9.3 PyInstaller Spec Changes

```python
# --- elevenlabs SDK ---
_hidden_imports += [
    'elevenlabs',
    'elevenlabs.client',
    'elevenlabs.core',
    'websockets',
]

# --- miniaudio ---
_hidden_imports += ['miniaudio']
try:
    _miniaudio_bins = collect_dynamic_libs('miniaudio')
    _binaries += _miniaudio_bins
except Exception:
    pass

# --- multiprocessing.connection (for named pipe IPC) ---
_hidden_imports += [
    'multiprocessing.connection',
    'multiprocessing.reduction',
]
```

### 9.4 UPX Exclusion

Add `miniaudio` compiled extension to UPX exclude list:

```python
_upx_exclude += ['miniaudio.pyd']
```

---

## 10. Implementation Plan and File Changes

### 10.1 New Files

| File | Purpose | Lines (est.) |
|------|---------|--------------|
| `src/tts.py` | TTS Protocol, ElevenLabsTTS, factory | ~200 |
| `src/audio_playback.py` | AudioPlayer (MP3 decode + sounddevice output) | ~150 |
| `src/overlay.py` | Floating overlay window (tkinter) | ~350 |
| `src/ipc.py` | Named pipe IPC server | ~150 |
| `src/context_menu.py` | Registry-based shell context menu | ~100 |
| `tests/test_tts.py` | TTS backend unit tests | ~100 |
| `tests/test_audio_playback.py` | Audio playback tests (mocked) | ~80 |
| `tests/test_overlay.py` | Overlay UI tests (basic) | ~60 |
| `tests/test_ipc.py` | IPC protocol tests | ~80 |
| `tests/test_context_menu.py` | Registry operation tests (mocked) | ~60 |

### 10.2 Modified Files

| File | Changes |
|------|---------|
| `src/constants.py` | Add TTS constants, SPEAKING state, TTS hotkey, keyring key |
| `src/config.py` | Add TTS/overlay/integration fields to AppConfig, config loading, save_to_toml |
| `src/main.py` | Initialize TTS backend, AudioPlayer, overlay, IPC server. Add SPEAKING state handling, TTS hotkey, new pipeline modes. ~100 new lines |
| `src/hotkey.py` | Register TTS hotkey (Ctrl+Alt+T). Add `register_tts()` method. ~30 new lines |
| `src/tray.py` | Add SPEAKING color/tooltip, "Show/Hide Overlay" menu item, TTS status. ~30 new lines |
| `src/notifications.py` | Add TTS-related audio cues (optional). ~10 new lines |
| `src/settings_dialog.py` | New TTS section (provider, voice, API key), overlay toggle, context menu install. ~200 new lines |
| `src/keyring_store.py` | No changes (generic; new key name is in constants) |
| `voice_paste.spec` | Add elevenlabs, miniaudio hidden imports and binaries |

### 10.3 Integration Points in main.py

```python
class VoicePasteApp:
    def __init__(self, config):
        # ... existing init ...

        # v0.6: TTS backend and audio player
        self._tts = create_tts_backend(config)
        self._audio_player = AudioPlayer()

        # v0.6: Overlay window
        if config.overlay_enabled:
            self._start_overlay()

        # v0.6: IPC server for context menu
        self._start_ipc_server()

    def _on_tts_hotkey(self) -> None:
        """Handle Ctrl+Alt+T: copy selection, send to TTS."""
        if self.state != AppState.IDLE:
            if self.state == AppState.SPEAKING:
                # Stop current playback
                self._stop_tts()
            return

        # Simulate Ctrl+C to copy selection
        # Read clipboard
        # Send to TTS pipeline
        ...

    def _run_tts_pipeline(self, text: str) -> None:
        """Synthesize and play text via TTS backend."""
        self._set_state(AppState.SPEAKING)
        try:
            audio_data = self._tts.synthesize(text)
            if self.state != AppState.SPEAKING:
                return  # Cancelled
            self._audio_player.play_mp3(audio_data)
            # Wait for playback to finish or be cancelled
            while self._audio_player.is_playing:
                if self.state != AppState.SPEAKING:
                    self._audio_player.stop()
                    return
                time.sleep(0.1)
        except TTSError as e:
            self._show_error(f"TTS error: {e}")
        finally:
            if self.state == AppState.SPEAKING:
                self._set_state(AppState.IDLE)

    def _stop_tts(self) -> None:
        """Stop TTS playback and return to IDLE."""
        self._audio_player.stop()
        if self._tts:
            self._tts.stop()
        self._set_state(AppState.IDLE)
```

---

## 11. Risk Mitigation Strategies

### Risk 1: Multiple tkinter Tk() Roots Cause Crashes

**Risk level**: Medium
**Mitigation**:
- Proven by Settings dialog (already a separate Tk root from pystray thread).
- Each Tk root gets its own Tcl interpreter; no shared state.
- Defensive: wrap overlay thread in try/except with graceful degradation
  (overlay fails silently, hotkeys still work).
- If instability is detected during testing, fall back to a single shared Tk root
  between overlay and settings (with mutual exclusion on who uses mainloop).

### Risk 2: elevenlabs SDK Incompatible with PyInstaller

**Risk level**: Low
**Mitigation**:
- The SDK is pure Python (httpx client). Its only native dependency is websockets
  (for streaming), which is well-tested with PyInstaller.
- If bundling fails: fall back to raw httpx calls to the ElevenLabs REST API
  (documented at api.elevenlabs.io). The SDK is a convenience, not a necessity.

### Risk 3: miniaudio .pyd Fails in Frozen Exe

**Risk level**: Low-Medium
**Mitigation**:
- miniaudio is a single compiled extension. Add to `collect_dynamic_libs()` and
  UPX exclusion list.
- Fallback: use `io.BytesIO` + `wave` module to write WAV headers and play raw
  PCM through sounddevice directly. This requires the ElevenLabs API to output
  PCM (supported via `pcm_44100` format) instead of MP3.
- Secondary fallback: use `winsound.PlaySound()` for WAV data (no external deps,
  but no cancel support).

### Risk 4: Named Pipe IPC Security

**Risk level**: Medium
**Mitigation**:
- Use `multiprocessing.connection.Listener` with an `authkey` to prevent
  unauthorized connections.
- The authkey is derived from a constant (hardcoded secret in the binary). This
  prevents casual abuse but is not cryptographically secure against a determined
  local attacker. Since the pipe only accepts "speak this text" commands, the
  attack surface is limited (worst case: an attacker can make VoicePaste speak).
- Named pipe DACL: default security (same user only on Windows).
- Rate limiting: reject pipe commands if one arrived less than 1 second ago.

### Risk 5: Context Menu Registration Fails Without Admin

**Risk level**: Low
**Mitigation**:
- HKCU registry writes never require admin rights. HKCU is per-user.
- If winreg operations fail (e.g., group policy restriction), show a clear error
  in the Settings dialog with manual registry instructions.

### Risk 6: Overlay Window Interferes with Fullscreen Apps

**Risk level**: Medium
**Mitigation**:
- Overlay respects `overrideredirect` and `topmost` but does NOT capture focus.
- Overlay does NOT appear on the taskbar (no system tray-like workarounds needed).
- Users can hide the overlay via tray menu or the [_] minimize button.
- Auto-hide on fullscreen: detect foreground window changes via periodic polling
  and hide overlay when a fullscreen app is detected. Deferred to v0.6.1 if
  user feedback indicates need.

### Risk 7: TTS Audio Feedback Loop

**Risk level**: Low (mitigated by state machine)
**Mitigation**:
- SPEAKING state blocks RECORDING transitions. User must stop TTS before recording.
- No automatic chaining (TTS does not auto-record; recording does not auto-TTS
  unless explicitly requested via overlay button).

---

## 12. Key Trade-offs Summary

| Decision | Chosen | Alternative | Rationale |
|----------|--------|-------------|-----------|
| TTS provider | ElevenLabs (cloud) | pyttsx3 (local, free) | Quality over cost; German voices sound natural |
| TTS playback | sounddevice + miniaudio | elevenlabs.play() | Cancel support, no external ffmpeg/mpv dependency |
| Overlay framework | tkinter Toplevel | Win32 API / Qt | Zero new deps, proven threading model, good enough for 4 buttons |
| Context menu IPC | Named pipe (multiprocessing) | Shared file / socket | Windows-native, reliable, authenticated |
| Context menu trigger | Hotkey (Ctrl+Alt+T) primary, shell registry optional | Shell registry only | Hotkey works in all apps; context menu only works for file-based selections |
| Overlay threading | Own Tk root on own thread | Shared root with Settings | Independence; overlay is persistent, settings is transient |
| State machine | Add SPEAKING state | Reuse PROCESSING | Clear UX feedback; prevents recording during playback |
| Streaming TTS | Deferred to v0.6.1 | Implement in v0.6.0 | Simplify MVP; full-buffer playback has adequate latency for typical text lengths |
| TTS enabled by default | No (opt-in) | Yes | Requires separate API key; avoid confusing users who only want STT |
| Overlay enabled by default | No (opt-in) | Yes | Power users prefer hotkeys; overlay may annoy minimalist users |

---

## 13. Phased Delivery Plan

### Phase 1: TTS Backend (Estimated: 2-3 days)

1. Add TTS constants to `constants.py`.
2. Implement `src/tts.py` (Protocol + ElevenLabsTTS).
3. Implement `src/audio_playback.py` (AudioPlayer with miniaudio).
4. Add SPEAKING state to `AppState`, update tray colors/tooltips.
5. Add TTS config fields to `AppConfig`, `load_config()`, `save_to_toml()`.
6. Add TTS keyring entry (`elevenlabs_api_key`).
7. Wire TTS into `main.py`: init, TTS hotkey, `_run_tts_pipeline()`, `_stop_tts()`.
8. Register TTS hotkey (Ctrl+Alt+T) in `hotkey.py`.
9. Unit tests for TTS backend (mocked API) and AudioPlayer.

**Deliverable**: TTS works via Ctrl+Alt+T hotkey. No overlay, no context menu.

### Phase 2: Overlay Window (Estimated: 2-3 days)

1. Implement `src/overlay.py` (tkinter Toplevel, dark theme, 4 buttons).
2. Add overlay config fields (`overlay_enabled`, position persistence).
3. Wire overlay into `main.py`: init on startup, state-aware button updates.
4. Add "Show/Hide Overlay" toggle to tray menu.
5. Connect overlay buttons to existing app methods.
6. Basic tests for overlay (lifecycle, button state).

**Deliverable**: Floating toolbar works alongside system tray.

### Phase 3: Context Menu + IPC (Estimated: 1-2 days)

1. Implement `src/ipc.py` (named pipe server).
2. Implement `src/context_menu.py` (registry install/uninstall).
3. Add `--context-menu-tts` CLI handler to `main.py`.
4. Wire IPC server into `main.py`: start on init, dispatch commands.
5. Add Integration section to Settings dialog.
6. Tests for IPC protocol and context menu registration.

**Deliverable**: Right-click "Read aloud" works. Full v0.6 feature set.

### Phase 4: Settings Dialog Extension (Integrated with Phases 1-3)

1. New TTS section: provider, voice ID, model, API key.
2. New Overlay section: enable toggle.
3. New Integration section: context menu install/uninstall.
4. Hot-reload for TTS settings (recreate TTS backend on save).

### Phase 5: Polish and Testing (Estimated: 1-2 days)

1. End-to-end testing of all new flows.
2. PyInstaller build verification (elevenlabs + miniaudio + miniaudio.pyd).
3. Update `voice_paste.spec` with new hidden imports and binaries.
4. Update `build.bat` if needed.
5. Update `README.md` and `CHANGELOG.md`.
6. Update `docs/ADR.md` with v0.6 decisions.

**Total estimated effort**: 8-12 developer days.

---

## Appendix A: ElevenLabs Voice Selection

Recommended voices for German TTS:

| Voice ID | Name | Description | Best For |
|----------|------|-------------|----------|
| `pFZP5JQG7iQjIQuC4Bku` | Lily | Warm, multilingual | General text, default |
| `nPczCjzI2devNBz1zQrb` | Brian | Clear, professional | Business/technical |
| `JBFqnCBsd6RMkjVDRZzb` | George | Deep, authoritative | Formal content |

The voice ID is configurable. The default (Lily) provides good German pronunciation
with the `eleven_flash_v2_5` model.

## Appendix B: miniaudio vs. Alternatives for MP3 Decoding

| Library | Pure Python | Binary Size | PyInstaller | License |
|---------|-------------|-------------|-------------|---------|
| **miniaudio** | No (C ext) | ~0.5 MB | Good (single .pyd) | MIT |
| pydub | Yes | ~0 MB | Good | MIT |
| pygame.mixer | No (C ext) | ~5 MB | Complex | LGPL |
| soundfile | No (C ext) | ~1 MB | Good | BSD |

`pydub` requires ffmpeg on PATH -- not available in a PyInstaller bundle without
bundling the entire ffmpeg binary (~80 MB).

`miniaudio` includes its own C decoder (dr_mp3, dr_wav, dr_flac). No external
binaries needed. This makes it ideal for a single-file .exe.

## Appendix C: Rejected Alternative -- COM Shell Extension

A COM-based `IContextMenu` implementation was evaluated and rejected:

1. **Complexity**: Requires registering a COM DLL in the registry, implementing
   IUnknown, IShellExtInit, and IContextMenu interfaces in Python. This is
   hundreds of lines of ctypes or comtypes code.
2. **In-process DLL**: Shell extensions are loaded into the Explorer process. A
   Python-based COM DLL would load the Python interpreter into Explorer, causing
   massive memory overhead and potential instability.
3. **Admin often needed**: While HKCU COM registration is possible, many shell
   extension guides assume HKLM.
4. **Overkill**: We only need a single menu item. The registry-based approach
   achieves this with 5 lines of registry writes.

## Appendix D: Named Pipe Protocol Specification

```
Pipe name: \\.\pipe\VoicePasteIPC

Message format: JSON + newline delimiter
Max message size: 65536 bytes
Encoding: UTF-8

Client -> Server:
  {"action": "tts", "text": "Hello world"}
  {"action": "ping"}
  {"action": "stop_tts"}

Server -> Client:
  {"status": "ok"}
  {"status": "error", "message": "description"}
  {"status": "busy", "state": "speaking"}

Authentication: multiprocessing.connection authkey
  authkey = b"VoicePasteIPC-v1"

Timeout: 5 seconds for connection, 10 seconds for response
```

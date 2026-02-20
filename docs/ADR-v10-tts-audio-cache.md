# Architecture Decision Record: v1.0 -- TTS Audio Cache / History

**Date**: 2026-02-20
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.9.0 (current)
**Target Version**: 1.0.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Context and Motivation](#2-context-and-motivation)
3. [Storage Architecture](#3-storage-architecture)
   - 3.1 [Location](#31-location)
   - 3.2 [File Format](#32-file-format)
   - 3.3 [Naming Convention](#33-naming-convention)
   - 3.4 [Cache Index](#34-cache-index)
   - 3.5 [Size Limits and Cleanup](#35-size-limits-and-cleanup)
4. [Component Design](#4-component-design)
   - 4.1 [New Module: tts_cache.py](#41-new-module-tts_cachepy)
   - 4.2 [Class Diagram](#42-class-diagram)
   - 4.3 [Data Model](#43-data-model)
5. [Integration Points](#5-integration-points)
   - 5.1 [TTS Pipeline Integration](#51-tts-pipeline-integration)
   - 5.2 [Replay Mechanism](#52-replay-mechanism)
   - 5.3 [HTTP API Extensions](#53-http-api-extensions)
   - 5.4 [System Tray Menu](#54-system-tray-menu)
   - 5.5 [Settings Dialog](#55-settings-dialog)
6. [Configuration Changes](#6-configuration-changes)
   - 6.1 [New Config Fields](#61-new-config-fields)
   - 6.2 [TOML Schema](#62-toml-schema)
7. [Threading Model](#7-threading-model)
8. [State Machine Impact](#8-state-machine-impact)
9. [Trade-offs Considered](#9-trade-offs-considered)
10. [Implementation Plan](#10-implementation-plan)
11. [Risk Assessment](#11-risk-assessment)

---

## 1. Executive Summary

This ADR proposes a **TTS Audio Cache** that persists synthesized audio files
locally so they can be replayed without re-calling the TTS backend (saving API
credits for ElevenLabs, saving compute for Piper). The cache survives app
restarts, is bounded by configurable size/age limits, and is exposed via the
existing HTTP API and tray menu.

**Key decisions**:

- **Storage**: `%LOCALAPPDATA%\VoicePaste\cache\tts\` (follows existing model
  cache pattern from `tts_model_manager.py`).
- **Index**: Single JSON file (`index.json`) -- not SQLite. Rationale: zero
  additional dependencies, the index rarely exceeds a few hundred entries, and
  JSON is human-readable for debugging.
- **Naming**: SHA256 hash of `(text + voice_id + provider)` for automatic
  deduplication. Same text + same voice = cache hit.
- **Replay**: Via HTTP API (`GET /tts/history`, `POST /tts/replay/{id}`) and
  system tray submenu ("Recent TTS" with last 10 entries).
- **Cache-through**: The `POST /tts` and hotkey-triggered TTS pipelines check
  the cache before calling the backend. Cache hits skip synthesis entirely.
- **Cleanup**: LRU eviction when total cache exceeds configured max size
  (default: 200 MB). Optional max-age eviction (default: 30 days).

**Non-goals for v1.0**:

- Audio editing or trimming of cached files.
- Streaming playback from partial cache (always complete files).
- Cloud sync of cached audio.

---

## 2. Context and Motivation

Currently, every TTS invocation calls the backend (ElevenLabs API or Piper
ONNX) even if the exact same text was spoken minutes ago. This has three costs:

1. **API charges**: ElevenLabs bills per character. Re-speaking the same text
   wastes quota.
2. **Latency**: Cloud TTS requires a network round-trip (500ms-2s). Local Piper
   is faster (~0.5s) but still non-trivial. A cache hit returns audio in <10ms.
3. **Offline replay**: With a cache, previously-heard audio can be replayed
   even when the network is unavailable or the Piper model is not loaded.

The user specifically requested: (a) a way to replay cached audio, and (b) a
setting to enable/disable this. The feature aligns with the existing
`%LOCALAPPDATA%\VoicePaste\` storage pattern used for STT and TTS models.

---

## 3. Storage Architecture

### 3.1 Location

```
%LOCALAPPDATA%\VoicePaste\cache\tts\
    index.json          <-- Cache index
    a1b2c3d4e5f6.mp3    <-- Cached audio file (hash-named)
    f7e8d9c0b1a2.wav    <-- Another cached audio file
    ...
```

**Rationale**: This follows the established pattern from `tts_model_manager.py`
which stores models in `%LOCALAPPDATA%\VoicePaste\models\tts\`. Using a
separate `cache\tts\` subtree keeps cached audio cleanly separated from model
files and makes it safe to wipe the cache without affecting models.

**Alternative considered**: Storing next to the `.exe` (app directory). Rejected
because (a) the exe directory may be read-only (e.g., installed in Program
Files), (b) `%LOCALAPPDATA%` is the Windows-standard location for per-user
application cache data, and (c) it keeps the portable exe directory clean.

### 3.2 File Format

Cached audio files are stored in their **original format** as returned by the
TTS backend:

- **ElevenLabs**: MP3 (as configured via `tts_output_format`, default
  `mp3_44100_128`).
- **Piper**: WAV (16-bit PCM, as returned by `PiperLocalTTS.synthesize()`).

**Rationale**: No transcoding needed. The `AudioPlayer` already handles both
MP3 and WAV transparently via `miniaudio.decode()`. Storing in the original
format avoids quality loss and the need for additional codec dependencies.

**File extension**: Determined at cache-write time based on a simple header
sniff:
- Bytes start with `RIFF` -> `.wav`
- Bytes start with `\xff\xfb` or `ID3` -> `.mp3`
- Fallback -> `.bin` (still playable by miniaudio)

### 3.3 Naming Convention

Each cache file is named using a **SHA256 hash** of the cache key:

```python
cache_key = f"{provider}:{voice_id}:{text}"
file_stem = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
```

Using the first 16 hex chars (64 bits) of SHA256 gives a collision probability
of ~1 in 2^32 at 65,000 entries, which is more than sufficient for a local
cache of a few hundred items.

**Why include provider + voice_id in the key**: The same text spoken by
different voices or providers produces different audio. The cache must
differentiate these.

**Why hash, not text**: Filenames must be filesystem-safe. Hashing avoids
encoding issues, path length limits, and special characters in the source text.

**Alternative considered**: Sequential integer IDs. Rejected because they
require an atomic counter and do not provide automatic deduplication. With
hashes, writing the same text+voice combination twice simply overwrites the
same file (idempotent).

### 3.4 Cache Index

A single JSON file `index.json` in the cache directory stores metadata:

```json
{
  "version": 1,
  "entries": {
    "a1b2c3d4e5f67890": {
      "text": "Dies ist ein Beispieltext.",
      "text_preview": "Dies ist ein Beispieltext.",
      "provider": "elevenlabs",
      "voice_id": "pFZP5JQG7iQjIQuC4Bku",
      "voice_label": "Lily",
      "format": "mp3",
      "file_size_bytes": 34567,
      "duration_seconds": 2.3,
      "created_at": "2026-02-20T14:30:00Z",
      "last_played_at": "2026-02-20T15:45:00Z",
      "play_count": 3
    }
  }
}
```

**Why JSON, not SQLite**:

| Criterion         | JSON                        | SQLite                       |
|--------------------|-----------------------------|------------------------------|
| Dependencies       | Zero (stdlib `json`)        | Zero (stdlib `sqlite3`)      |
| Complexity         | ~50 lines of R/W code       | ~100 lines + schema + migrations |
| Query capability   | Linear scan (fine for <1000) | Full SQL (overkill here)     |
| Human-readable     | Yes (editable)              | No (binary)                  |
| Corruption risk    | Low (atomic write via tmp)  | Very low (WAL journal)       |
| PyInstaller impact | None                        | None (sqlite3 is stdlib)     |

For an expected cache size of 10-500 entries, JSON is the simpler choice. If
the cache ever needs complex queries (unlikely), migration to SQLite is
straightforward because the data model is a flat key-value structure.

**Atomic writes**: `index.json` is written via the same pattern used in
`config.py`: write to `index.json.tmp`, then `os.replace()` atomically.

**Index corruption recovery**: If `index.json` is missing or corrupt, the cache
module rebuilds it by scanning the cache directory for audio files. Files
without matching index entries get metadata estimated from file attributes
(creation time, file size). This means the cache is never "broken" -- at worst,
some metadata (text preview, play count) is lost.

### 3.5 Size Limits and Cleanup

**Eviction strategy**: LRU (Least Recently Used), based on `last_played_at`
(or `created_at` if never replayed).

**Triggers**: Eviction runs:
1. After every cache write (inline, before returning from `put()`).
2. On app startup (deferred to a background thread to avoid slowing startup).

**Configurable limits**:

| Setting              | Default  | Range         | Description                     |
|----------------------|----------|---------------|---------------------------------|
| `max_cache_size_mb`  | 200      | 10 - 2000     | Total cache directory size in MB |
| `max_cache_age_days` | 30       | 1 - 365       | Max age of unused entries (days) |
| `max_cache_entries`  | 500      | 10 - 5000     | Max number of cached entries     |

**Eviction algorithm**:

```
1. Remove all entries older than max_cache_age_days (by last_played_at).
2. While total_size > max_cache_size_mb OR count > max_cache_entries:
   a. Sort remaining entries by last_played_at ascending (oldest first).
   b. Remove the oldest entry (delete file + remove from index).
3. Save updated index.json.
```

**Alternative considered**: FIFO (first in, first out). Rejected because FIFO
would evict frequently-replayed audio, while LRU keeps popular entries cached.

---

## 4. Component Design

### 4.1 New Module: tts_cache.py

A single new file `src/tts_cache.py` containing the `TTSAudioCache` class.
This follows the project's pattern of one module per concern (`tts.py` for
backends, `audio_playback.py` for playback, `tts_model_manager.py` for model
downloads).

### 4.2 Class Diagram

```
+---------------------+
|   TTSAudioCache     |
+---------------------+
| - _cache_dir: Path  |
| - _index_path: Path |
| - _index: dict      |
| - _lock: Lock       |
| - _config: CacheConfig |
+---------------------+
| + __init__(config)   |
| + get(key) -> bytes? |    <-- Cache lookup (returns audio bytes or None)
| + put(key, meta,     |    <-- Store audio bytes with metadata
|       audio) -> str  |        Returns entry_id (hash)
| + get_entry(id)      |    <-- Get metadata for a single entry
|       -> CacheEntry? |
| + list_entries(      |    <-- List entries (for UI/API), sorted by recency
|       limit) -> list |
| + replay(id)         |    <-- Load cached audio bytes by entry ID
|       -> bytes?      |
| + delete(id) -> bool |    <-- Delete a single entry
| + clear() -> int     |    <-- Delete all entries, return count
| + stats() -> dict    |    <-- Total size, entry count, oldest/newest
| + evict()            |    <-- Run eviction (called internally + on startup)
+---------------------+

+---------------------+
|   CacheConfig       |  (NamedTuple or dataclass)
+---------------------+
| + enabled: bool     |
| + max_size_mb: int  |
| + max_age_days: int |
| + max_entries: int  |
+---------------------+

+---------------------+
|   CacheEntry        |  (TypedDict or dataclass)
+---------------------+
| + entry_id: str     |  (16-char hex hash)
| + text: str         |
| + text_preview: str |  (first 80 chars, for UI display)
| + provider: str     |
| + voice_id: str     |
| + voice_label: str  |
| + format: str       |  ("mp3" or "wav")
| + file_size_bytes   |
| + duration_seconds  |
| + created_at: str   |  (ISO 8601)
| + last_played_at: str |
| + play_count: int   |
+---------------------+
```

### 4.3 Data Model

The cache key is a composite of three values that uniquely identify the audio
output:

```python
@dataclass(frozen=True)
class CacheKey:
    """Composite key for TTS audio cache lookup."""
    provider: str       # "elevenlabs" or "piper"
    voice_id: str       # ElevenLabs voice ID or Piper voice name
    text: str           # The exact input text

    def to_hash(self) -> str:
        """Generate the 16-char hex hash used as filename/entry_id."""
        raw = f"{self.provider}:{self.voice_id}:{self.text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
```

**Why `text` must be exact**: Even whitespace differences produce different
audio timing. The cache uses the text as-is, without normalization. This
prevents subtle bugs where "Hello World" and "Hello  World" are treated as
the same but produce audibly different results.

**Duration estimation**: The duration is calculated from the decoded PCM
length at cache-write time:

```python
decoded = miniaudio.decode(audio_data, output_format=..., nchannels=1, sample_rate=44100)
duration_seconds = len(decoded.samples) / decoded.sample_rate / decoded.nchannels
```

This adds ~10ms of overhead at write time but provides accurate duration for
the UI without re-decoding at display time.

---

## 5. Integration Points

### 5.1 TTS Pipeline Integration

The cache integrates as a **transparent layer** between the TTS trigger and the
TTS backend. Both the hotkey-triggered and API-triggered TTS paths converge
on `_run_tts_pipeline()` in `main.py`.

**Modified flow in `_run_tts_pipeline()`**:

```
                     text
                      |
                      v
              +----------------+
              | Cache lookup   |  (TTSAudioCache.get())
              | (by key hash)  |
              +-------+--------+
                      |
              +-------+-------+
              |               |
          HIT |           MISS|
              v               v
        audio_data     +------------+
              |        | TTS Backend |  (ElevenLabs / Piper)
              |        | .synthesize |
              |        +------+-----+
              |               |
              |               v
              |        +------------+
              |        | Cache store|  (TTSAudioCache.put())
              |        +------+-----+
              |               |
              +-------+-------+
                      |
                      v
              +----------------+
              | AudioPlayer    |
              | .play()        |
              +----------------+
```

**Key principle**: The cache is purely additive. If the cache is disabled or
fails, the pipeline falls back to the non-cached path. No existing behavior
changes.

**Cache write is synchronous but fast**: Writing a ~50KB MP3 file to disk
takes <1ms on any modern SSD. The index JSON write adds ~1ms. Total overhead
for a cache miss: ~2ms. This is negligible compared to the TTS synthesis
time (500ms-2s).

### 5.2 Replay Mechanism

**How the user triggers replay**:

1. **System tray submenu**: Right-click tray icon > "Recent TTS" >
   list of up to 10 entries showing `text_preview` and `duration_seconds`.
   Clicking an entry replays it.

2. **HTTP API**: `GET /tts/history` returns a list of cached entries.
   `POST /tts/replay/{id}` triggers playback of a specific entry.

**Replay uses the existing AudioPlayer**: The replayed audio bytes are passed
to `AudioPlayer.play()` exactly as if they came from a fresh synthesis. This
means all existing controls work: Escape to stop, state machine transitions
(IDLE -> SPEAKING -> IDLE), overlay updates.

**Replay state transition**: Replaying a cached entry follows the same state
machine as live TTS: IDLE -> SPEAKING -> IDLE. The PROCESSING state is skipped
because no synthesis is needed. This is a minor state machine difference that
the user will perceive as "instant playback."

### 5.3 HTTP API Extensions

Four new endpoints:

```
GET  /tts/history              List cached entries (newest first)
GET  /tts/history/{id}         Get metadata for a single entry
POST /tts/replay/{id}          Replay a cached entry
DELETE /tts/history/{id}       Delete a cached entry
DELETE /tts/history             Clear entire cache
```

**`GET /tts/history` response**:

```json
{
  "status": "ok",
  "data": {
    "entries": [
      {
        "id": "a1b2c3d4e5f67890",
        "text_preview": "Dies ist ein Beispieltext.",
        "provider": "elevenlabs",
        "voice_label": "Lily",
        "duration_seconds": 2.3,
        "created_at": "2026-02-20T14:30:00Z",
        "play_count": 3
      }
    ],
    "total_entries": 42,
    "total_size_mb": 87.3,
    "cache_enabled": true
  }
}
```

Optional query parameters: `?limit=20&offset=0` for pagination.

**`POST /tts/replay/{id}` response**:

```json
{"status": "ok"}
```

Returns 404 if the entry ID does not exist. Returns 409 (busy) if the app is
not in IDLE state.

**`POST /tts` change**: The existing `/tts` endpoint gains **automatic cache-
through** behavior. Before calling the backend, it checks the cache. The
response includes a `"cached": true|false` field so the caller knows whether
it was a cache hit.

**`DELETE /tts/history/{id}` response**:

```json
{"status": "ok", "deleted": true}
```

**`DELETE /tts/history` response**:

```json
{"status": "ok", "deleted_count": 42}
```

### 5.4 System Tray Menu

The existing tray menu structure (from `tray.py`) gains a new submenu:

```
Voice Paste
  |-- Settings...
  |-- Hands-Free Mode  [ON/OFF]
  |-- Recent TTS >>                     <-- NEW
  |     |-- "Dies ist ein Beispiel..." (2.3s, Lily)
  |     |-- "Guten Morgen, wie ge..." (1.8s, Thorsten)
  |     |-- ...
  |     |-- (separator)
  |     |-- Clear TTS Cache (42 entries, 87 MB)
  |-- (separator)
  |-- Quit
```

**Entry format**: `"{text_preview}" ({duration}s, {voice_label})`

**Limit**: 10 most recent entries in the submenu (configurable via
constant, not user-facing config). More entries are accessible via the API.

**Dynamic update**: The submenu is rebuilt each time the tray menu is opened
(pystray supports dynamic menu items via callables). This is cheap because it
only reads the in-memory index, no disk I/O.

### 5.5 Settings Dialog

A new section in the Settings dialog: **"TTS Cache"** (placed after the TTS
section).

```
+-------------------------------------------------------+
| TTS Cache                                             |
+-------------------------------------------------------+
| [x] Enable TTS audio cache                            |
|                                                       |
| Max cache size:    [200   ] MB                        |
| Max entry age:     [30    ] days                      |
| Max entries:       [500   ]                           |
|                                                       |
| Cache location: %LOCALAPPDATA%\VoicePaste\cache\tts   |
| Current usage:  42 entries, 87.3 MB                   |
|                                                       |
| [ Clear Cache ]    [ Open Folder ]                    |
+-------------------------------------------------------+
```

**"Clear Cache" button**: Calls `TTSAudioCache.clear()`, shows confirmation
count in a toast notification.

**"Open Folder" button**: Opens the cache directory in Windows Explorer via
`os.startfile()`.

---

## 6. Configuration Changes

### 6.1 New Config Fields

Added to `AppConfig` dataclass:

```python
# --- v1.0: TTS Audio Cache ---
tts_cache_enabled: bool = True
tts_cache_max_size_mb: int = 200
tts_cache_max_age_days: int = 30
tts_cache_max_entries: int = 500
```

**Default: enabled**. The cache has no downsides for users who use TTS
regularly. Users who never use TTS will never trigger a cache write, so
enabling by default is safe.

### 6.2 TOML Schema

New `[tts_cache]` section in `config.toml`:

```toml
[tts_cache]
# Cache TTS audio locally to avoid re-synthesis of the same text.
# Saves API credits (ElevenLabs) and reduces latency on repeated playback.
enabled = true
# Maximum total cache size in MB. Oldest entries are evicted when exceeded.
max_size_mb = 200
# Maximum age of unused cache entries in days. Set to 0 to disable age limit.
max_age_days = 30
# Maximum number of cached entries. Set to 0 for unlimited (bounded by size).
max_entries = 500
```

Added to `constants.py`:

```python
# --- v1.0: TTS Audio Cache configuration ---
DEFAULT_TTS_CACHE_ENABLED = True
DEFAULT_TTS_CACHE_MAX_SIZE_MB = 200
DEFAULT_TTS_CACHE_MAX_AGE_DAYS = 30
DEFAULT_TTS_CACHE_MAX_ENTRIES = 500
TTS_CACHE_TRAY_MENU_LIMIT = 10  # Max entries shown in tray submenu
```

---

## 7. Threading Model

The cache **does not introduce any new threads**. All cache operations run on
the existing threads:

| Operation           | Thread                     | Blocking? |
|---------------------|----------------------------|-----------|
| `get()` (lookup)    | T2 pipeline worker         | No (~1ms read from memory) |
| `put()` (store)     | T2 pipeline worker         | No (~2ms file write + index update) |
| `evict()` (cleanup) | T2 pipeline worker (inline on put) | No (~5ms for scanning 500 entries) |
| `evict()` (startup) | T2 pipeline worker (deferred) | No (runs after app init) |
| `list_entries()`    | API handler thread / pystray | No (reads in-memory index) |
| `replay()`          | T2 pipeline worker         | No (~1ms file read) |
| `clear()`           | Settings thread (T3) or API | Brief (~50ms for deleting files) |

**Thread safety**: A `threading.Lock` in `TTSAudioCache` protects the
in-memory index dict. File writes are atomic (write to `.tmp` + `os.replace`).
The lock is held only for the brief in-memory mutation, never during file I/O.

```python
# Pseudocode: put() lock strategy
def put(self, key: CacheKey, audio_data: bytes, metadata: dict) -> str:
    entry_id = key.to_hash()
    file_path = self._cache_dir / f"{entry_id}.{ext}"

    # File write outside lock (no contention)
    tmp_path = file_path.with_suffix(".tmp")
    tmp_path.write_bytes(audio_data)
    tmp_path.replace(file_path)

    # Index update inside lock (brief)
    with self._lock:
        self._index["entries"][entry_id] = {... metadata ...}
        self._save_index()  # Atomic write
        self._evict_if_needed()  # In-memory scan + possible file deletes

    return entry_id
```

**Why not async**: The cache operations are all sub-millisecond (except
`clear()` which deletes files). Async would add complexity without measurable
benefit. The existing pipeline already runs on a dedicated worker thread, so
disk I/O does not block the UI.

---

## 8. State Machine Impact

The cache introduces **one new transition** for replay:

```
Current state machine (unchanged):
  IDLE -> RECORDING -> PROCESSING -> SPEAKING -> IDLE
  IDLE -> PROCESSING -> SPEAKING -> IDLE  (TTS hotkey)

New transition for cache replay:
  IDLE -> SPEAKING -> IDLE  (replay skips PROCESSING)
```

This is technically already valid -- `_set_state()` does not enforce transition
ordering. But it is a new path that skips PROCESSING because no synthesis is
needed. The overlay (when re-enabled) should handle this gracefully (it already
shows SPEAKING state regardless of how it was reached).

**No new AppState values needed**. The existing states fully cover the cache
feature.

---

## 9. Trade-offs Considered

### JSON vs. SQLite for cache index

| Factor              | JSON (chosen)               | SQLite                      |
|---------------------|-----------------------------|-----------------------------|
| Simplicity          | ~50 lines, no schema        | ~100 lines + migrations     |
| Startup time        | ~1ms (read + parse)         | ~5ms (open + query)         |
| Query power         | Linear scan                 | Full SQL                    |
| Concurrent access   | Lock + atomic write         | Built-in WAL locking        |
| Corruption recovery | Rebuild from file scan      | SQLite auto-recovery        |
| Max practical size  | ~1000 entries               | Millions                    |

**Decision**: JSON. The cache will realistically have <500 entries. JSON is
simpler, human-readable, and sufficient. If future versions need complex
queries, SQLite migration is straightforward.

### Separate cache per voice vs. single flat cache

**Chosen: Single flat cache** with voice information embedded in the cache key
hash. This simplifies the directory structure and eviction logic. Per-voice
subdirectories would complicate size calculations and LRU ordering.

### Cache on every TTS call vs. opt-in per call

**Chosen: Cache on every TTS call** (when cache is enabled). There is no
use case for "synthesize but do not cache." If the user wants to avoid caching
specific text, they can disable the cache globally. Granular per-call control
adds API complexity without clear user benefit.

### Hash truncation: 16 chars vs. full 64 chars

**Chosen: 16 chars (64 bits)**. At 500 entries, the birthday paradox
collision probability is ~0.000003%. Even at 100,000 entries (far beyond any
realistic usage), collision probability is ~0.03%. Full 64-char hashes would
produce unwieldy filenames with no practical safety benefit.

### Duration in index vs. computed on demand

**Chosen: Stored in index at write time**. Computing duration requires
decoding the audio file (miniaudio), which takes ~10ms per file. For the tray
menu showing 10 entries, that is 100ms of blocking decode. Storing duration
at write time (when we already have the audio bytes in memory) avoids this.

---

## 10. Implementation Plan

### Phase 1: Core cache module (tts_cache.py)

New file: `src/tts_cache.py`

- `TTSAudioCache` class with `get()`, `put()`, `replay()`, `list_entries()`,
  `delete()`, `clear()`, `stats()`, `evict()`.
- `CacheKey` dataclass.
- `CacheEntry` TypedDict.
- Atomic JSON index read/write.
- LRU eviction logic.
- Index rebuild from directory scan on corruption.

### Phase 2: Pipeline integration (main.py, tts.py)

Modify `VoicePasteApp`:

- Add `self._tts_cache: Optional[TTSAudioCache]` instance variable.
- Initialize in `__init__` based on `config.tts_cache_enabled`.
- Modify `_run_tts_pipeline()` to check cache before synthesis.
- Modify `_run_pipeline()` (tts_ask mode) to check cache before synthesis.
- Add cache write after successful synthesis.
- Add startup eviction on a deferred thread.

### Phase 3: Configuration (constants.py, config.py)

- Add constants: `DEFAULT_TTS_CACHE_*`.
- Add `AppConfig` fields: `tts_cache_enabled`, `tts_cache_max_size_mb`,
  `tts_cache_max_age_days`, `tts_cache_max_entries`.
- Add `[tts_cache]` section to `save_to_toml()` and `load_config()`.
- Add `CONFIG_TEMPLATE` section.

### Phase 4: HTTP API extensions (api_server.py, main.py)

- Add route handlers for `/tts/history`, `/tts/history/{id}`,
  `/tts/replay/{id}`, `DELETE /tts/history/{id}`, `DELETE /tts/history`.
- Add `_api_dispatch()` cases for new actions.
- Add `"cached"` field to existing `/tts` response.

### Phase 5: Tray menu (tray.py)

- Add "Recent TTS" submenu with dynamic entries from `TTSAudioCache.list_entries()`.
- Add "Clear TTS Cache" menu item.
- Wire menu items to replay callback in `VoicePasteApp`.

### Phase 6: Settings dialog (settings_dialog.py)

- Add "TTS Cache" section with enable toggle, size/age/entries fields.
- Add "Clear Cache" and "Open Folder" buttons.
- Wire to `AppConfig` save and hot-reload.

### Phase 7: Tests

- Unit tests for `tts_cache.py`: get/put/evict/clear/rebuild/thread-safety.
- Integration tests for cache-through in the TTS pipeline (mock backend).
- API endpoint tests.

### File changes summary

| File                     | Change type | Description                          |
|--------------------------|-------------|--------------------------------------|
| `src/tts_cache.py`       | **NEW**     | Core cache module                    |
| `src/main.py`            | MODIFY      | Pipeline integration, replay wiring  |
| `src/constants.py`       | MODIFY      | New default constants                |
| `src/config.py`          | MODIFY      | New fields, TOML parsing, save       |
| `src/api_server.py`      | MODIFY      | New route handlers                   |
| `src/tray.py`            | MODIFY      | "Recent TTS" submenu                 |
| `src/settings_dialog.py` | MODIFY      | "TTS Cache" settings section         |
| `tests/test_tts_cache.py`| **NEW**     | Cache unit tests                     |

---

## 11. Risk Assessment

### R1: Index corruption on crash (LOW)

**Risk**: If the app crashes during `index.json` write, the file could be
truncated or empty.

**Mitigation**: Atomic write via `tmp + os.replace()`. If `index.json` is
unreadable on load, the cache rebuilds it from the directory listing. Worst
case: some metadata (text, play count) is lost for entries, but the audio
files themselves are intact.

### R2: Disk space consumption (LOW)

**Risk**: Unbounded cache fills the disk.

**Mitigation**: Configurable max size (default 200 MB) with LRU eviction.
Eviction runs after every `put()` and on startup. The user can also manually
clear the cache via Settings or API.

### R3: Stale audio for changed voices (LOW)

**Risk**: User changes ElevenLabs voice settings but the cache still returns
audio from the old voice.

**Mitigation**: The cache key includes `voice_id`. Changing the voice in
Settings produces a different cache key, so old entries are not returned.
Old entries are eventually evicted by age/size limits.

### R4: Privacy -- cached text stored in plaintext (MEDIUM)

**Risk**: The `index.json` contains the full text of every TTS request,
stored in plaintext on the local filesystem.

**Mitigation**: (a) The cache is in `%LOCALAPPDATA%`, which is per-user
and not world-readable. (b) The "Clear Cache" function wipes all files and
the index. (c) The `max_age_days` setting ensures old entries are
automatically purged. (d) The README should note that cached text is stored
locally and advise users handling sensitive text to disable the cache.

### R5: File handle exhaustion on large cache (VERY LOW)

**Risk**: Scanning 500 files for eviction opens many file handles.

**Mitigation**: The eviction logic reads file sizes from `os.stat()` (no
file handle opened) and deletes via `os.remove()` (briefly opens handle).
Python's garbage collector handles this efficiently.

### R6: PyInstaller bundling impact (NONE)

The cache uses only standard library modules (`json`, `hashlib`, `os`,
`pathlib`, `threading`, `datetime`). No new dependencies. No `.spec` file
changes needed. Zero impact on binary size.

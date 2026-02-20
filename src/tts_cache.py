"""TTS Audio Cache for Voice Paste.

Caches synthesized TTS audio files locally to avoid re-calling the TTS backend
for the same text. Saves API credits (ElevenLabs) and reduces latency.

Cache files are stored in %LOCALAPPDATA%\\VoicePaste\\cache\\tts\\ with SHA256-
based filenames for automatic deduplication. A JSON index tracks metadata.

v1.0: Initial implementation.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Valid entry_id: exactly 16 lowercase hex characters (output of SHA256[:16])
_VALID_ENTRY_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheKey:
    """Composite key for TTS audio cache lookup.

    The cache key includes provider and voice_id so the same text spoken by
    different voices produces different cache entries.
    """

    provider: str  # "elevenlabs" or "piper"
    voice_id: str  # ElevenLabs voice ID or Piper voice name
    text: str  # The exact input text (no normalization)

    def to_hash(self) -> str:
        """Generate the 16-char hex hash used as filename and entry_id."""
        raw = f"{self.provider}:{self.voice_id}:{self.text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class CacheConfig:
    """Configuration for the TTS audio cache."""

    enabled: bool = True
    max_size_mb: int = 200
    max_age_days: int = 30
    max_entries: int = 500


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _detect_audio_format(data: bytes) -> str:
    """Detect audio format from file header bytes.

    Returns "wav", "mp3", or "bin" (fallback).
    """
    if data[:4] == b"RIFF":
        return "wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return "bin"


def _estimate_duration(audio_data: bytes) -> float:
    """Estimate audio duration in seconds using miniaudio.

    Returns 0.0 if decoding fails (non-fatal).
    """
    try:
        import miniaudio

        decoded = miniaudio.decode(
            audio_data,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=44100,
        )
        return len(decoded.samples) / decoded.sample_rate / decoded.nchannels
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# TTSAudioCache
# ---------------------------------------------------------------------------


class TTSAudioCache:
    """Thread-safe TTS audio cache with LRU eviction.

    Stores audio files in a local directory with a JSON index for metadata.
    The cache is transparent: get() returns None on miss, put() stores on write.
    """

    def __init__(self, config: CacheConfig, cache_dir: Optional[Path] = None) -> None:
        """Initialize the cache.

        Args:
            config: Cache configuration.
            cache_dir: Override cache directory (default: %LOCALAPPDATA%\\VoicePaste\\cache\\tts).
        """
        self._config = config
        self._lock = threading.Lock()

        if cache_dir is not None:
            self._cache_dir = cache_dir
        else:
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            if not local_appdata:
                local_appdata = str(Path.home() / "AppData" / "Local")
            self._cache_dir = Path(local_appdata) / "VoicePaste" / "cache" / "tts"

        self._index_path = self._cache_dir / "index.json"
        self._index: dict[str, Any] = {"version": 1, "entries": {}}

        if self._config.enabled:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_index()

    # -----------------------------------------------------------------------
    # Index I/O
    # -----------------------------------------------------------------------

    def _load_index(self) -> None:
        """Load index.json from disk. Rebuild from directory on failure."""
        if not self._index_path.exists():
            logger.debug("TTS cache: no index.json found, starting fresh.")
            return

        try:
            raw = self._index_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and "entries" in data:
                self._index = data
                logger.info(
                    "TTS cache loaded: %d entries.", len(self._index["entries"])
                )
            else:
                logger.warning("TTS cache: index.json has unexpected format, rebuilding.")
                self._rebuild_index()
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("TTS cache: index.json corrupt (%s), rebuilding.", e)
            self._rebuild_index()

    def _save_index(self) -> None:
        """Write index.json atomically (tmp + os.replace)."""
        try:
            tmp_path = self._index_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self._index_path)
        except OSError as e:
            logger.error("TTS cache: failed to save index: %s", e)

    def _rebuild_index(self) -> None:
        """Rebuild index from audio files in the cache directory."""
        self._index = {"version": 1, "entries": {}}
        if not self._cache_dir.exists():
            return

        count = 0
        for f in self._cache_dir.iterdir():
            if f.suffix in (".mp3", ".wav", ".bin") and f.stem != "index":
                entry_id = f.stem
                stat = f.stat()
                self._index["entries"][entry_id] = {
                    "text": "(recovered)",
                    "text_preview": "(recovered)",
                    "provider": "unknown",
                    "voice_id": "unknown",
                    "voice_label": "unknown",
                    "format": f.suffix.lstrip("."),
                    "file_size_bytes": stat.st_size,
                    "duration_seconds": 0.0,
                    "created_at": datetime.fromtimestamp(
                        stat.st_ctime, tz=timezone.utc
                    ).isoformat(),
                    "last_played_at": datetime.fromtimestamp(
                        stat.st_ctime, tz=timezone.utc
                    ).isoformat(),
                    "play_count": 0,
                }
                count += 1

        if count:
            self._save_index()
            logger.info("TTS cache: rebuilt index with %d recovered entries.", count)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get(self, key: CacheKey) -> Optional[bytes]:
        """Look up cached audio by key.

        Returns audio bytes on hit, None on miss. Updates last_played_at
        and play_count on hit.
        """
        if not self._config.enabled:
            return None

        entry_id = key.to_hash()

        with self._lock:
            entry = self._index["entries"].get(entry_id)
            if entry is None:
                return None

        # Read file outside lock
        file_path = self._cache_dir / f"{entry_id}.{entry['format']}"
        if not file_path.exists():
            # Stale index entry -- remove it
            with self._lock:
                self._index["entries"].pop(entry_id, None)
                self._save_index()
            return None

        try:
            audio_data = file_path.read_bytes()
        except OSError as e:
            logger.warning("TTS cache: failed to read %s: %s", file_path, e)
            return None

        # Update access metadata
        with self._lock:
            if entry_id in self._index["entries"]:
                self._index["entries"][entry_id]["last_played_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                self._index["entries"][entry_id]["play_count"] = (
                    entry.get("play_count", 0) + 1
                )
                self._save_index()

        logger.info("TTS cache hit: %s (%d bytes)", entry_id, len(audio_data))
        return audio_data

    def put(
        self,
        key: CacheKey,
        audio_data: bytes,
        voice_label: str = "",
    ) -> str:
        """Store audio bytes in the cache.

        Args:
            key: Cache key (provider, voice_id, text).
            audio_data: Raw audio bytes (MP3 or WAV).
            voice_label: Human-readable voice name for display.

        Returns:
            The entry_id (16-char hex hash).
        """
        if not self._config.enabled:
            return key.to_hash()

        entry_id = key.to_hash()
        fmt = _detect_audio_format(audio_data)
        file_path = self._cache_dir / f"{entry_id}.{fmt}"

        # Write file outside lock (atomic via tmp + replace)
        try:
            tmp_path = file_path.with_suffix(f".{fmt}.tmp")
            tmp_path.write_bytes(audio_data)
            tmp_path.replace(file_path)
        except OSError as e:
            logger.error("TTS cache: failed to write %s: %s", file_path, e)
            return entry_id

        duration = _estimate_duration(audio_data)
        text_preview = key.text[:80]
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self._index["entries"][entry_id] = {
                "text": key.text,
                "text_preview": text_preview,
                "provider": key.provider,
                "voice_id": key.voice_id,
                "voice_label": voice_label or key.voice_id,
                "format": fmt,
                "file_size_bytes": len(audio_data),
                "duration_seconds": round(duration, 1),
                "created_at": now,
                "last_played_at": now,
                "play_count": 0,
            }
            self._evict_if_needed()
            self._save_index()

        logger.info(
            "TTS cache put: %s (%d bytes, %.1fs, %s)",
            entry_id,
            len(audio_data),
            duration,
            fmt,
        )
        return entry_id

    @staticmethod
    def _is_valid_entry_id(entry_id: str) -> bool:
        """Validate that entry_id is a safe 16-char hex string."""
        return bool(_VALID_ENTRY_ID_RE.match(entry_id))

    def replay(self, entry_id: str) -> Optional[bytes]:
        """Load cached audio bytes by entry ID for replay.

        Updates last_played_at and play_count.

        Returns:
            Audio bytes, or None if entry not found or entry_id invalid.
        """
        if not self._is_valid_entry_id(entry_id):
            logger.warning("TTS cache: invalid entry_id rejected: %r", entry_id[:40])
            return None

        with self._lock:
            entry = self._index["entries"].get(entry_id)
            if entry is None:
                return None

        file_path = self._cache_dir / f"{entry_id}.{entry['format']}"
        if not file_path.exists():
            with self._lock:
                self._index["entries"].pop(entry_id, None)
                self._save_index()
            return None

        try:
            audio_data = file_path.read_bytes()
        except OSError as e:
            logger.warning("TTS cache: failed to read %s: %s", file_path, e)
            return None

        with self._lock:
            if entry_id in self._index["entries"]:
                self._index["entries"][entry_id]["last_played_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                self._index["entries"][entry_id]["play_count"] = (
                    entry.get("play_count", 0) + 1
                )
                self._save_index()

        logger.info("TTS cache replay: %s (%d bytes)", entry_id, len(audio_data))
        return audio_data

    def get_entry(self, entry_id: str) -> Optional[dict[str, Any]]:
        """Get metadata for a single cache entry."""
        if not self._is_valid_entry_id(entry_id):
            return None

        with self._lock:
            entry = self._index["entries"].get(entry_id)
            if entry is None:
                return None
            return {"id": entry_id, **entry}

    def list_entries(self, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
        """List cache entries sorted by last_played_at (newest first).

        Args:
            limit: Max entries to return.
            offset: Number of entries to skip.

        Returns:
            List of entry dicts with 'id' field added.
        """
        with self._lock:
            entries = [
                {"id": eid, **meta}
                for eid, meta in self._index["entries"].items()
            ]

        entries.sort(key=lambda e: e.get("last_played_at", ""), reverse=True)
        return entries[offset : offset + limit]

    def delete(self, entry_id: str) -> bool:
        """Delete a single cache entry (file + index).

        Returns True if the entry existed, False if not found or invalid ID.
        """
        if not self._is_valid_entry_id(entry_id):
            logger.warning("TTS cache: invalid entry_id rejected: %r", entry_id[:40])
            return False

        with self._lock:
            entry = self._index["entries"].pop(entry_id, None)
            if entry is None:
                return False
            self._save_index()

        # Delete file outside lock
        for ext in ("mp3", "wav", "bin"):
            p = self._cache_dir / f"{entry_id}.{ext}"
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

        logger.info("TTS cache: deleted entry %s", entry_id)
        return True

    def clear(self) -> int:
        """Delete all cache entries. Returns the count of deleted entries."""
        with self._lock:
            count = len(self._index["entries"])
            self._index["entries"] = {}
            self._save_index()

        # Delete all audio files
        if self._cache_dir.exists():
            for f in self._cache_dir.iterdir():
                if f.suffix in (".mp3", ".wav", ".bin"):
                    try:
                        f.unlink()
                    except OSError:
                        pass

        logger.info("TTS cache cleared: %d entries removed.", count)
        return count

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            entries = self._index["entries"]
            total_bytes = sum(e.get("file_size_bytes", 0) for e in entries.values())
            return {
                "total_entries": len(entries),
                "total_size_mb": round(total_bytes / (1024 * 1024), 1),
                "total_size_bytes": total_bytes,
                "cache_enabled": self._config.enabled,
                "max_size_mb": self._config.max_size_mb,
                "max_entries": self._config.max_entries,
                "max_age_days": self._config.max_age_days,
                "cache_dir": str(self._cache_dir),
            }

    # -----------------------------------------------------------------------
    # Eviction
    # -----------------------------------------------------------------------

    def evict(self) -> int:
        """Run eviction manually (e.g., at startup). Returns count of evicted entries."""
        with self._lock:
            count = self._evict_if_needed()
            if count:
                self._save_index()
        return count

    def _evict_if_needed(self) -> int:
        """Run LRU eviction. Must be called with self._lock held.

        Returns count of evicted entries.
        """
        entries = self._index["entries"]
        evicted = 0

        # Phase 1: Remove entries older than max_age_days
        if self._config.max_age_days > 0:
            cutoff = time.time() - (self._config.max_age_days * 86400)
            to_remove = []
            for eid, meta in entries.items():
                last_played = meta.get("last_played_at", meta.get("created_at", ""))
                try:
                    ts = datetime.fromisoformat(last_played).timestamp()
                except (ValueError, TypeError):
                    ts = 0
                if ts < cutoff:
                    to_remove.append(eid)

            for eid in to_remove:
                self._delete_file(eid, entries[eid])
                del entries[eid]
                evicted += 1

        # Phase 2: Evict by size and count (LRU)
        max_bytes = self._config.max_size_mb * 1024 * 1024
        max_entries = self._config.max_entries if self._config.max_entries > 0 else float("inf")

        total_bytes = sum(e.get("file_size_bytes", 0) for e in entries.values())

        while (total_bytes > max_bytes or len(entries) > max_entries) and entries:
            # Find the LRU entry (oldest last_played_at)
            oldest_id = min(
                entries,
                key=lambda eid: entries[eid].get(
                    "last_played_at", entries[eid].get("created_at", "")
                ),
            )
            total_bytes -= entries[oldest_id].get("file_size_bytes", 0)
            self._delete_file(oldest_id, entries[oldest_id])
            del entries[oldest_id]
            evicted += 1

        if evicted:
            logger.info("TTS cache: evicted %d entries.", evicted)

        return evicted

    def _delete_file(self, entry_id: str, entry: dict[str, Any]) -> None:
        """Delete the audio file for an entry. Non-fatal on error."""
        fmt = entry.get("format", "bin")
        p = self._cache_dir / f"{entry_id}.{fmt}"
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

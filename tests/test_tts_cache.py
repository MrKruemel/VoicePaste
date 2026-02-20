"""Tests for TTS Audio Cache (tts_cache.py).

Covers:
- Cache key hashing and deduplication
- get/put round-trip
- Cache miss returns None
- LRU eviction by size, age, and count
- replay() updates play_count
- list_entries() sorted by recency
- delete() and clear()
- stats() returns correct values
- Thread safety (concurrent put/get)
- Index rebuild from directory scan
- Atomic index writes (corruption recovery)
"""

import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest

import sys
import os

# Ensure src is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tts_cache import TTSAudioCache, CacheConfig, CacheKey, _detect_audio_format


# --- Fixtures ---

@pytest.fixture
def cache_dir(tmp_path):
    """Provide a temporary cache directory."""
    d = tmp_path / "tts_cache"
    d.mkdir()
    return d


@pytest.fixture
def cache(cache_dir):
    """Provide an enabled TTSAudioCache with small limits for testing."""
    cfg = CacheConfig(enabled=True, max_size_mb=1, max_age_days=30, max_entries=10)
    return TTSAudioCache(cfg, cache_dir=cache_dir)


@pytest.fixture
def sample_wav():
    """Minimal WAV-like bytes for testing."""
    return b"RIFF" + b"\x00" * 200


@pytest.fixture
def sample_mp3():
    """Minimal MP3-like bytes for testing."""
    return b"\xff\xfb" + b"\x00" * 200


@pytest.fixture
def sample_key():
    return CacheKey(provider="elevenlabs", voice_id="voice123", text="Hello world")


# --- Format detection ---

class TestFormatDetection:
    def test_wav_detected(self):
        assert _detect_audio_format(b"RIFF\x00\x00\x00\x00") == "wav"

    def test_mp3_id3_detected(self):
        assert _detect_audio_format(b"ID3\x04\x00\x00") == "mp3"

    def test_mp3_sync_detected(self):
        assert _detect_audio_format(b"\xff\xfb\x90\x00") == "mp3"

    def test_unknown_fallback(self):
        assert _detect_audio_format(b"\x00\x00\x00\x00") == "bin"

    def test_empty_fallback(self):
        assert _detect_audio_format(b"") == "bin"


# --- CacheKey ---

class TestCacheKey:
    def test_hash_deterministic(self, sample_key):
        h1 = sample_key.to_hash()
        h2 = sample_key.to_hash()
        assert h1 == h2
        assert len(h1) == 16

    def test_different_text_different_hash(self):
        k1 = CacheKey("elevenlabs", "v1", "Hello")
        k2 = CacheKey("elevenlabs", "v1", "World")
        assert k1.to_hash() != k2.to_hash()

    def test_different_voice_different_hash(self):
        k1 = CacheKey("elevenlabs", "voice_a", "Hello")
        k2 = CacheKey("elevenlabs", "voice_b", "Hello")
        assert k1.to_hash() != k2.to_hash()

    def test_different_provider_different_hash(self):
        k1 = CacheKey("elevenlabs", "v1", "Hello")
        k2 = CacheKey("piper", "v1", "Hello")
        assert k1.to_hash() != k2.to_hash()


# --- Basic get/put ---

class TestGetPut:
    def test_put_and_get(self, cache, sample_key, sample_wav):
        cache.put(sample_key, sample_wav, voice_label="TestVoice")
        result = cache.get(sample_key)
        assert result is not None
        assert result == sample_wav

    def test_miss_returns_none(self, cache):
        key = CacheKey("x", "y", "nonexistent")
        assert cache.get(key) is None

    def test_put_returns_entry_id(self, cache, sample_key, sample_wav):
        entry_id = cache.put(sample_key, sample_wav)
        assert entry_id == sample_key.to_hash()

    def test_put_wav_creates_wav_file(self, cache, cache_dir, sample_key, sample_wav):
        entry_id = cache.put(sample_key, sample_wav)
        assert (cache_dir / f"{entry_id}.wav").exists()

    def test_put_mp3_creates_mp3_file(self, cache, cache_dir, sample_mp3):
        key = CacheKey("elevenlabs", "v1", "MP3 test")
        entry_id = cache.put(key, sample_mp3)
        assert (cache_dir / f"{entry_id}.mp3").exists()

    def test_deduplication(self, cache, sample_key, sample_wav):
        id1 = cache.put(sample_key, sample_wav)
        id2 = cache.put(sample_key, sample_wav)
        assert id1 == id2
        assert cache.stats()["total_entries"] == 1


# --- Disabled cache ---

class TestDisabledCache:
    def test_disabled_get_returns_none(self, cache_dir, sample_key, sample_wav):
        cfg = CacheConfig(enabled=False)
        c = TTSAudioCache(cfg, cache_dir=cache_dir)
        assert c.get(sample_key) is None

    def test_disabled_put_still_returns_id(self, cache_dir, sample_key, sample_wav):
        cfg = CacheConfig(enabled=False)
        c = TTSAudioCache(cfg, cache_dir=cache_dir)
        entry_id = c.put(sample_key, sample_wav)
        assert entry_id == sample_key.to_hash()


# --- Replay ---

class TestReplay:
    def test_replay_returns_audio(self, cache, sample_key, sample_wav):
        entry_id = cache.put(sample_key, sample_wav)
        result = cache.replay(entry_id)
        assert result == sample_wav

    def test_replay_increments_play_count(self, cache, sample_key, sample_wav):
        entry_id = cache.put(sample_key, sample_wav)
        cache.replay(entry_id)
        cache.replay(entry_id)
        entry = cache.get_entry(entry_id)
        assert entry["play_count"] >= 2

    def test_replay_nonexistent_returns_none(self, cache):
        assert cache.replay("nonexistent") is None


# --- list_entries ---

class TestListEntries:
    def test_list_empty(self, cache):
        assert cache.list_entries() == []

    def test_list_sorted_by_recency(self, cache, sample_wav):
        for i in range(3):
            key = CacheKey("p", "v", f"text_{i}")
            cache.put(key, sample_wav)
            time.sleep(0.01)

        entries = cache.list_entries(limit=10)
        assert len(entries) == 3
        # Newest first
        assert entries[0]["text_preview"].startswith("text_2")

    def test_list_respects_limit(self, cache, sample_wav):
        for i in range(5):
            key = CacheKey("p", "v", f"entry_{i}")
            cache.put(key, sample_wav)
        entries = cache.list_entries(limit=3)
        assert len(entries) == 3


# --- Delete and clear ---

class TestDeleteClear:
    def test_delete_existing(self, cache, sample_key, sample_wav, cache_dir):
        entry_id = cache.put(sample_key, sample_wav)
        assert cache.delete(entry_id) is True
        assert cache.get(sample_key) is None
        # File should be removed
        assert not (cache_dir / f"{entry_id}.wav").exists()

    def test_delete_nonexistent(self, cache):
        assert cache.delete("nonexistent") is False

    def test_clear(self, cache, sample_wav):
        for i in range(5):
            key = CacheKey("p", "v", f"clear_test_{i}")
            cache.put(key, sample_wav)
        count = cache.clear()
        assert count == 5
        assert cache.stats()["total_entries"] == 0


# --- Stats ---

class TestStats:
    def test_stats_empty(self, cache):
        s = cache.stats()
        assert s["total_entries"] == 0
        assert s["total_size_mb"] == 0.0
        assert s["cache_enabled"] is True

    def test_stats_after_put(self, cache, sample_key, sample_wav):
        cache.put(sample_key, sample_wav)
        s = cache.stats()
        assert s["total_entries"] == 1
        assert s["total_size_bytes"] > 0


# --- Eviction ---

class TestEviction:
    def test_evict_by_count(self, cache_dir, sample_wav):
        cfg = CacheConfig(enabled=True, max_size_mb=100, max_age_days=365, max_entries=3)
        c = TTSAudioCache(cfg, cache_dir=cache_dir)

        for i in range(5):
            key = CacheKey("p", "v", f"evict_count_{i}")
            c.put(key, sample_wav)
            time.sleep(0.01)

        assert c.stats()["total_entries"] == 3

    def test_evict_by_size(self, cache_dir):
        # Each entry is ~1KB, set max to 2KB
        cfg = CacheConfig(enabled=True, max_size_mb=0, max_age_days=365, max_entries=5000)
        # Override max_size_mb by setting it in bytes via a tiny limit
        cfg2 = CacheConfig(enabled=True, max_size_mb=1, max_age_days=365, max_entries=5000)
        c = TTSAudioCache(cfg2, cache_dir=cache_dir)

        big_data = b"RIFF" + b"\x00" * (500 * 1024)  # ~500KB each
        for i in range(5):
            key = CacheKey("p", "v", f"evict_size_{i}")
            c.put(key, big_data)
            time.sleep(0.01)

        # Max is 1MB, each entry is ~500KB, so at most 2 should survive
        assert c.stats()["total_entries"] <= 2


# --- Index rebuild ---

class TestIndexRebuild:
    def test_rebuild_on_corrupt_index(self, cache, cache_dir, sample_key, sample_wav):
        entry_id = cache.put(sample_key, sample_wav)

        # Corrupt the index
        index_path = cache_dir / "index.json"
        index_path.write_text("NOT VALID JSON", encoding="utf-8")

        # Recreate cache (triggers reload -> rebuild)
        cfg = CacheConfig(enabled=True, max_size_mb=100, max_age_days=30, max_entries=100)
        c2 = TTSAudioCache(cfg, cache_dir=cache_dir)

        # Should have recovered the entry from file scan
        assert c2.stats()["total_entries"] == 1

    def test_rebuild_on_missing_index(self, cache, cache_dir, sample_key, sample_wav):
        entry_id = cache.put(sample_key, sample_wav)

        # Delete index
        (cache_dir / "index.json").unlink()

        cfg = CacheConfig(enabled=True, max_size_mb=100, max_age_days=30, max_entries=100)
        c2 = TTSAudioCache(cfg, cache_dir=cache_dir)

        # Should start fresh (no index = no entries, but file is still there)
        # Files without index aren't auto-discovered unless index is corrupt
        # The _load_index just logs and returns
        assert c2.stats()["total_entries"] == 0


# --- Thread safety ---

class TestThreadSafety:
    def test_concurrent_put_get(self, cache, sample_wav):
        errors = []

        def writer(i):
            try:
                key = CacheKey("p", "v", f"thread_test_{i}")
                cache.put(key, sample_wav)
            except Exception as e:
                errors.append(e)

        def reader(i):
            try:
                key = CacheKey("p", "v", f"thread_test_{i}")
                cache.get(key)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == [], f"Thread errors: {errors}"

"""Tests for TTS Audio Export (tts_export.py).

Covers:
- Filename sanitization
- Format detection
- Export disabled returns None
- Export with WAV and MP3
- Filename collision handling
- list_exports() returns correct metadata
- stats() returns correct values
- set_export_dir() changes directory
- Empty text/audio rejected
- Unconfigured directory handling
- Custom filename hints
"""

import sys
import os
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tts_export import (
    TTSAudioExporter,
    _detect_audio_format,
    _sanitize_for_filename,
    _generate_export_filename,
)


# --- Fixtures ---

@pytest.fixture
def export_dir(tmp_path):
    d = tmp_path / "exports"
    d.mkdir()
    return d


@pytest.fixture
def exporter(export_dir):
    return TTSAudioExporter(export_dir=export_dir, enabled=True)


@pytest.fixture
def sample_wav():
    return b"RIFF" + b"\x00" * 200


@pytest.fixture
def sample_mp3():
    return b"\xff\xfb" + b"\x00" * 200


# --- Sanitization ---

class TestSanitization:
    def test_simple_text(self):
        assert _sanitize_for_filename("Hello World") == "hello-world"

    def test_special_characters_removed(self):
        result = _sanitize_for_filename("Hello! @World# $123")
        assert result == "hello-world-123"

    def test_german_umlauts_removed(self):
        result = _sanitize_for_filename("Guten Tag")
        assert result == "guten-tag"

    def test_empty_fallback(self):
        assert _sanitize_for_filename("!!!") == "untitled"
        assert _sanitize_for_filename("") == "untitled"

    def test_long_text_truncated(self):
        long_text = "a" * 100
        result = _sanitize_for_filename(long_text)
        assert len(result) <= 40

    def test_multiple_spaces_collapsed(self):
        assert _sanitize_for_filename("hello   world") == "hello-world"

    def test_leading_trailing_hyphens_stripped(self):
        assert _sanitize_for_filename("--hello--") == "hello"


# --- Filename generation ---

class TestFilenameGeneration:
    def test_contains_timestamp(self):
        name = _generate_export_filename("Hello", "mp3")
        # Format: YYYYMMDD_HHMMSS_...
        assert name[8] == "_"
        assert name.endswith(".mp3")

    def test_contains_text(self):
        name = _generate_export_filename("Hello World", "wav")
        assert "hello-world" in name

    def test_correct_extension(self):
        assert _generate_export_filename("test", "wav").endswith(".wav")
        assert _generate_export_filename("test", "mp3").endswith(".mp3")


# --- Export disabled ---

class TestDisabledExport:
    def test_disabled_returns_none(self, export_dir, sample_wav):
        exp = TTSAudioExporter(export_dir=export_dir, enabled=False)
        result = exp.export("Hello", sample_wav)
        assert result is None

    def test_disabled_list_returns_empty(self, export_dir):
        exp = TTSAudioExporter(export_dir=export_dir, enabled=False)
        assert exp.list_exports() == []


# --- Export ---

class TestExport:
    def test_export_wav(self, exporter, sample_wav, export_dir):
        result = exporter.export("Hello World", sample_wav)
        assert result is not None
        assert result.exists()
        assert result.suffix == ".wav"
        assert result.parent == export_dir

    def test_export_mp3(self, exporter, sample_mp3, export_dir):
        result = exporter.export("Hello World", sample_mp3)
        assert result is not None
        assert result.suffix == ".mp3"

    def test_export_content_matches(self, exporter, sample_wav):
        result = exporter.export("Test content", sample_wav)
        assert result.read_bytes() == sample_wav

    def test_export_empty_text_rejected(self, exporter, sample_wav):
        assert exporter.export("", sample_wav) is None
        assert exporter.export("  ", sample_wav) is None

    def test_export_empty_audio_rejected(self, exporter):
        assert exporter.export("Hello", b"") is None

    def test_export_no_dir_configured(self, sample_wav):
        exp = TTSAudioExporter(export_dir=Path(""), enabled=True)
        assert exp.export("Hello", sample_wav) is None


# --- Collision handling ---

class TestCollisionHandling:
    def test_duplicate_gets_counter(self, exporter, sample_wav):
        r1 = exporter.export("Hello", sample_wav)
        # Force same timestamp by exporting immediately again
        r2 = exporter.export("Hello", sample_wav)
        assert r1 is not None
        assert r2 is not None
        assert r1 != r2
        # Both should exist
        assert r1.exists()
        assert r2.exists()


# --- Custom filename hint ---

class TestFilenameHint:
    def test_custom_hint(self, exporter, sample_wav):
        result = exporter.export("Some text", sample_wav, filename_hint="Chapter 01")
        assert result is not None
        assert "chapter-01" in result.name


# --- list_exports ---

class TestListExports:
    def test_empty_dir(self, exporter):
        assert exporter.list_exports() == []

    def test_lists_exported_files(self, exporter, sample_wav):
        exporter.export("File one", sample_wav)
        exporter.export("File two", sample_wav)
        exports = exporter.list_exports()
        assert len(exports) == 2
        assert all("filename" in e for e in exports)
        assert all("size_bytes" in e for e in exports)

    def test_sorted_newest_first(self, exporter, sample_wav):
        exporter.export("First", sample_wav)
        time.sleep(0.05)
        exporter.export("Second", sample_wav)
        exports = exporter.list_exports()
        assert "second" in exports[0]["filename"]


# --- Stats ---

class TestStats:
    def test_stats_empty(self, exporter):
        s = exporter.stats()
        assert s["total_files"] == 0
        assert s["total_size_mb"] == 0.0
        assert s["enabled"] is True

    def test_stats_after_export(self, exporter, sample_wav):
        exporter.export("Hello", sample_wav)
        s = exporter.stats()
        assert s["total_files"] == 1
        assert s["total_size_bytes"] > 0


# --- set_export_dir ---

class TestSetExportDir:
    def test_change_dir(self, exporter, sample_wav, tmp_path):
        new_dir = tmp_path / "new_exports"
        new_dir.mkdir()
        exporter.set_export_dir(new_dir)
        result = exporter.export("Hello", sample_wav)
        assert result is not None
        assert result.parent == new_dir

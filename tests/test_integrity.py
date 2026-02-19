"""Tests for the SHA256 integrity verification module (src/integrity.py).

Covers:
    - compute_file_sha256: basic hashing, empty file, large file (chunked), missing file
    - verify_file_sha256: match, mismatch, case insensitivity, file errors
    - verify_directory_files: all pass, partial fail, missing file, empty hashes (graceful degradation)
    - _log_directory_hashes: non-existent directory, mixed files and dirs
"""

import hashlib
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from integrity import (
    compute_file_sha256,
    verify_file_sha256,
    verify_directory_files,
    _log_directory_hashes,
    _HASH_CHUNK_SIZE,
)


# ---------------------------------------------------------------------------
# compute_file_sha256
# ---------------------------------------------------------------------------

class TestComputeFileSha256:
    """Tests for compute_file_sha256."""

    def test_known_content_hash(self, tmp_path):
        """SHA256 of known content matches the standard library result."""
        content = b"Hello, Voice Paste!"
        expected = hashlib.sha256(content).hexdigest()
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(content)

        result = compute_file_sha256(file_path)

        assert result == expected
        assert result == expected.lower()

    def test_empty_file_hash(self, tmp_path):
        """SHA256 of an empty file is the well-known empty hash."""
        file_path = tmp_path / "empty.bin"
        file_path.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()

        result = compute_file_sha256(file_path)

        assert result == expected

    def test_large_file_chunked_read(self, tmp_path):
        """File larger than _HASH_CHUNK_SIZE is hashed correctly via chunked read."""
        # Create a file that spans multiple chunks (2.5x chunk size)
        content = os.urandom(int(_HASH_CHUNK_SIZE * 2.5))
        expected = hashlib.sha256(content).hexdigest()
        file_path = tmp_path / "large.bin"
        file_path.write_bytes(content)

        result = compute_file_sha256(file_path)

        assert result == expected

    def test_returns_lowercase_hex(self, tmp_path):
        """Result is always lowercase hexadecimal."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("test", encoding="utf-8")

        result = compute_file_sha256(file_path)

        assert result == result.lower()
        # SHA256 hex digest is exactly 64 characters
        assert len(result) == 64

    def test_file_not_found_raises(self, tmp_path):
        """FileNotFoundError is raised for nonexistent file."""
        file_path = tmp_path / "nonexistent.bin"

        with pytest.raises(FileNotFoundError):
            compute_file_sha256(file_path)

    def test_binary_content_hash(self, tmp_path):
        """Binary file with null bytes is hashed correctly."""
        content = b"\x00\x01\x02\xff\xfe\xfd" * 100
        expected = hashlib.sha256(content).hexdigest()
        file_path = tmp_path / "binary.bin"
        file_path.write_bytes(content)

        result = compute_file_sha256(file_path)

        assert result == expected


# ---------------------------------------------------------------------------
# verify_file_sha256
# ---------------------------------------------------------------------------

class TestVerifyFileSha256:
    """Tests for verify_file_sha256."""

    def test_matching_hash_returns_true(self, tmp_path):
        """Returns True when computed hash matches expected hash."""
        content = b"matching content"
        expected = hashlib.sha256(content).hexdigest()
        file_path = tmp_path / "match.bin"
        file_path.write_bytes(content)

        assert verify_file_sha256(file_path, expected) is True

    def test_mismatched_hash_returns_false(self, tmp_path):
        """Returns False when computed hash does not match expected hash."""
        file_path = tmp_path / "mismatch.bin"
        file_path.write_bytes(b"actual content")

        wrong_hash = "0" * 64  # Definitely wrong

        assert verify_file_sha256(file_path, wrong_hash) is False

    def test_case_insensitive_comparison(self, tmp_path):
        """Comparison is case-insensitive (uppercase expected matches lowercase computed)."""
        content = b"case test"
        expected_lower = hashlib.sha256(content).hexdigest()
        expected_upper = expected_lower.upper()
        file_path = tmp_path / "case.bin"
        file_path.write_bytes(content)

        assert verify_file_sha256(file_path, expected_upper) is True

    def test_file_not_found_returns_false(self, tmp_path):
        """Returns False (not raises) when file does not exist."""
        file_path = tmp_path / "missing.bin"

        result = verify_file_sha256(file_path, "a" * 64)

        assert result is False

    def test_os_error_returns_false(self, tmp_path):
        """Returns False when file cannot be read (OSError)."""
        file_path = tmp_path / "unreadable.bin"
        file_path.write_bytes(b"content")

        with patch("integrity.compute_file_sha256", side_effect=OSError("Permission denied")):
            result = verify_file_sha256(file_path, "a" * 64)

        assert result is False

    def test_logs_computed_hash(self, tmp_path, caplog):
        """Logs the computed SHA256 at INFO level."""
        content = b"log test"
        expected = hashlib.sha256(content).hexdigest()
        file_path = tmp_path / "logtest.bin"
        file_path.write_bytes(content)

        import logging
        with caplog.at_level(logging.INFO, logger="integrity"):
            verify_file_sha256(file_path, expected)

        assert expected in caplog.text

    def test_logs_mismatch_error(self, tmp_path, caplog):
        """Logs a SHA256 MISMATCH error on hash mismatch."""
        file_path = tmp_path / "mismatch_log.bin"
        file_path.write_bytes(b"content")

        import logging
        with caplog.at_level(logging.ERROR, logger="integrity"):
            verify_file_sha256(file_path, "0" * 64)

        assert "MISMATCH" in caplog.text


# ---------------------------------------------------------------------------
# verify_directory_files
# ---------------------------------------------------------------------------

class TestVerifyDirectoryFiles:
    """Tests for verify_directory_files."""

    def test_all_files_match(self, tmp_path):
        """Returns True when all files match their expected hashes."""
        file_a = tmp_path / "model.bin"
        file_b = tmp_path / "config.json"
        file_a.write_bytes(b"model data")
        file_b.write_bytes(b'{"key": "value"}')

        expected_hashes = {
            "model.bin": hashlib.sha256(b"model data").hexdigest(),
            "config.json": hashlib.sha256(b'{"key": "value"}').hexdigest(),
        }

        result = verify_directory_files(tmp_path, expected_hashes)

        assert result is True

    def test_one_file_mismatches(self, tmp_path):
        """Returns False when one file has a mismatched hash."""
        file_a = tmp_path / "model.bin"
        file_b = tmp_path / "config.json"
        file_a.write_bytes(b"model data")
        file_b.write_bytes(b'{"corrupted": true}')

        expected_hashes = {
            "model.bin": hashlib.sha256(b"model data").hexdigest(),
            "config.json": hashlib.sha256(b'{"key": "value"}').hexdigest(),  # Won't match
        }

        result = verify_directory_files(tmp_path, expected_hashes)

        assert result is False

    def test_missing_file_returns_false(self, tmp_path):
        """Returns False when an expected file is missing."""
        file_a = tmp_path / "model.bin"
        file_a.write_bytes(b"model data")

        expected_hashes = {
            "model.bin": hashlib.sha256(b"model data").hexdigest(),
            "missing.json": "a" * 64,
        }

        result = verify_directory_files(tmp_path, expected_hashes)

        assert result is False

    def test_empty_hashes_returns_true_graceful_degradation(self, tmp_path):
        """Empty expected_hashes dict returns True (skip verification)."""
        result = verify_directory_files(tmp_path, {})

        assert result is True

    def test_empty_hashes_logs_warning(self, tmp_path, caplog):
        """Empty expected_hashes dict logs a warning about skipped verification."""
        import logging
        with caplog.at_level(logging.WARNING, logger="integrity"):
            verify_directory_files(tmp_path, {})

        assert "skipped" in caplog.text.lower() or "Integrity verification skipped" in caplog.text

    def test_empty_hashes_logs_computed_hashes_for_collection(self, tmp_path, caplog):
        """Empty expected_hashes triggers logging of existing file hashes for collection."""
        file_a = tmp_path / "model.bin"
        file_a.write_bytes(b"model data")

        import logging
        with caplog.at_level(logging.INFO, logger="integrity"):
            verify_directory_files(tmp_path, {})

        # Should log the computed hash for collection
        assert "Computed SHA256 for collection" in caplog.text
        assert "model.bin" in caplog.text

    def test_all_files_pass_returns_true(self, tmp_path):
        """Returns True when verifying a single file that matches."""
        file_a = tmp_path / "config.json"
        file_a.write_bytes(b"config")

        expected_hashes = {
            "config.json": hashlib.sha256(b"config").hexdigest(),
        }

        assert verify_directory_files(tmp_path, expected_hashes) is True

    def test_verifies_each_file_independently(self, tmp_path):
        """Each file is verified independently; one failure does not skip others."""
        file_a = tmp_path / "a.bin"
        file_b = tmp_path / "b.bin"
        file_a.write_bytes(b"aaa")
        file_b.write_bytes(b"bbb")

        expected_hashes = {
            "a.bin": "0" * 64,  # Will fail
            "b.bin": hashlib.sha256(b"bbb").hexdigest(),  # Will pass
        }

        # Should return False because a.bin fails, even though b.bin passes
        result = verify_directory_files(tmp_path, expected_hashes)
        assert result is False


# ---------------------------------------------------------------------------
# _log_directory_hashes
# ---------------------------------------------------------------------------

class TestLogDirectoryHashes:
    """Tests for _log_directory_hashes."""

    def test_nonexistent_directory_does_not_raise(self, tmp_path):
        """Does not raise when directory does not exist."""
        nonexistent = tmp_path / "nope"

        # Should not raise
        _log_directory_hashes(nonexistent)

    def test_logs_hashes_of_files(self, tmp_path, caplog):
        """Logs SHA256 hashes for all files in the directory."""
        file_a = tmp_path / "model.bin"
        file_a.write_bytes(b"model data")
        expected_hash = hashlib.sha256(b"model data").hexdigest()

        import logging
        with caplog.at_level(logging.INFO, logger="integrity"):
            _log_directory_hashes(tmp_path)

        assert expected_hash in caplog.text
        assert "model.bin" in caplog.text

    def test_skips_subdirectories(self, tmp_path):
        """Only hashes files, not subdirectories."""
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()
        file_a = tmp_path / "file.bin"
        file_a.write_bytes(b"data")

        # Should not raise (subdirectories are skipped via is_file check)
        _log_directory_hashes(tmp_path)

    def test_empty_directory(self, tmp_path):
        """Does not raise or log errors for empty directory."""
        empty = tmp_path / "empty"
        empty.mkdir()

        # Should not raise
        _log_directory_hashes(empty)

"""Tests for clipboard backup/restore functionality.

Validates:
- US-0.2.5: Clipboard preservation
- clipboard_backup() reads current clipboard text
- clipboard_restore() writes backed-up text
- Graceful handling when clipboard is empty or unavailable
- REQ-S14: Never log clipboard contents
"""

import logging
import pytest
from unittest.mock import patch, MagicMock

from paste import clipboard_backup, clipboard_restore, CF_UNICODETEXT


class TestClipboardBackup:
    """Test clipboard_backup function."""

    @patch("paste._open_clipboard")
    @patch("paste._close_clipboard")
    def test_returns_none_when_clipboard_cannot_open(
        self, mock_close, mock_open
    ):
        """Should return None when clipboard cannot be opened."""
        mock_open.return_value = False
        result = clipboard_backup()
        assert result is None

    @patch("paste._close_clipboard")
    @patch("paste._open_clipboard")
    @patch("paste.ctypes")
    def test_returns_none_when_no_text_format(
        self, mock_ctypes, mock_open, mock_close
    ):
        """Should return None when clipboard has no CF_UNICODETEXT."""
        mock_open.return_value = True
        mock_ctypes.windll.user32.IsClipboardFormatAvailable.return_value = False
        result = clipboard_backup()
        assert result is None

    @patch("paste._close_clipboard")
    @patch("paste._open_clipboard")
    @patch("paste.ctypes")
    def test_returns_none_when_get_data_fails(
        self, mock_ctypes, mock_open, mock_close
    ):
        """Should return None when GetClipboardData returns null."""
        mock_open.return_value = True
        mock_ctypes.windll.user32.IsClipboardFormatAvailable.return_value = True
        mock_ctypes.windll.user32.GetClipboardData.return_value = 0
        result = clipboard_backup()
        assert result is None


class TestClipboardRestore:
    """Test clipboard_restore function."""

    @patch("paste._close_clipboard")
    @patch("paste._open_clipboard")
    def test_none_backup_does_nothing(self, mock_open, mock_close):
        """Restoring None should not attempt clipboard operations."""
        clipboard_restore(None)
        mock_open.assert_not_called()

    @patch("paste._close_clipboard")
    @patch("paste._open_clipboard")
    def test_restore_fails_gracefully_when_clipboard_locked(
        self, mock_open, mock_close
    ):
        """Should not crash when clipboard cannot be opened for restore."""
        mock_open.return_value = False
        # Should not raise
        clipboard_restore("backed up text")


class TestClipboardContentNotInLogs:
    """REQ-S14: Clipboard contents must never appear in logs."""

    @patch("paste._close_clipboard")
    @patch("paste._open_clipboard")
    @patch("paste.ctypes")
    def test_backup_does_not_log_content(
        self, mock_ctypes, mock_open, mock_close, caplog
    ):
        """clipboard_backup should not log the actual clipboard text."""
        mock_open.return_value = True
        mock_ctypes.windll.user32.IsClipboardFormatAvailable.return_value = True
        mock_ctypes.windll.user32.GetClipboardData.return_value = 12345
        mock_ctypes.windll.kernel32.GlobalLock.return_value = 67890
        mock_ctypes.wstring_at.return_value = "Secret clipboard content XYZ123"

        with caplog.at_level(logging.DEBUG):
            result = clipboard_backup()

        log_output = caplog.text
        assert "Secret clipboard content" not in log_output
        assert "XYZ123" not in log_output

    @patch("paste._close_clipboard")
    @patch("paste._open_clipboard")
    @patch("paste.ctypes")
    def test_restore_does_not_log_content(
        self, mock_ctypes, mock_open, mock_close, caplog
    ):
        """clipboard_restore should not log the restored text."""
        mock_open.return_value = True
        mock_ctypes.windll.user32.EmptyClipboard.return_value = True
        mock_ctypes.windll.kernel32.GlobalAlloc.return_value = 12345
        mock_ctypes.windll.kernel32.GlobalLock.return_value = 67890
        mock_ctypes.windll.user32.SetClipboardData.return_value = 1

        with caplog.at_level(logging.DEBUG):
            clipboard_restore("Top secret restore content ABC789")

        log_output = caplog.text
        assert "Top secret restore content" not in log_output
        assert "ABC789" not in log_output


class TestClipboardBackupRestoreEdgeCases:
    """Test edge cases for clipboard backup/restore."""

    @patch("paste._close_clipboard")
    @patch("paste._open_clipboard")
    def test_restore_empty_string(self, mock_open, mock_close):
        """Restoring an empty string should not crash."""
        mock_open.return_value = True
        # Should not raise
        with patch("paste.ctypes") as mock_ctypes:
            mock_ctypes.windll.user32.EmptyClipboard.return_value = True
            mock_ctypes.windll.kernel32.GlobalAlloc.return_value = 12345
            mock_ctypes.windll.kernel32.GlobalLock.return_value = 67890
            mock_ctypes.windll.user32.SetClipboardData.return_value = 1
            clipboard_restore("")

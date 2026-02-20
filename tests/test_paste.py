"""Tests for the clipboard and paste module.

Validates:
- US-0.1.4: Paste transcript at cursor
- REQ-S18: Plain text paste only (CF_UNICODETEXT)
- REQ-S14: Never log clipboard contents

Windows-only: paste.py uses ctypes.windll (Win32 clipboard API).
"""

import sys
import pytest
from unittest.mock import patch, MagicMock, call

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="paste.py requires Windows (ctypes.windll)",
)

from paste import paste_text, CF_UNICODETEXT


class TestPasteText:
    """Test the paste_text function."""

    @patch("paste.kb")
    @patch("paste.ctypes")
    @patch("paste.time")
    def test_successful_paste(self, mock_time, mock_ctypes, mock_kb):
        """US-0.1.4: Text is placed on clipboard and Ctrl+V simulated."""
        # Mock clipboard operations
        mock_ctypes.windll.user32.OpenClipboard.return_value = True
        mock_ctypes.windll.user32.EmptyClipboard.return_value = True
        mock_ctypes.windll.kernel32.GlobalAlloc.return_value = 12345
        mock_ctypes.windll.kernel32.GlobalLock.return_value = 67890
        mock_ctypes.windll.user32.SetClipboardData.return_value = True

        result = paste_text("Hallo Welt")

        assert result is True
        mock_kb.send.assert_called_with("ctrl+v")

    @patch("paste.kb")
    @patch("paste.ctypes")
    @patch("paste.time")
    def test_empty_text_not_pasted(self, mock_time, mock_ctypes, mock_kb):
        """US-0.1.4: Empty transcript is not pasted."""
        result = paste_text("")
        assert result is False
        mock_kb.send.assert_not_called()

    @patch("paste.kb")
    @patch("paste.ctypes")
    @patch("paste.time")
    def test_whitespace_only_not_pasted(self, mock_time, mock_ctypes, mock_kb):
        """US-0.1.4: Whitespace-only text is not pasted."""
        result = paste_text("   \n\t  ")
        assert result is False
        mock_kb.send.assert_not_called()

    @patch("paste.kb")
    @patch("paste._open_clipboard", return_value=False)
    @patch("paste.time")
    def test_clipboard_open_failure(self, mock_time, mock_open_clip, mock_kb):
        """Clipboard open failure returns False."""
        result = paste_text("Test text")

        assert result is False
        mock_kb.send.assert_not_called()

    def test_cf_unicodetext_value(self):
        """REQ-S18: CF_UNICODETEXT constant is correct."""
        assert CF_UNICODETEXT == 13


class TestPlainTextOnly:
    """REQ-S18: Verify only plain text clipboard format is used."""

    def test_no_rich_text_formats(self):
        """paste module should not reference rich text formats."""
        import paste as paste_module
        source = open(paste_module.__file__).read()
        # Should not use CF_HTML, CF_RTF, or other rich formats
        assert "CF_HTML" not in source
        assert "CF_RTF" not in source
        assert "RegisterClipboardFormat" not in source


class TestClipboardContentNotLogged:
    """REQ-S14: Verify clipboard contents are never logged."""

    def test_paste_text_does_not_log_content(self):
        """paste_text should not log the actual text being pasted."""
        import paste as paste_module
        source = open(paste_module.__file__).read()

        # The module logs text length but should not log text content
        # Look for f-string or format patterns that would log the actual text
        assert "logger.info(text" not in source
        assert "logger.debug(text" not in source
        assert 'logger.info("Text:' not in source
        assert "logger.info(summary" not in source

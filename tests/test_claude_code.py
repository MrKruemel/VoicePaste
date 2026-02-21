"""Tests for the Claude Code CLI integration module."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from claude_code import (
    ClaudeCodeBackend,
    ClaudeCodeError,
    ClaudeCodeNotFoundError,
    ClaudeCodeResult,
    ClaudeCodeTimeoutError,
    _parse_claude_output,
)


# ---------------------------------------------------------------------------
# _parse_claude_output tests
# ---------------------------------------------------------------------------

class TestParseClaudeOutput:
    """Tests for JSON output parsing."""

    def test_flat_dict_with_result(self):
        stdout = json.dumps({"result": "Hello world"})
        assert _parse_claude_output(stdout) == "Hello world"

    def test_flat_dict_with_text(self):
        stdout = json.dumps({"text": "Some text"})
        assert _parse_claude_output(stdout) == "Some text"

    def test_flat_dict_with_content(self):
        stdout = json.dumps({"content": "Content here"})
        assert _parse_claude_output(stdout) == "Content here"

    def test_array_with_result_item(self):
        data = [
            {"type": "system", "text": "setup"},
            {"type": "result", "result": "The answer is 42"},
        ]
        assert _parse_claude_output(json.dumps(data)) == "The answer is 42"

    def test_array_with_text_items(self):
        data = [
            {"type": "text", "text": "Line 1"},
            {"type": "text", "text": "Line 2"},
        ]
        assert _parse_claude_output(json.dumps(data)) == "Line 1\nLine 2"

    def test_plain_text_fallback(self):
        """Non-JSON output is returned as-is."""
        assert _parse_claude_output("Just plain text") == "Just plain text"

    def test_empty_output_raises(self):
        with pytest.raises(ClaudeCodeError, match="empty output"):
            _parse_claude_output("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ClaudeCodeError, match="empty output"):
            _parse_claude_output("   \n  ")

    def test_unknown_dict_shape(self):
        """Unknown dict keys still return something useful."""
        data = {"foo": "bar", "baz": 42}
        result = _parse_claude_output(json.dumps(data))
        assert "foo" in result
        assert "bar" in result

    def test_array_result_item_with_text_key(self):
        """Result item using 'text' key instead of 'result'."""
        data = [{"type": "result", "text": "answer via text key"}]
        assert _parse_claude_output(json.dumps(data)) == "answer via text key"


# ---------------------------------------------------------------------------
# ClaudeCodeBackend.is_available tests
# ---------------------------------------------------------------------------

class TestIsAvailable:
    """Tests for CLI availability check."""

    @patch("claude_code.shutil.which")
    def test_available_when_claude_in_path(self, mock_which):
        mock_which.return_value = "/usr/local/bin/claude"
        assert ClaudeCodeBackend.is_available() is True
        mock_which.assert_called_once_with("claude")

    @patch("claude_code.shutil.which")
    def test_not_available_when_missing(self, mock_which):
        mock_which.return_value = None
        assert ClaudeCodeBackend.is_available() is False


# ---------------------------------------------------------------------------
# ClaudeCodeBackend.get_version tests
# ---------------------------------------------------------------------------

class TestGetVersion:
    """Tests for version retrieval."""

    @patch("claude_code.subprocess.run")
    def test_returns_version_string(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1.0.17\n",
        )
        assert ClaudeCodeBackend.get_version() == "1.0.17"

    @patch("claude_code.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert ClaudeCodeBackend.get_version() is None

    @patch("claude_code.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_none_when_not_found(self, mock_run):
        assert ClaudeCodeBackend.get_version() is None

    @patch("claude_code.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 10))
    def test_returns_none_on_timeout(self, mock_run):
        assert ClaudeCodeBackend.get_version() is None


# ---------------------------------------------------------------------------
# ClaudeCodeBackend.invoke tests
# ---------------------------------------------------------------------------

class TestInvoke:
    """Tests for the main invoke method."""

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_successful_invocation(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "Claude says hello"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(working_directory="/tmp/project")
        result = backend.invoke("What is 2+2?")

        assert isinstance(result, ClaudeCodeResult)
        assert result.text == "Claude says hello"
        assert result.duration_seconds >= 0

        # Verify subprocess called correctly
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "What is 2+2?" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        assert call_args[1]["cwd"] == "/tmp/project"

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_with_system_prompt(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(system_prompt="Be concise.")
        backend.invoke("test")

        cmd = mock_run.call_args[0][0]
        assert "--append-system-prompt" in cmd
        assert "Be concise." in cmd

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_with_skip_permissions(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(skip_permissions=True)
        backend.invoke("test")

        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_without_skip_permissions(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(skip_permissions=False)
        backend.invoke("test")

        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_without_system_prompt(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend()
        backend.invoke("test")

        cmd = mock_run.call_args[0][0]
        assert "--append-system-prompt" not in cmd

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=False)
    def test_raises_not_found_when_unavailable(self, mock_avail):
        backend = ClaudeCodeBackend()
        with pytest.raises(ClaudeCodeNotFoundError):
            backend.invoke("test")

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run", side_effect=FileNotFoundError)
    def test_raises_not_found_on_file_not_found(self, mock_run, mock_avail):
        backend = ClaudeCodeBackend()
        with pytest.raises(ClaudeCodeNotFoundError):
            backend.invoke("test")

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run",
           side_effect=subprocess.TimeoutExpired("claude", 120))
    def test_raises_timeout(self, mock_run, mock_avail):
        backend = ClaudeCodeBackend(timeout_seconds=120)
        with pytest.raises(ClaudeCodeTimeoutError, match="timed out"):
            backend.invoke("complex question")

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_raises_on_non_zero_exit(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: authentication failed",
        )
        backend = ClaudeCodeBackend()
        with pytest.raises(ClaudeCodeError, match="authentication failed"):
            backend.invoke("test")

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_default_cwd_is_none(self, mock_run, mock_avail):
        """When no working_directory set, cwd=None (inherits process cwd)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend()
        backend.invoke("test")

        assert mock_run.call_args[1]["cwd"] is None

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_plain_text_response(self, mock_run, mock_avail):
        """Non-JSON stdout is treated as plain text."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Just a plain text response\nwith newlines",
            stderr="",
        )
        backend = ClaudeCodeBackend()
        result = backend.invoke("test")
        assert result.text == "Just a plain text response\nwith newlines"

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_stdin_is_devnull(self, mock_run, mock_avail):
        """Verify stdin=subprocess.DEVNULL to prevent interactive prompts."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend()
        backend.invoke("test")

        assert mock_run.call_args[1]["stdin"] == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# Session / conversation management tests
# ---------------------------------------------------------------------------

class TestSessionManagement:
    """Tests for --continue flag and conversation tracking."""

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_first_call_no_continue_flag(self, mock_run, mock_avail):
        """First invocation should not include --continue."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(continue_conversation=True)
        backend.invoke("hello")

        cmd = mock_run.call_args[0][0]
        assert "--continue" not in cmd

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_second_call_has_continue_flag(self, mock_run, mock_avail):
        """Second invocation should include --continue when enabled."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(continue_conversation=True)
        backend.invoke("first")
        backend.invoke("second")

        cmd = mock_run.call_args[0][0]
        assert "--continue" in cmd

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_continue_disabled_no_flag(self, mock_run, mock_avail):
        """When continue_conversation=False, --continue is never used."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(continue_conversation=False)
        backend.invoke("first")
        backend.invoke("second")

        # Check both calls: neither should have --continue
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert "--continue" not in cmd

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_new_conversation_resets_session(self, mock_run, mock_avail):
        """new_conversation() resets state so next call is fresh."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )
        backend = ClaudeCodeBackend(continue_conversation=True)
        backend.invoke("first")
        assert backend._session_active is True

        backend.new_conversation()
        assert backend._session_active is False

        backend.invoke("after reset")
        cmd = mock_run.call_args[0][0]
        assert "--continue" not in cmd

    def test_new_conversation_on_fresh_backend(self):
        """new_conversation() on unused backend is a no-op."""
        backend = ClaudeCodeBackend(continue_conversation=True)
        assert backend._session_active is False
        backend.new_conversation()  # should not raise
        assert backend._session_active is False

    @patch("claude_code.ClaudeCodeBackend.is_available", return_value=True)
    @patch("claude_code.subprocess.run")
    def test_session_not_set_on_error(self, mock_run, mock_avail):
        """Failed invocation should not set _session_active."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="some error",
        )
        backend = ClaudeCodeBackend(continue_conversation=True)
        with pytest.raises(ClaudeCodeError):
            backend.invoke("fail")

        assert backend._session_active is False

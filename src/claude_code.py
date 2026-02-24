"""Claude Code CLI integration for VoicePaste.

Subprocess wrapper that invokes `claude -p` to send voice transcripts
to Claude Code and receive responses with full project context.

v1.2: Initial implementation (Phase 1).
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def _find_claude_binary() -> Optional[str]:
    """Locate the ``claude`` CLI binary.

    Search order:
    1. ``shutil.which("claude")`` — works when npm bin dir is on PATH.
    2. Windows fallback: ``%APPDATA%\\npm\\claude.cmd`` — npm global installs
       put a ``.cmd`` wrapper here, but the directory is often missing from
       the system PATH (PowerShell finds ``claude.ps1`` instead, which
       ``subprocess.run`` cannot execute).
    3. Linux/macOS fallback: common npm global bin directories.

    Returns:
        Absolute path to the claude binary, or None if not found.
    """
    # 1. Standard PATH search
    found = shutil.which("claude")
    if found:
        return found

    # 2. Windows: check npm global directory
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            cmd_path = os.path.join(appdata, "npm", "claude.cmd")
            if os.path.isfile(cmd_path):
                logger.debug("Found claude.cmd at npm fallback: %s", cmd_path)
                return cmd_path

    # 3. Linux/macOS: common npm global bin locations
    else:
        for candidate in (
            os.path.expanduser("~/.npm-global/bin/claude"),
            "/usr/local/bin/claude",
            os.path.expanduser("~/.local/bin/claude"),
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                logger.debug("Found claude at fallback: %s", candidate)
                return candidate

    return None


class ClaudeCodeError(Exception):
    """Base exception for Claude Code CLI errors."""


class ClaudeCodeNotFoundError(ClaudeCodeError):
    """Raised when the `claude` CLI binary is not found in PATH."""


class ClaudeCodeTimeoutError(ClaudeCodeError):
    """Raised when the Claude Code subprocess exceeds the timeout."""


@dataclass
class ClaudeCodeResult:
    """Result from a Claude Code CLI invocation.

    Attributes:
        text: The response text from Claude Code.
        session_id: Session ID for future --resume support (Phase 2).
        duration_seconds: Wall-clock time of the invocation.
    """
    text: str
    session_id: Optional[str] = None
    duration_seconds: float = 0.0


class ClaudeCodeBackend:
    """Subprocess wrapper for the Claude Code CLI.

    Invokes `claude -p "<prompt>"` with optional working directory
    and system prompt, parses the JSON output, and returns the result.
    """

    def __init__(
        self,
        working_directory: Optional[str] = None,
        system_prompt: Optional[str] = None,
        timeout_seconds: int = 120,
        skip_permissions: bool = False,
        continue_conversation: bool = False,
    ) -> None:
        """Initialize the Claude Code backend.

        Args:
            working_directory: Directory to run `claude` in (cwd).
                If None, uses VoicePaste's current working directory.
            system_prompt: Optional system prompt appended via
                --append-system-prompt.
            timeout_seconds: Maximum time to wait for the subprocess.
            skip_permissions: If True, pass --dangerously-skip-permissions
                to bypass the allowlist. Only use on trusted systems.
            continue_conversation: If True, pass --continue after the
                first invocation to maintain conversation context.
        """
        self.working_directory = working_directory
        self.system_prompt = system_prompt
        self.timeout_seconds = timeout_seconds
        self.skip_permissions = skip_permissions
        self.continue_conversation = continue_conversation
        self._session_active = False

    def invoke(self, prompt: str) -> ClaudeCodeResult:
        """Send a prompt to Claude Code CLI and return the response.

        Args:
            prompt: The user's transcribed speech to send to Claude.

        Returns:
            ClaudeCodeResult with the response text.

        Raises:
            ClaudeCodeNotFoundError: If `claude` is not in PATH.
            ClaudeCodeTimeoutError: If the subprocess exceeds timeout.
            ClaudeCodeError: On non-zero exit or JSON parse failure.
        """
        claude_bin = _find_claude_binary()
        if not claude_bin:
            raise ClaudeCodeNotFoundError(
                "Claude Code CLI not found in PATH. "
                "Install: npm install -g @anthropic-ai/claude-code"
            )

        cmd = [claude_bin, "-p", prompt, "--output-format", "json"]

        if self.continue_conversation and self._session_active:
            cmd.append("--continue")

        if self.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if self.system_prompt:
            cmd.extend(["--append-system-prompt", self.system_prompt])

        cwd = self.working_directory or None

        logger.info(
            "Invoking Claude Code CLI (cwd=%s, timeout=%ds, prompt_len=%d)",
            cwd or "(default)",
            self.timeout_seconds,
            len(prompt),
        )

        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=cwd,
            )
        except FileNotFoundError:
            raise ClaudeCodeNotFoundError(
                "Claude Code CLI not found. "
                "Install: npm install -g @anthropic-ai/claude-code"
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            raise ClaudeCodeTimeoutError(
                f"Claude Code timed out after {duration:.1f}s "
                f"(limit: {self.timeout_seconds}s)"
            )

        duration = time.monotonic() - start

        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else ""
            logger.error(
                "Claude Code exited with code %d: %s",
                result.returncode,
                stderr[:500],
            )
            raise ClaudeCodeError(
                f"Claude Code exited with code {result.returncode}: "
                f"{stderr[:200]}"
            )

        # Parse JSON output
        response_text = _parse_claude_output(result.stdout)

        logger.info(
            "Claude Code responded in %.1fs (%d chars)",
            duration,
            len(response_text),
        )

        # Mark session as active for --continue on subsequent calls
        self._session_active = True

        return ClaudeCodeResult(
            text=response_text,
            duration_seconds=duration,
        )

    def new_conversation(self) -> None:
        """Reset session state so the next invocation starts a fresh conversation."""
        was_active = self._session_active
        self._session_active = False
        if was_active:
            logger.info("Claude Code conversation reset.")

    @staticmethod
    def is_available() -> bool:
        """Check if the ``claude`` CLI is available."""
        return _find_claude_binary() is not None

    @staticmethod
    def get_version() -> Optional[str]:
        """Get the Claude Code CLI version string, or None if unavailable."""
        claude_bin = _find_claude_binary()
        if not claude_bin:
            return None
        try:
            result = subprocess.run(
                [claude_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None


def _parse_claude_output(stdout: str) -> str:
    """Parse Claude Code CLI JSON output into plain text.

    Handles multiple output formats:
    - Flat object: {"result": "..."}
    - Array format: [{"type": "result", "result": "..."}]
    - Plain text fallback if JSON parsing fails

    Args:
        stdout: Raw stdout from the claude subprocess.

    Returns:
        Extracted response text.

    Raises:
        ClaudeCodeError: If output is empty or unparseable.
    """
    stdout = stdout.strip()
    if not stdout:
        raise ClaudeCodeError("Claude Code returned empty output.")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Not JSON — treat as plain text response
        logger.debug("Claude output is not JSON, using as plain text.")
        return stdout

    # Flat object format: {"result": "..."}
    if isinstance(data, dict):
        if "result" in data:
            return str(data["result"])
        if "text" in data:
            return str(data["text"])
        if "content" in data:
            return str(data["content"])
        # Unknown dict shape — try to extract something useful
        logger.warning("Unknown Claude JSON dict shape: %s", list(data.keys()))
        return json.dumps(data, ensure_ascii=False)

    # Array format: [{"type": "result", ...}]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                if item.get("type") == "result":
                    return str(item.get("result", item.get("text", "")))
        # No result item found — concatenate text items
        texts = []
        for item in data:
            if isinstance(item, dict):
                text = item.get("result") or item.get("text") or item.get("content")
                if text:
                    texts.append(str(text))
        if texts:
            return "\n".join(texts)
        logger.warning("No text found in Claude JSON array output.")
        return stdout

    # Unexpected type
    return str(data)

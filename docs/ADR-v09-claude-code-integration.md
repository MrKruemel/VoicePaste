# Architecture Decision Record: Claude Code CLI Integration

**Date**: 2026-02-19
**Status**: Proposed
**Author**: Solution Architect
**Base Version**: 0.8.0 (current)
**Relevant to**: v0.9.x or v0.10.0

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Context and Problem Statement](#2-context-and-problem-statement)
3. [CLI Integration Pattern](#3-cli-integration-pattern)
4. [Structured Output Design](#4-structured-output-design)
5. [Security Architecture](#5-security-architecture)
6. [Pipeline Integration](#6-pipeline-integration)
7. [Session Management](#7-session-management)
8. [Settings UI Design](#8-settings-ui-design)
9. [Configuration Schema](#9-configuration-schema)
10. [Component Diagram](#10-component-diagram)
11. [Implementation Phases](#11-implementation-phases)
12. [Risk Assessment](#12-risk-assessment)
13. [Rejected Alternatives](#13-rejected-alternatives)
14. [Open Questions](#14-open-questions)

---

## 1. Executive Summary

This ADR describes how to integrate the **Claude Code CLI** (`claude`) as a
backend for the Voice Paste tool. Claude Code is Anthropic's official CLI for
Claude. It supports non-interactive mode (`claude -p "prompt"`), structured JSON
output (`--output-format json`), schema enforcement (`--json-schema`), tool
restrictions (`--allowedTools`, `--disallowedTools`), model selection (`--model`),
budget limits (`--max-budget-usd`), and session continuity (`-c`, `-r`).

The integration adds Claude Code as a **new summarization / prompt backend**
alongside the existing OpenAI, OpenRouter, and Ollama options. It does NOT
replace any existing backend -- it is an additional provider choice.

**Key architectural decision**: Claude Code CLI is invoked via `subprocess.run()`
in non-interactive print mode (`-p`). This is the simplest, most robust pattern.
No SDK, no library, no persistent process. Just a subprocess call with structured
JSON output.

**Security stance**: Voice Paste will invoke Claude Code with **tool restrictions
by default**. The default configuration disables file editing and code execution
tools (`--allowedTools "Read Grep Glob WebSearch WebFetch"`). Users who understand
the implications can enable additional tools via Settings. The
`--dangerously-skip-permissions` flag is **never** used.

---

## 2. Context and Problem Statement

### Why Add Claude Code CLI?

Voice Paste currently supports three summarization/prompt providers:

| Provider | Transport | Key Advantage |
|----------|-----------|---------------|
| OpenAI | HTTP API (openai SDK) | Quality, speed |
| OpenRouter | HTTP API (openai SDK) | Model variety |
| Ollama | HTTP API (openai SDK) | Local, free |

All three use the same transport mechanism (OpenAI-compatible HTTP API via the
`openai` Python SDK). Adding Claude Code CLI introduces a fundamentally different
transport: **subprocess invocation** of a locally installed CLI tool.

### Why Not Use the Anthropic Python SDK?

The Anthropic Python SDK (`anthropic`) would provide direct API access to Claude
models. However:

1. **New dependency**: The `anthropic` SDK is a new package (~5 MB) that must be
   bundled. The `openai` SDK is already bundled. Adding another SDK increases
   binary size and maintenance burden.

2. **Requires separate API key management**: The Anthropic API requires a
   separate API key (`ANTHROPIC_API_KEY`), adding another credential to manage.

3. **No tool use without infrastructure**: The SDK provides tool use, but you
   must implement tool execution yourself. Claude Code CLI already has a complete
   tool execution environment with sandboxing, permission management, and
   error handling.

4. **The CLI is already installed**: Users who want Claude integration likely
   already have `claude` CLI installed. Zero additional dependencies.

5. **Session management for free**: The CLI handles conversation persistence,
   session IDs, and context continuity. We get multi-turn conversations without
   any state management code.

### When Would the Anthropic SDK Be Better?

The SDK would be preferred if:
- We needed streaming responses (progressive paste)
- We needed sub-200ms latency (subprocess startup adds ~500ms)
- We needed Claude models without CLI installed

These are future considerations, not current requirements. **The CLI-first
approach is the simplest working solution.**

### What Can Claude Code Do That Other Backends Cannot?

Claude Code CLI has **agentic capabilities**: it can read files, search
codebases, run commands, and browse the web. This transforms Voice Paste from a
"transcribe and summarize" tool into a "transcribe and act" tool.

Example voice commands that become possible:

- "What are the open issues in the current git repo?"
- "Summarize the contents of README.md"
- "Search this codebase for all functions that handle authentication"
- "What is the weather in Berlin today?" (via WebSearch)

This is a qualitative leap beyond what OpenAI/OpenRouter/Ollama provide through
a chat completion API.

---

## 3. CLI Integration Pattern

### 3.1 Invocation Strategy

**Decision**: Use `subprocess.run()` with `capture_output=True` in a worker thread.

```python
import subprocess
import json
import shutil
from typing import Optional

def invoke_claude(
    prompt: str,
    system_prompt: str = "",
    model: str = "sonnet",
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    max_turns: int = 3,
    max_budget_usd: float = 0.05,
    timeout_seconds: int = 60,
    json_schema: dict | None = None,
    session_resume: str | None = None,
    continue_session: bool = False,
    working_directory: str | None = None,
) -> dict:
    """Invoke Claude Code CLI in non-interactive print mode.

    Args:
        prompt: The user prompt (transcribed voice input).
        system_prompt: System prompt prepended to the conversation.
        model: Model alias ("haiku", "sonnet", "opus") or full name.
        allowed_tools: Whitelist of tools Claude may use.
        disallowed_tools: Blacklist of tools Claude must not use.
        max_turns: Maximum agentic turns (limits tool use loops).
        max_budget_usd: Maximum spend per invocation.
        timeout_seconds: Subprocess timeout.
        json_schema: JSON Schema for structured output enforcement.
        session_resume: Session ID or name to resume.
        continue_session: Continue the most recent conversation.
        working_directory: Working directory for Claude (sandboxing).

    Returns:
        Parsed JSON response dict with keys:
        - "result": The text output from Claude.
        - "cost_usd": Total cost of the invocation.
        - "session_id": Session ID for continuation.
        - "is_error": Whether an error occurred.
        - "duration_ms": Wall-clock time of the invocation.

    Raises:
        ClaudeNotInstalledError: If `claude` CLI is not found on PATH.
        ClaudeTimeoutError: If the subprocess exceeds timeout.
        ClaudeInvocationError: If the subprocess returns non-zero exit code.
    """
```

### 3.2 CLI Detection

Before invoking, we must verify `claude` is available:

```python
def is_claude_available() -> bool:
    """Check if Claude Code CLI is installed and accessible."""
    return shutil.which("claude") is not None

def get_claude_version() -> Optional[str]:
    """Return the Claude CLI version string, or None if not installed."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
```

This check runs:
- At application startup (logged, no error if missing)
- When the user selects "Claude Code" as the summarization provider in Settings
- Before each invocation (fast path, cached for 60 seconds)

### 3.3 Command Construction

The full command is built from configuration:

```python
def _build_command(
    prompt: str,
    system_prompt: str,
    model: str,
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
    max_turns: int,
    max_budget_usd: float,
    json_schema: dict | None,
    session_resume: str | None,
    continue_session: bool,
) -> list[str]:
    """Build the claude CLI argument list."""
    cmd = ["claude", "--print"]

    # Output format: always JSON for structured parsing
    cmd.extend(["--output-format", "json"])

    # Model selection
    if model:
        cmd.extend(["--model", model])

    # System prompt
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    # Tool restrictions
    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])
    if disallowed_tools:
        cmd.extend(["--disallowedTools", ",".join(disallowed_tools)])

    # Budget and turn limits
    cmd.extend(["--max-budget-usd", str(max_budget_usd)])

    # JSON Schema enforcement (optional)
    if json_schema:
        cmd.extend(["--json-schema", json.dumps(json_schema)])

    # Session management
    if continue_session:
        cmd.append("--continue")
    elif session_resume:
        cmd.extend(["--resume", session_resume])

    # The prompt itself (positional argument)
    cmd.append(prompt)

    return cmd
```

### 3.4 Output Parsing

With `--output-format json`, Claude CLI returns a JSON object. The shape depends
on the CLI version but generally contains:

```json
{
  "type": "result",
  "subtype": "success",
  "cost_usd": 0.003,
  "is_error": false,
  "duration_ms": 2345,
  "duration_api_ms": 1890,
  "num_turns": 1,
  "result": "The summarized text goes here.",
  "session_id": "abc123-def456"
}
```

Our parsing strategy:

```python
def _parse_response(stdout: str, stderr: str, returncode: int) -> dict:
    """Parse Claude CLI JSON output into a normalized response.

    Handles:
    - Valid JSON output (happy path)
    - Non-JSON output (fallback: treat stdout as plain text)
    - Empty output
    - Non-zero exit codes
    """
    if returncode != 0:
        return {
            "result": "",
            "is_error": True,
            "error_message": stderr.strip() or f"Exit code {returncode}",
            "cost_usd": 0.0,
            "session_id": "",
            "duration_ms": 0,
        }

    # Try JSON parse
    try:
        data = json.loads(stdout)
        return {
            "result": data.get("result", ""),
            "is_error": data.get("is_error", False),
            "error_message": data.get("result", "") if data.get("is_error") else "",
            "cost_usd": data.get("cost_usd", 0.0),
            "session_id": data.get("session_id", ""),
            "duration_ms": data.get("duration_ms", 0),
        }
    except json.JSONDecodeError:
        # Fallback: treat stdout as plain text (older CLI versions or
        # --output-format text accidentally set)
        return {
            "result": stdout.strip(),
            "is_error": False,
            "error_message": "",
            "cost_usd": 0.0,
            "session_id": "",
            "duration_ms": 0,
        }
```

### 3.5 Error Handling

| Error Condition | Detection | Recovery |
|-----------------|-----------|----------|
| `claude` not on PATH | `shutil.which("claude") is None` | Show error in tray notification. Disable Claude provider until Settings change. |
| Subprocess timeout | `subprocess.TimeoutExpired` | Kill process. Show "Claude timed out" notification. Fall through to paste raw transcript. |
| Non-zero exit code | `result.returncode != 0` | Parse stderr for error message. Show notification. Fall through to paste raw transcript. |
| JSON parse failure | `json.JSONDecodeError` | Fall back to plain text extraction from stdout. |
| Claude CLI auth error | stderr contains "auth" or "API key" | Show notification: "Claude Code not authenticated. Run `claude auth` in terminal." |
| Budget exceeded | stderr contains "budget" | Show notification with cost info. |
| Empty result | `result == ""` after parsing | Paste raw transcript (graceful degradation). |

### 3.6 Why subprocess.run() and Not subprocess.Popen()?

`subprocess.run()` is the right choice because:

1. **Blocking is fine**: The pipeline already runs on a worker thread (Thread 2).
   Blocking the worker thread while waiting for Claude is the intended behavior.

2. **Simpler error handling**: `run()` returns a `CompletedProcess` with
   `returncode`, `stdout`, `stderr` all available. No need to manage pipes.

3. **Timeout support**: `run(timeout=N)` raises `TimeoutExpired` cleanly.

4. **No streaming needed (yet)**: The current pipeline waits for the full result
   before pasting. If we add streaming paste in the future, we would switch to
   `Popen` with `--output-format stream-json`.

`subprocess.Popen()` would be needed for:
- Real-time streaming output to a progress indicator
- Cancellation (kill the process on Escape during PROCESSING)

**Future enhancement**: Use `Popen` for cancellation support. When the user
presses Escape during PROCESSING, we can send `SIGTERM` / `TerminateProcess()`
to the Claude subprocess. With `run()`, the only cancellation is timeout.

---

## 4. Structured Output Design

### 4.1 JSON Schema for Voice Commands

When Claude Code is used as a prompt backend (voice prompt mode), we want
structured output so we can reliably extract the answer and optional metadata.

**Response Schema:**

```json
{
  "type": "object",
  "properties": {
    "answer": {
      "type": "string",
      "description": "The response text to paste or speak."
    },
    "action": {
      "type": "string",
      "enum": ["paste", "tts", "paste_and_tts", "none"],
      "description": "What to do with the answer."
    },
    "language": {
      "type": "string",
      "description": "ISO language code of the response (e.g., 'de', 'en')."
    }
  },
  "required": ["answer"]
}
```

**When to use the schema:**

- **Summarization mode**: Do NOT use schema. Claude's raw text output is the
  summarized text. No need for JSON wrapping.
- **Voice prompt mode**: Use schema. The `answer` field is pasted. The `action`
  field can influence whether the result is pasted, spoken, or both.
- **TTS ask mode**: Use schema. The `answer` field is spoken via TTS.

### 4.2 Fallback Strategy

If `--json-schema` enforcement fails (older CLI version, model hallucination):

1. Try parsing stdout as JSON.
2. If JSON parsing fails, treat entire stdout as the answer text.
3. Default `action` to the mode-appropriate default ("paste" for prompt mode,
   "tts" for tts_ask mode).

This ensures the user always gets a result, even if schema enforcement breaks.

### 4.3 When NOT to Use Structured Output

For **summarization mode** (the default `Ctrl+Alt+R` hotkey), Claude receives
the raw transcript and a system prompt asking it to clean up the text. The
output should be plain text, not JSON. We achieve this by:

- NOT passing `--json-schema`
- Using `--output-format json` (to get the metadata envelope)
- Extracting the `result` field, which contains the plain text summary

The JSON envelope from `--output-format json` is always present regardless of
`--json-schema`. The schema only constrains the `result` field's content.

---

## 5. Security Architecture

### 5.1 Threat Model

Claude Code CLI is a powerful tool that can:
- Read files on the filesystem
- Write and edit files
- Execute shell commands
- Search the web
- Make HTTP requests

When Voice Paste invokes Claude Code with **voice-transcribed input**, the
transcribed text becomes the user prompt. This creates a prompt injection
surface: if the user's speech is transcribed as something that instructs Claude
to perform harmful actions, those actions could be executed.

**However**: The risk is substantially lower than with arbitrary text input
because:
- The input is the user's own speech (they are the threat actor)
- The user explicitly triggered the recording via hotkey
- Background noise transcription artifacts are unlikely to form coherent
  prompt injection payloads
- Claude Code's built-in safety mitigations apply

### 5.2 Defense in Depth

**Layer 1: Tool Restrictions (primary defense)**

Default `--allowedTools` whitelist:

```
Read Grep Glob WebSearch WebFetch
```

This allows Claude to:
- Read files (Read, Grep, Glob) -- useful for "what does this file say?" queries
- Search the web (WebSearch, WebFetch) -- useful for factual questions

This denies Claude:
- Bash (no shell command execution)
- Edit (no file modification)
- Write (no file creation)
- NotebookEdit (no notebook modification)

The user can expand this whitelist in Settings if they understand the
implications. The Settings UI groups tools into safety tiers:

| Tier | Tools | Risk | Default |
|------|-------|------|---------|
| **Read-only** | Read, Grep, Glob | Low | Enabled |
| **Web access** | WebSearch, WebFetch | Low | Enabled |
| **File modification** | Edit, Write, NotebookEdit | Medium | Disabled |
| **Shell execution** | Bash | High | Disabled |
| **All tools** | (everything) | High | Disabled |

**Layer 2: Permission Mode**

Claude Code CLI has built-in permission management. By default (without
`--dangerously-skip-permissions`), it prompts for confirmation before:
- Running Bash commands
- Editing files
- Writing new files

In non-interactive `--print` mode, the permission behavior depends on the
`--permission-mode` flag:

| Mode | Behavior | Our Usage |
|------|----------|-----------|
| `default` | Prompts for permissions (hangs in -p mode) | NOT suitable |
| `plan` | Plan only, no execution | Too restrictive |
| `acceptEdits` | Auto-accepts edits, prompts for Bash | Reasonable default |
| `dontAsk` | Auto-accepts most, prompts for risky | Good default |
| `bypassPermissions` | Skips all checks | Only with `--allow-dangerously-skip-permissions` |

**Decision**: Use `--permission-mode dontAsk` as the default. This lets Claude
use the tools in its allowed set without hanging on permission prompts.

Combined with the tool whitelist, this is safe: if `Bash` is not in
`--allowedTools`, Claude cannot execute shell commands regardless of permission
mode.

**Layer 3: Budget Limit**

`--max-budget-usd 0.10` by default. Prevents runaway costs from agentic loops.
Configurable in Settings (min: $0.01, max: $5.00).

**Layer 4: Turn Limit**

Claude Code CLI does not expose a `--max-turns` flag directly. However, the
budget limit effectively caps turns because each turn costs money.

**Layer 5: Working Directory Restriction**

The `cwd` parameter of `subprocess.run()` controls where Claude Code operates.
Options:

| Strategy | Value | Effect |
|----------|-------|--------|
| Current directory | `os.getcwd()` | Claude sees whatever the user ran Voice Paste from |
| Home directory | `Path.home()` | Claude sees the user's home dir |
| Temp directory | `tempfile.mkdtemp()` | Claude sees an empty directory (most restrictive) |
| Configured path | User-specified | Claude sees a specific project directory |

**Decision**: Default to the user's home directory. This provides a reasonable
scope for "search my files" type queries without exposing system directories.
The user can override this in Settings to point at a specific project.

**Layer 6: No `--dangerously-skip-permissions`**

Voice Paste **never** passes `--dangerously-skip-permissions`. This flag is
designed for sandboxed environments with no internet access. Voice Paste runs on
the user's desktop with full internet access, so this flag is never appropriate.

### 5.3 Prompt Injection Mitigation

The system prompt for Claude Code invocations includes explicit safety
instructions:

```
You are a helpful voice assistant invoked via the Voice Paste tool.

IMPORTANT SAFETY RULES:
1. The user prompt below is transcribed from speech. It may contain
   transcription errors. Interpret it charitably.
2. NEVER modify, delete, or overwrite files unless the user explicitly
   asks you to AND file modification tools are enabled.
3. NEVER execute destructive commands (rm, del, format, etc.).
4. If the user's request is ambiguous, ask for clarification in your
   response rather than guessing.
5. Respond in the same language as the user's input.
```

This system prompt is **appended** via `--append-system-prompt` (not
`--system-prompt`, which would replace the CLI's built-in system prompt). This
preserves Claude Code's own safety instructions while adding Voice Paste-specific
guidance.

### 5.4 Security Settings Summary

| Setting | Default | Configurable | Location |
|---------|---------|-------------|----------|
| Allowed tools | `Read,Grep,Glob,WebSearch,WebFetch` | Yes (Settings) | config.toml |
| Permission mode | `dontAsk` | No (hardcoded) | code |
| Budget limit | $0.10 | Yes (Settings) | config.toml |
| Working directory | User home | Yes (Settings) | config.toml |
| Skip permissions | Never | No (hardcoded to never) | code |
| System prompt | Safety-aware default | Yes (Settings) | config.toml |

---

## 6. Pipeline Integration

### 6.1 Where Claude Code Fits

Claude Code is a **new summarization/prompt provider**. It integrates at the
same level as OpenAI, OpenRouter, and Ollama in the summarization step.

Current pipeline:
```
Record -> STT -> Summarize/Prompt -> Paste/TTS
                 ^
                 |
                 CloudLLMSummarizer (OpenAI/OpenRouter/Ollama)
```

With Claude Code:
```
Record -> STT -> Summarize/Prompt -> Paste/TTS
                 ^
                 |
                 +-- CloudLLMSummarizer (OpenAI/OpenRouter/Ollama)
                 |
                 +-- ClaudeCodeSummarizer (NEW -- subprocess to CLI)
```

### 6.2 New Summarizer Implementation

```python
class ClaudeCodeSummarizer:
    """Summarizer backend that invokes Claude Code CLI.

    Implements the Summarizer protocol. Uses subprocess to invoke
    `claude -p` with the transcript as input.
    """

    def __init__(
        self,
        model: str = "sonnet",
        system_prompt: str = SUMMARIZE_SYSTEM_PROMPT,
        allowed_tools: list[str] | None = None,
        max_budget_usd: float = 0.10,
        timeout_seconds: int = 60,
        working_directory: str | None = None,
    ) -> None: ...

    def summarize(
        self,
        text: str,
        language: str = "de",
        system_prompt: str | None = None,
    ) -> str:
        """Summarize text using Claude Code CLI.

        Invokes `claude -p` with the text as the user prompt.
        Returns the cleaned/summarized text.

        Falls back to returning the input text unchanged if:
        - Claude CLI is not installed
        - The subprocess times out
        - Any other error occurs
        """
```

### 6.3 Provider Selection

The `summarization_provider` config field gains a new value: `"claude"`.

```python
# In _rebuild_summarizer():
if config.summarization_provider == "claude":
    if not is_claude_available():
        self._summarizer = PassthroughSummarizer()
        logger.warning(
            "Claude Code CLI not found. "
            "Install it with: npm install -g @anthropic-ai/claude-code"
        )
        return

    self._summarizer = ClaudeCodeSummarizer(
        model=config.claude_model,
        system_prompt=config.active_system_prompt,
        allowed_tools=config.claude_allowed_tools,
        max_budget_usd=config.claude_max_budget_usd,
        timeout_seconds=config.claude_timeout_seconds,
        working_directory=config.claude_working_directory,
    )
```

### 6.4 Hotkey Behavior

**No new hotkey needed.** Claude Code integrates as a provider behind the
existing hotkeys:

| Hotkey | Mode | With Claude Code |
|--------|------|-----------------|
| `Ctrl+Alt+R` | Summary | Record -> STT -> Claude summarizes -> Paste |
| `Ctrl+Alt+A` | Voice Prompt | Record -> STT -> Claude answers (agentic) -> Paste |
| `Ctrl+Alt+Y` | Ask + TTS | Record -> STT -> Claude answers -> TTS speaks |

The key difference with Claude Code is the **Voice Prompt** and **Ask + TTS**
modes. Because Claude Code has tool access, a voice prompt like "What are the
open PRs in this repo?" will actually work -- Claude will use its tools to find
the answer, rather than hallucinating.

### 6.5 Graceful Degradation

The fallback chain when Claude Code fails:

```
1. Try Claude Code CLI invocation
   |
   +-- Success: return structured result
   |
   +-- Failure (timeout, error, not installed):
       |
       2. Fall back to PassthroughSummarizer (return raw transcript)
          |
          3. Show tray notification explaining the failure
```

We do NOT fall back to another LLM provider automatically. If the user chose
Claude Code, they want Claude Code. Silent fallback to GPT-4o-mini would be
confusing. Instead, we paste the raw transcript and notify the user.

---

## 7. Session Management

### 7.1 Stateless vs. Stateful

| Approach | Benefit | Cost |
|----------|---------|------|
| **Stateless** (fresh session per invocation) | Simple, no state to manage, predictable behavior | No conversation continuity, cannot ask follow-up questions |
| **Stateful** (continue session) | Multi-turn conversation, context carryover, more natural interaction | Session file management, stale context, cost accumulation |

**Decision**: Default to **stateless**. Offer **opt-in stateful** mode.

**Rationale:**

1. Most Voice Paste use cases are one-shot: "clean up this text", "what is X?",
   "summarize this paragraph". These do not benefit from session continuity.

2. Stateful sessions accumulate context, which means each subsequent turn is
   more expensive (more input tokens). For voice-triggered interactions that
   happen frequently, this cost can add up.

3. Stale context is confusing. If the user asked about file X an hour ago and
   now asks about file Y, the old context about X is irrelevant noise.

4. Claude Code sessions are persisted on disk by the CLI itself. Voice Paste
   does not need to manage session storage.

### 7.2 Stateful Mode (Opt-In)

When enabled in Settings, Voice Paste maintains a "conversation session":

```python
class ClaudeCodeSummarizer:
    def __init__(self, ..., session_enabled: bool = False):
        self._session_id: str | None = None
        self._session_enabled = session_enabled

    def summarize(self, text: str, ...) -> str:
        if self._session_enabled and self._session_id:
            # Continue existing session
            response = invoke_claude(
                prompt=text,
                session_resume=self._session_id,
                ...
            )
        else:
            # Fresh session
            response = invoke_claude(prompt=text, ...)

        # Store session ID for next invocation
        if self._session_enabled:
            self._session_id = response.get("session_id")

        return response["result"]

    def reset_session(self) -> None:
        """Clear the current session, starting fresh next time."""
        self._session_id = None
```

Session management API:

| Method | Behavior |
|--------|----------|
| `reset_session()` | Clear session ID. Next invocation starts fresh. |
| `_session_id` property | Current session ID (or None). |

The session resets automatically when:
- The user changes the model in Settings
- The user changes the system prompt
- 30 minutes have passed since the last invocation (configurable)
- The user explicitly resets via a tray menu action

### 7.3 Session ID Storage

Session IDs are stored **in memory only** (attribute on `ClaudeCodeSummarizer`).
They are NOT persisted to config.toml or disk. When Voice Paste restarts, the
session starts fresh. Claude Code's own session files remain on disk (in
`~/.claude/projects/`), but Voice Paste does not reference them across restarts.

---

## 8. Settings UI Design

### 8.1 New "Claude Code" Section in Summarization Tab

The existing Summarization tab in the Settings dialog gains a new provider
option. When "Claude Code" is selected, Claude-specific settings appear:

```
+-- Summarization ------------------------------------------------+
|                                                                  |
|  [x] Enable text cleanup and summarization                       |
|                                                                  |
|  Provider: [ Claude Code      v]                                 |
|                                                                  |
|  +-- Claude Code Settings ----------------------------------+    |
|  |                                                          |    |
|  |  Status: [*] Installed (v2.1.47)                         |    |
|  |      or: [!] Not installed. Run: npm i -g @anthropic...  |    |
|  |                                                          |    |
|  |  Model:  [ sonnet          v]                            |    |
|  |          (haiku / sonnet / opus / custom)                |    |
|  |                                                          |    |
|  |  Tool Access:                                            |    |
|  |    [x] Read files (Read, Grep, Glob)                     |    |
|  |    [x] Web search (WebSearch, WebFetch)                  |    |
|  |    [ ] Edit files (Edit, Write)                          |    |
|  |    [ ] Run commands (Bash)                               |    |
|  |                                                          |    |
|  |  Budget limit per request: [$0.10     ]                  |    |
|  |  Timeout (seconds):        [60        ]                  |    |
|  |                                                          |    |
|  |  Working directory: [C:\Users\tim    ] [Browse...]       |    |
|  |    (Directory Claude can access. Leave empty for home.)  |    |
|  |                                                          |    |
|  |  [x] Enable conversation continuity                      |    |
|  |      (Claude remembers context across voice commands)    |    |
|  |      Session timeout: [30] minutes                       |    |
|  |      [Reset Session]                                     |    |
|  |                                                          |    |
|  +----------------------------------------------------------+    |
|                                                                  |
|  System Prompt:                                                  |
|  +----------------------------------------------------------+    |
|  | Du bist ein Textbereinigungsassistent...                 |    |
|  +----------------------------------------------------------+    |
|                                                                  |
+------------------------------------------------------------------+
```

### 8.2 UI Behavior

**Provider dropdown**: When the user selects "Claude Code", the Claude-specific
settings frame becomes visible. When any other provider is selected, it is
hidden. This follows the existing pattern of showing/hiding backend-specific
controls (e.g., local model controls appear only when STT backend = "local").

**Status indicator**: On provider selection, Voice Paste runs
`get_claude_version()` asynchronously. The status label shows:
- Green checkmark + version if installed
- Warning icon + installation instructions if not found

**Tool access checkboxes**: Grouped by risk tier. Each checkbox maps to a set of
tool names in `--allowedTools`. The mapping is:

```python
CLAUDE_TOOL_TIERS = {
    "read_files": ["Read", "Grep", "Glob"],
    "web_access": ["WebSearch", "WebFetch"],
    "edit_files": ["Edit", "Write", "NotebookEdit"],
    "run_commands": ["Bash"],
}
```

When "Run commands" is checked, a warning label appears:
"Caution: Claude can execute shell commands on your system."

**Budget limit**: A text entry with validation (positive float, min $0.01,
max $5.00). Default $0.10.

**Working directory**: A text entry with a "Browse..." button that opens a
directory chooser dialog. Empty means user home directory.

**Session controls**: The "Enable conversation continuity" checkbox shows the
session timeout spinner and "Reset Session" button when checked.

---

## 9. Configuration Schema

### 9.1 Config.toml Additions

```toml
[summarization]
# Existing fields...
enabled = true
provider = "claude"  # NEW value: "claude" (alongside "openai", "openrouter", "ollama")
model = "gpt-4o-mini"
base_url = ""
custom_prompt = ""

# --- Claude Code CLI settings (only used when provider = "claude") ---
[claude]
# Model alias: "haiku", "sonnet", "opus", or a full model name
model = "sonnet"
# Tool access tiers (each enables a group of Claude Code tools)
tool_read_files = true       # Read, Grep, Glob
tool_web_access = true       # WebSearch, WebFetch
tool_edit_files = false      # Edit, Write, NotebookEdit
tool_run_commands = false    # Bash
# Maximum spend per invocation (USD). Prevents runaway agentic loops.
max_budget_usd = 0.10
# Subprocess timeout in seconds. Kills the process if exceeded.
timeout_seconds = 60
# Working directory for Claude (empty = user home directory)
working_directory = ""
# Session continuity: remember context across voice commands
session_enabled = false
# Session timeout in minutes (session resets after this period of inactivity)
session_timeout_minutes = 30
```

### 9.2 AppConfig Additions

```python
@dataclass
class AppConfig:
    # ... existing fields ...

    # --- Claude Code CLI fields ---
    claude_model: str = "sonnet"
    claude_tool_read_files: bool = True
    claude_tool_web_access: bool = True
    claude_tool_edit_files: bool = False
    claude_tool_run_commands: bool = False
    claude_max_budget_usd: float = 0.10
    claude_timeout_seconds: int = 60
    claude_working_directory: str = ""
    claude_session_enabled: bool = False
    claude_session_timeout_minutes: int = 30

    @property
    def claude_allowed_tools(self) -> list[str]:
        """Build the --allowedTools list from tier flags."""
        tools: list[str] = []
        if self.claude_tool_read_files:
            tools.extend(["Read", "Grep", "Glob"])
        if self.claude_tool_web_access:
            tools.extend(["WebSearch", "WebFetch"])
        if self.claude_tool_edit_files:
            tools.extend(["Edit", "Write", "NotebookEdit"])
        if self.claude_tool_run_commands:
            tools.append("Bash")
        return tools
```

### 9.3 Validation

```python
CLAUDE_VALID_MODELS = ("haiku", "sonnet", "opus")

# In load_config():
claude_model = claude_section.get("model", "sonnet")
# Allow any string (for full model names like "claude-sonnet-4-6")
# but validate common aliases
if claude_model in CLAUDE_VALID_MODELS:
    pass  # known alias, fine
elif claude_model.startswith("claude-"):
    pass  # full model name, fine
else:
    logger.warning(
        "Unknown Claude model '%s'. Using 'sonnet'.", claude_model
    )
    claude_model = "sonnet"

claude_max_budget = claude_section.get("max_budget_usd", 0.10)
if not (0.01 <= claude_max_budget <= 5.0):
    logger.warning(
        "claude max_budget_usd %.2f out of range [0.01, 5.00]. "
        "Clamping to range.",
        claude_max_budget,
    )
    claude_max_budget = max(0.01, min(5.0, claude_max_budget))
```

---

## 10. Component Diagram

### 10.1 Architecture with Claude Code Integration

```
+--------------------------------------------------------------------+
|                        main.py (Entry Point)                        |
+--------------------------------------------------------------------+
|                                                                      |
|  +------------------+     +------------------+                       |
|  |  Hotkey Manager  |---->|  State Machine   |                       |
|  | (keyboard lib)   |     |  (AppState enum) |                       |
|  +------------------+     +--------+---------+                       |
|                                    |                                 |
|                        +-----------+-----------+                     |
|                        |                       |                     |
|                  +-----v------+         +------v-------+             |
|                  |   Audio     |         |   Paste      |            |
|                  |   Recorder  |         |   Manager    |            |
|                  | (sounddevice)|        | (clipboard + |            |
|                  +-----+------+         |  Ctrl+V sim) |            |
|                        |                +--------------+             |
|                  +-----v------+                                      |
|                  | STT Backend |  <-- Factory + Protocol             |
|                  +-----+------+                                      |
|                        |                                             |
|               +--------+--------+                                    |
|               |                 |                                     |
|        +------v-------+ +------v---------+                           |
|        | Cloud Whisper | | Local Whisper  |                          |
|        | (OpenAI API)  | | (faster-whisper)|                         |
|        +--------------+ +----------------+                           |
|                                                                      |
|                  +----------------+                                   |
|                  | Summarizer     |  <-- Factory + Protocol           |
|                  +-------+--------+                                   |
|                          |                                            |
|           +--------------+----------+----------+                     |
|           |              |          |          |                      |
|  +--------v--+   +------v---+  +---v------+  +v--------------+      |
|  | OpenAI    |   |OpenRouter|  | Ollama   |  | Claude Code   | NEW  |
|  | GPT-4o-mi |   | (API)    |  | (local)  |  | CLI           |      |
|  | (openai   |   | (openai  |  | (openai  |  | (subprocess)  |      |
|  |  SDK)     |   |  SDK)    |  |  SDK)    |  |               |      |
|  +-----------+   +----------+  +----------+  +------+--------+      |
|                                                      |               |
|                                               +------v--------+     |
|                                               | claude CLI    |     |
|                                               | (external     |     |
|                                               |  process)     |     |
|                                               | --print       |     |
|                                               | --output json |     |
|                                               | --model X     |     |
|                                               | --allowedTools|     |
|                                               +---------------+     |
|                                                                      |
|  +------------------+     +------------------+                       |
|  | TTS Backend      |     | Settings Dialog  |                       |
|  | (ElevenLabs /    |     | (tkinter, v0.7+) |                       |
|  |  Piper)          |     | + Claude tab     |                       |
|  +------------------+     +------------------+                       |
|                                                                      |
|  +------------------+     +------------------+                       |
|  |  System Tray     |     |  Keyring Store   |                       |
|  |  (pystray)       |     |  (Cred Manager)  |                       |
|  +------------------+     +------------------+                       |
+----------------------------------------------------------------------+
```

### 10.2 Data Flow: Voice Command with Claude Code

```
User speaks: "Was sind die offenen Issues in diesem Repo?"
    |
    v
[Microphone] --> [AudioRecorder] --> WAV bytes
    |
    v
[STT Backend] --> "Was sind die offenen Issues in diesem Repo?"
    |
    v
[ClaudeCodeSummarizer.summarize()]
    |
    | Builds command:
    | claude -p "Was sind die offenen Issues in diesem Repo?"
    |   --output-format json
    |   --model sonnet
    |   --append-system-prompt "You are a helpful voice assistant..."
    |   --allowedTools "Read,Grep,Glob,WebSearch,WebFetch"
    |   --max-budget-usd 0.10
    |   --permission-mode dontAsk
    |
    v
[subprocess.run(cmd, cwd=working_dir, timeout=60)]
    |
    | Claude Code internally:
    |   1. Reads the prompt
    |   2. Decides to use Bash(git) -- but Bash is NOT in allowedTools
    |   3. Falls back to Grep to search for issue-related files
    |   4. Formulates answer from available context
    |
    v
[JSON response] --> parse --> extract "result" field
    |
    v
"Es gibt 3 offene Issues: #12 Bug in der Authentifizierung,
 #15 Performance-Problem beim Laden, #18 Dark-Mode-Fehler."
    |
    v
[Paste at cursor] or [TTS speak aloud]
```

### 10.3 Threading Model (No Change)

Claude Code integration does NOT add any threads. The `subprocess.run()` call
happens on the existing pipeline worker thread (Thread 2). The subprocess itself
is an external OS process -- it does not share Voice Paste's process space.

```
Main Thread:     pystray event loop (system tray)
Thread 1:        keyboard hotkey listener (daemon)
Thread 2:        Pipeline worker (per session, daemon)
                 ^-- subprocess.run("claude -p ...") blocks HERE
Thread 3:        Settings dialog tkinter (on demand)
```

---

## 11. Implementation Phases

### Phase 1: Core Integration (MVP)

**Goal**: Claude Code as a working summarization/prompt provider.

**Files to create:**

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `src/claude_backend.py` | `ClaudeCodeSummarizer` class, CLI invocation, output parsing | ~250 |

**Files to modify:**

| File | Changes | Est. Lines Changed |
|------|---------|-------------------|
| `src/constants.py` | Add `CLAUDE_*` constants (default model, tool tiers, budget defaults) | +30 |
| `src/config.py` | Add `claude_*` fields to `AppConfig`, load/save/validate | +60 |
| `src/main.py` | Add Claude Code to `_rebuild_summarizer()` provider switch | +15 |

**Deliverables:**
- Working `claude` provider that can be selected via `config.toml`
- Graceful degradation if CLI not installed
- Full logging of invocations (no transcript content, per REQ-S24)

**Estimated effort**: 2 days

### Phase 2: Settings UI

**Goal**: Configure Claude Code from the Settings dialog.

**Files to modify:**

| File | Changes | Est. Lines Changed |
|------|---------|-------------------|
| `src/settings_dialog.py` | Add Claude Code controls in Summarization tab (model dropdown, tool checkboxes, budget entry, working dir picker, session controls) | +120 |
| `src/config.py` | Add save_to_toml() support for Claude fields | +20 |

**Deliverables:**
- Full Settings UI for Claude Code configuration
- CLI status detection (installed/not installed indicator)
- Tool tier checkboxes with risk warnings
- Budget and timeout controls
- Working directory picker

**Estimated effort**: 2 days

### Phase 3: Session Continuity

**Goal**: Opt-in multi-turn conversations.

**Files to modify:**

| File | Changes | Est. Lines Changed |
|------|---------|-------------------|
| `src/claude_backend.py` | Add session management (`--resume`, session timeout, reset) | +50 |
| `src/settings_dialog.py` | Session toggle, timeout spinner, reset button | +30 |
| `src/tray.py` | "Reset Claude Session" menu item (optional) | +10 |

**Deliverables:**
- Session continuity toggle in Settings
- Session timeout with automatic reset
- Manual session reset from tray menu

**Estimated effort**: 1 day

### Phase 4: Testing and Documentation

**Goal**: Comprehensive tests and user documentation.

**Files to create:**

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `tests/test_claude_backend.py` | Unit tests for ClaudeCodeSummarizer (mocked subprocess) | ~200 |

**Files to modify:**

| File | Changes |
|------|---------|
| `README.md` | Claude Code setup instructions, configuration reference |
| `CHANGELOG.md` | Release notes for Claude Code integration |
| `docs/ADR.md` | Add Claude Code to the main ADR |

**Estimated effort**: 1.5 days

### Total Estimated Effort: 6.5 days

---

## 12. Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| **Claude CLI not installed** | High (many users will not have it) | Clear detection, helpful error messages, installation instructions in Settings UI. |
| **Claude CLI version incompatibility** | Medium | Parse `--version` output. Warn if below minimum required version. Test with 2.x series. |
| **Subprocess timeout on complex queries** | Medium | Default 60s timeout. Configurable. Tray notification on timeout. Paste raw transcript as fallback. |
| **Agentic loops exceed budget** | Low | `--max-budget-usd` enforced by CLI. Default $0.10. |
| **Prompt injection via transcribed speech** | Low | Tool restrictions + system prompt safety instructions + Claude's built-in safety. Background noise artifacts are unlikely to form coherent attacks. |
| **Claude CLI auth expired** | Medium | Detect auth errors in stderr. Show "Run `claude auth` in terminal" notification. |
| **Working directory exposes sensitive files** | Low | Default to home directory. User controls scope via Settings. Tool restrictions limit what Claude can do with file access. |
| **Session state becomes stale** | Low | Auto-reset after configurable timeout (default 30 min). Manual reset via tray menu. |
| **Large output exceeds paste buffer** | Very Low | Claude's responses are typically short. The clipboard can handle megabytes. |
| **subprocess.run() inherits environment** | Low | `claude` CLI uses its own auth. No Voice Paste secrets are in env vars. The `openai` SDK API key is not passed to the subprocess. |
| **PyInstaller bundle path issues** | Low | `claude` is resolved via `shutil.which()` which searches PATH. PyInstaller's frozen path does not affect this. |
| **Cost surprises for users** | Medium | Budget limit default $0.10. Show cost in tray notification after each invocation. Cumulative cost tracking in future. |

---

## 13. Rejected Alternatives

### 13.1 Rejected: Anthropic Python SDK (`anthropic` package)

**Why considered**: Direct API access to Claude models without the CLI.

**Why rejected**:
- Adds a new dependency (~5 MB) to the binary.
- Requires a separate `ANTHROPIC_API_KEY` credential.
- No tool execution environment -- would need to build our own.
- No session management -- would need to implement conversation storage.
- The CLI already provides all of this for free.

**When to reconsider**: If we need streaming responses for real-time paste, or
if we need to support Claude models on systems where the CLI cannot be installed.

### 13.2 Rejected: Persistent Claude Process (Popen + stdin/stdout piping)

**Why considered**: Keep a `claude` process running and pipe prompts to it for
lower per-invocation latency (no process startup overhead).

**Why rejected**:
- Claude CLI is designed for one-shot `--print` mode. Persistent stdin piping
  is not a documented or supported interaction pattern.
- Process management complexity (handle crashes, restarts, zombie processes).
- Memory: a persistent Claude CLI process consumes ~50-100 MB of RAM.
- The 500ms process startup overhead is acceptable for a voice-triggered tool
  (the user does not notice it amid the 2-5 second STT + LLM latency).

### 13.3 Rejected: Claude Code as a Separate Mode with Dedicated Hotkey

**Why considered**: A dedicated `Ctrl+Alt+C` hotkey that always invokes Claude
Code, separate from the summarization/prompt modes.

**Why rejected**:
- Adds hotkey complexity. The user already has 4 hotkeys to remember.
- Claude Code is a **backend provider**, not a **mode**. The mode (summary,
  prompt, tts_ask) determines the UX flow; the provider determines who does the
  AI work. These are orthogonal.
- A dedicated hotkey would bypass the summarization provider selection, creating
  a confusing dual-path architecture.

**However**: If user feedback indicates that people want to use Claude Code
specifically for agentic queries while keeping GPT-4o-mini for fast summarization,
we could add a "Claude Code mode" hotkey in a future version. This would be
analogous to how `Ctrl+Alt+R` (summary) and `Ctrl+Alt+A` (prompt) use the same
provider but different system prompts.

### 13.4 Rejected: Using --dangerously-skip-permissions

**Why considered**: Simplifies invocation because Claude does not hang on
permission prompts in `--print` mode.

**Why rejected absolutely**:
- Voice Paste runs on the user's desktop with full network access. This is NOT
  a sandboxed environment.
- `--dangerously-skip-permissions` would allow Claude to run arbitrary Bash
  commands, edit any file, and make any HTTP request without any guardrail.
- Even with `--allowedTools` restrictions, the flag's existence in the command
  line is a security liability (if the tool list is misconfigured, the flag
  removes the last safety net).
- The `--permission-mode dontAsk` flag achieves the same non-interactive behavior
  for the tools in the allowed set, without bypassing the permission system
  entirely.

### 13.5 Rejected: MCP Server Integration

**Why considered**: Voice Paste could expose an MCP (Model Context Protocol)
server that Claude Code connects to, enabling richer bidirectional interaction.

**Why rejected for now**:
- Massive increase in complexity. MCP requires implementing a server with tool
  definitions, request handlers, and connection management.
- Voice Paste is a simple pipeline tool, not a platform. MCP integration would
  be overengineering for the current use case.
- The subprocess invocation pattern is one-directional (Voice Paste calls Claude,
  not the other way around), which is the correct dependency direction.

**When to reconsider**: If Claude Code gains native support for "plugins" that
can be auto-discovered, or if we want Claude Code to proactively push
notifications to Voice Paste.

---

## 14. Open Questions

| # | Question | Default Assumption | Decision Needed? |
|---|----------|-------------------|-----------------|
| 1 | Should Claude Code replace the existing `Ctrl+Alt+A` prompt behavior, or be a separate provider behind the same hotkey? | Separate provider behind the same hotkey. User picks provider in Settings. | No (clear recommendation) |
| 2 | Should the `--model` flag accept free-form text (any model name) or be restricted to known aliases? | Accept any string, validate known aliases. | No |
| 3 | What is the minimum Claude CLI version required? | 2.0.0 (first stable `--print` + `--output-format json`). | Yes -- need to verify exact version that supports all used flags. |
| 4 | Should we show Claude's cost per invocation in the tray notification? | Yes, as an optional info line. | Preference question |
| 5 | Should session continuity be the default? | No. Stateless by default, session continuity opt-in. | No (clear recommendation) |
| 6 | Should we support `--add-dir` to give Claude access to additional directories beyond the working directory? | Not in MVP. Add if users request it. | No |
| 7 | What should the default system prompt be for Claude Code in prompt mode? | Safety-aware prompt (Section 5.3) + the existing `PROMPT_SYSTEM_PROMPT`. | Minor preference |
| 8 | Should the budget limit be per-invocation or daily cumulative? | Per-invocation in MVP. Daily cumulative tracking in a future version. | No |
| 9 | Should Voice Paste bundle a custom Claude Code skill (`.claude/commands/`)? | Not in MVP. Interesting future direction. | Not yet |
| 10 | Should the "Run commands" checkbox require a confirmation dialog? | Yes -- a modal warning "This allows Claude to execute shell commands." | Preference question |

---

## Appendix A: Claude Code CLI Flags Reference

Flags used by Voice Paste:

| Flag | Purpose | Our Usage |
|------|---------|-----------|
| `-p` / `--print` | Non-interactive mode, print and exit | Always used |
| `--output-format json` | JSON response envelope | Always used |
| `--model <alias>` | Model selection | Configured per user |
| `--append-system-prompt <text>` | Add to system prompt | Safety instructions |
| `--allowedTools <tools>` | Tool whitelist | Based on tier checkboxes |
| `--max-budget-usd <amount>` | Cost cap | Default $0.10 |
| `--json-schema <schema>` | Structured output | Only in prompt/tts_ask mode |
| `-c` / `--continue` | Continue last session | When session enabled |
| `-r` / `--resume <id>` | Resume specific session | When session enabled |
| `--permission-mode dontAsk` | Non-interactive permissions | Always used |

Flags we explicitly **never** use:

| Flag | Why Not |
|------|---------|
| `--dangerously-skip-permissions` | Security: never bypass permissions |
| `--allow-dangerously-skip-permissions` | Same reason |
| `--tools ""` | Would disable all tools, making Claude useless |

## Appendix B: Error Messages

| Condition | Tray Notification Text |
|-----------|----------------------|
| CLI not installed | "Claude Code CLI not found.\nInstall: npm install -g @anthropic-ai/claude-code" |
| CLI auth failed | "Claude Code authentication failed.\nRun 'claude auth' in a terminal." |
| Timeout | "Claude Code timed out after {N} seconds.\nRaw transcript pasted instead." |
| Budget exceeded | "Claude Code budget limit reached (${X}).\nIncrease in Settings > Summarization." |
| Unknown error | "Claude Code error.\nCheck voice-paste.log for details." |
| Empty response | "Claude Code returned no output.\nRaw transcript pasted instead." |

## Appendix C: Cost Estimation

Approximate costs per Voice Paste invocation with Claude Code:

| Model | Input (500 tokens, ~30s speech) | Output (~200 tokens) | Total |
|-------|--------------------------------|---------------------|-------|
| Haiku | $0.0004 | $0.0002 | ~$0.0006 |
| Sonnet | $0.0015 | $0.0010 | ~$0.0025 |
| Opus | $0.0075 | $0.0050 | ~$0.0125 |

With tool use (e.g., Grep + Read = 2 additional turns):

| Model | 3-turn invocation | Daily (50 invocations) | Monthly |
|-------|-------------------|----------------------|---------|
| Haiku | ~$0.002 | $0.10 | $3.00 |
| Sonnet | ~$0.008 | $0.40 | $12.00 |
| Opus | ~$0.040 | $2.00 | $60.00 |

**Recommendation**: Default to Sonnet. It provides excellent quality at
reasonable cost. Haiku for budget-conscious users. Opus for maximum quality.

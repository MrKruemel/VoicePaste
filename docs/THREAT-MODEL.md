# Threat Model

## Voice-to-Summary Paste Tool

**Date**: 2026-02-13
**Author**: Security Engineer

---

## 1. System Overview

The Voice-to-Summary Paste Tool is a Windows desktop application that:
1. Captures microphone audio via global hotkey
2. Sends audio to a cloud API (OpenAI Whisper) for transcription
3. Optionally sends transcript to a cloud LLM (OpenAI GPT-4o-mini) for summarization
4. Pastes the result at the cursor position via clipboard

The tool handles **sensitive data** at every stage: voice recordings, transcribed text (potentially containing confidential information), API credentials, and clipboard contents.

---

## 2. Asset Inventory

| Asset | Sensitivity | Location | Lifecycle |
|-------|-------------|----------|-----------|
| **Audio recording** | HIGH -- may contain passwords, PII, confidential business info | In-memory buffer only | Created on record start, destroyed after API call or on cancel |
| **OpenAI API key** | HIGH -- grants access to paid API, could incur charges | config.toml on disk | Persistent; read at startup |
| **Transcript text** | MEDIUM-HIGH -- contains spoken content | In-memory only | Created by API response, destroyed after paste |
| **Summary text** | MEDIUM -- processed version of transcript | In-memory only | Created by API response, destroyed after paste |
| **Clipboard contents** | MEDIUM -- user's existing clipboard data | OS clipboard | Backed up before paste, restored after (v0.2+) |
| **Log file** | LOW-MEDIUM -- could contain error details with PII | Disk (voice-paste.log) | Persistent; grows over time |
| **config.toml** | MEDIUM -- contains API key and preferences | Disk | Persistent |

---

## 3. Threat Analysis

### T1: API Key Exposure

**Threat**: The OpenAI API key is stored in plaintext in config.toml and could be exposed through:
- Accidental commit to version control
- Malware reading the config file
- Log file containing the key
- Screen sharing showing the config file

**Risk**: HIGH -- API key misuse leads to unauthorized charges.

**Mitigations**:
- [REQ-S01] **Never log the API key.** Mask it in all log output (show only last 4 characters).
- [REQ-S02] **Never hardcode the API key** in source code. Always read from config.
- [REQ-S03] **Include config.toml in .gitignore** by default. Ship only config.example.toml.
- [REQ-S04] **Document in README**: "Keep your config.toml private. Never share it or commit it to version control."
- [REQ-S05] **File permissions**: On creation, set config.toml permissions to user-only read/write (Windows ACL) where practical. At minimum, document this recommendation.

### T2: Audio Data in Transit

**Threat**: Voice recordings are sent over the network to OpenAI's API. Interception could expose sensitive spoken content.

**Risk**: MEDIUM -- mitigated by HTTPS but user must trust the API provider.

**Mitigations**:
- [REQ-S06] **HTTPS only.** All API calls must use HTTPS. Never allow HTTP fallback.
- [REQ-S07] **TLS certificate validation must be enabled.** Never set `verify=False` in HTTP clients.
- [REQ-S08] **Document data flow**: README must clearly state that audio is sent to OpenAI's servers. Users must consent to this by configuring their API key.

### T3: Audio Data at Rest

**Threat**: Audio recordings could be written to disk (temp files, crash dumps) and later recovered.

**Risk**: HIGH if audio hits disk, LOW if strictly in-memory.

**Mitigations**:
- [REQ-S09] **Audio must never be written to disk.** All audio data stays in in-memory buffers (BytesIO, numpy arrays). No temp files.
- [REQ-S10] **On error or cancel, audio buffers must be explicitly cleared** (overwrite with zeros, then delete reference).
- [REQ-S11] **No audio data in logs.** Never log raw audio bytes, base64-encoded audio, or audio file paths.

### T4: Clipboard Data Leakage

**Threat**: The tool temporarily overwrites the clipboard with the transcript. During this window:
- Another application could read the transcript from the clipboard
- If the tool crashes during paste, the transcript remains on the clipboard
- Original clipboard contents could be lost

**Risk**: MEDIUM -- clipboard is a shared OS resource.

**Mitigations**:
- [REQ-S12] **Minimize clipboard exposure window.** Write to clipboard, paste, restore as quickly as possible. Target <500ms total clipboard exposure.
- [REQ-S13] **Always restore clipboard in a finally block.** Even if paste simulation fails, original contents must be restored (v0.2+).
- [REQ-S14] **Never log clipboard contents** (neither the transcript being pasted nor the backed-up original).

### T5: Global Hotkey as Attack Vector

**Threat**: The `keyboard` library uses low-level Windows hooks (SetWindowsHookEx). This is the same mechanism used by keyloggers. Risks:
- Antivirus false positives
- The hook could theoretically be exploited by other processes

**Risk**: LOW -- the tool only registers specific hotkey combinations, not a full keylogger.

**Mitigations**:
- [REQ-S15] **Only hook the specific hotkey combination** (Ctrl+Win and Escape). Do not use blanket keyboard monitoring.
- [REQ-S16] **Document antivirus considerations** in README. Provide guidance for whitelisting.
- [REQ-S17] **Consider code signing** for the .exe to reduce antivirus false positives (v1.0).

### T6: Transcript/Summary Content Injection

**Threat**: The transcribed or summarized text is pasted into the user's active application. If the text contains:
- Executable commands (in a terminal)
- SQL injection payloads (in a database tool)
- Script injection (in a web form)

The tool could inadvertently execute malicious content that was spoken.

**Risk**: LOW -- this is the user's own speech being transcribed. The risk is equivalent to the user typing the same text.

**Mitigations**:
- [REQ-S18] **Paste as plain text only.** Never paste rich text, HTML, or formatted content. Use CF_UNICODETEXT clipboard format.
- [REQ-S19] **Document the risk** in README: "The tool pastes text as-is. Be aware that pasting into a terminal or command prompt will execute any commands in the pasted text."

### T7: Dependency Supply Chain

**Threat**: Third-party packages (keyboard, sounddevice, openai, pystray, Pillow) could contain malicious code, especially if installed from compromised PyPI mirrors.

**Risk**: MEDIUM -- standard supply chain risk for Python projects.

**Mitigations**:
- [REQ-S20] **Pin all dependencies** to specific versions in requirements.txt.
- [REQ-S21] **Audit dependencies** before each release. Check for known CVEs using `pip-audit` or `safety`.
- [REQ-S22] **Minimize dependency count.** Only add a dependency when the alternative (stdlib/manual implementation) is significantly worse.
- [REQ-S23] **Install from official PyPI only.** Document this in build instructions.

### T8: Log File Information Disclosure

**Threat**: Log files may accumulate sensitive information over time: error messages containing user text, API responses, file paths, system information.

**Risk**: LOW-MEDIUM -- depends on log content.

**Mitigations**:
- [REQ-S24] **Never log**: API keys, audio data, transcript content, clipboard content, or full API responses.
- [REQ-S25] **Log only**: Timestamps, state transitions, success/failure status, error types (not full messages from API), performance metrics (duration, audio length).
- [REQ-S26] **Log rotation**: Implement log file size limit (e.g., 5 MB) with rotation. Prevent unbounded growth.

### T9: Multiple Instance Race Condition

**Threat**: If two instances run simultaneously, both register the same hotkey and compete for microphone access and clipboard writes, leading to undefined behavior.

**Risk**: LOW -- unlikely but possible.

**Mitigations**:
- [REQ-S27] **Single-instance enforcement** via named mutex (Windows) or lock file. Second instance exits with message. (v0.2+; acceptable to defer from v0.1.)

---

## 4. Security Requirements Summary

### Critical (Must have for v0.1)

| ID | Requirement | Validates Against |
|----|-------------|-------------------|
| REQ-S01 | Never log the API key | T1 |
| REQ-S02 | Never hardcode the API key | T1 |
| REQ-S06 | HTTPS only for all API calls | T2 |
| REQ-S07 | TLS certificate validation enabled | T2 |
| REQ-S09 | Audio never written to disk | T3 |
| REQ-S11 | No audio data in logs | T3 |
| REQ-S15 | Only hook specific hotkey combinations | T5 |
| REQ-S18 | Paste as plain text only (CF_UNICODETEXT) | T6 |

### High (Must have for v0.2)

| ID | Requirement | Validates Against |
|----|-------------|-------------------|
| REQ-S03 | config.toml in .gitignore | T1 |
| REQ-S10 | Clear audio buffers on error/cancel | T3 |
| REQ-S12 | Minimize clipboard exposure window (<500ms) | T4 |
| REQ-S13 | Always restore clipboard in finally block | T4 |
| REQ-S14 | Never log clipboard contents | T4 |
| REQ-S24 | Never log transcript content | T8 |
| REQ-S25 | Log only safe data (timestamps, states, status) | T8 |
| REQ-S27 | Single-instance enforcement | T9 |

### Medium (Must have for v1.0)

| ID | Requirement | Validates Against |
|----|-------------|-------------------|
| REQ-S04 | Document API key safety in README | T1 |
| REQ-S05 | Recommend user-only file permissions for config | T1 |
| REQ-S08 | Document data flow (audio sent to cloud) | T2 |
| REQ-S16 | Document antivirus considerations | T5 |
| REQ-S17 | Consider code signing for .exe | T5 |
| REQ-S19 | Document paste-into-terminal risk | T6 |
| REQ-S20 | Pin all dependency versions | T7 |
| REQ-S21 | Audit dependencies for CVEs before release | T7 |
| REQ-S22 | Minimize dependency count | T7 |
| REQ-S23 | Install from official PyPI only | T7 |
| REQ-S26 | Log file rotation (5 MB limit) | T8 |

---

## 5. Security Review Checklist (Pre-Release)

This checklist must be completed before any release:

- [ ] No API keys or secrets in source code
- [ ] No API keys or secrets in log output (test with DEBUG level)
- [ ] All HTTP calls use HTTPS with certificate validation
- [ ] Audio data is never written to disk (verify with Process Monitor)
- [ ] Clipboard contents are restored in all code paths (happy path, error, cancel)
- [ ] Log file does not contain transcript text, clipboard data, or API keys
- [ ] All dependencies are pinned to specific versions
- [ ] `pip-audit` reports no known vulnerabilities
- [ ] .gitignore includes config.toml
- [ ] README documents: data flow, API key safety, antivirus notes
- [ ] Binary does not contain embedded plaintext secrets (strings analysis)

---

## 6. GDPR and Privacy Considerations

Since the tool processes voice data (personal data under GDPR):

1. **Data minimization**: Audio is kept in memory only for the duration of processing. No persistent storage.
2. **Purpose limitation**: Audio is used solely for transcription. Transcripts are used solely for pasting. No analytics, no telemetry.
3. **Third-party processing**: Audio and text are sent to OpenAI. Users must be aware of OpenAI's data processing policies.
4. **User control**: The user initiates every recording. No background recording. No always-on microphone.
5. **Data deletion**: Audio buffers are cleared after each operation. No local data retention beyond the log file (which contains no PII if requirements are followed).

**Recommendation for README**: Include a "Privacy" section stating:
- What data is collected (audio, only while recording)
- Where it is sent (OpenAI API)
- How long it is retained (in-memory only, cleared after paste)
- What is logged (state transitions and errors only, no content)

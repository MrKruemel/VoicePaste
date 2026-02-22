# Security Review -- VoicePaste v1.3 Wayland Support

**Date**: 2026-02-22
**Reviewer**: Security Engineer
**Scope**: Full security audit of Wayland compatibility changes + status review of previously identified findings
**Branch**: `feature/linux-support` (commit `edcd9ab`)

---

## Executive Summary

This review covers the major Wayland compatibility changes in VoicePaste v1.3, including evdev-based global hotkey monitoring, UInput-based keystroke injection, updated clipboard operations, and build configuration changes. The review also verifies the mitigation status of all previously identified findings.

**Overall Assessment: GO with conditions**

The codebase demonstrates strong security awareness. The development team has consistently applied good practices: no shell injection vectors, audio stays in memory, API keys are masked in logs, clipboard content is never logged, and the existing threat model requirements (REQ-S01 through REQ-S27) are enforced. The Wayland changes introduce new attack surface (evdev input monitoring, UInput injection) but the implementation is defensible.

Two new findings require attention before release. Neither is a release blocker, but both should be tracked:

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 0 |
| Medium | 1 (new) |
| Low | 2 (1 new, 1 carried) |
| Informational | 2 (carried) |

---

## New Findings

### SEC-082: UInput Device Capabilities Unrestricted -- RESOLVED

- **Severity**: Medium (was), now Low (after fix)
- **Status**: **RESOLVED** -- UInput capabilities restricted to KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V only
- **Category**: Privilege Escalation / Input Injection
- **Location**: `/home/mrkruemel/Projects/VoicePaste/VoicePaste/src/evdev_hotkey.py`, line 644
- **Description**: The `UInputController._ensure_device()` method creates the virtual keyboard with default capabilities, which allows injection of ALL KEY_* and BTN_* event codes. The `send_key()` method (line 667) accepts arbitrary key names from the full `_KEY_CODES` dictionary, not just paste-related keys. While current call sites only use "ctrl+v" and "ctrl+shift+v", the API surface permits arbitrary keystroke injection. If an attacker gains code execution within the process (e.g., through a dependency vulnerability), they could use the existing UInput device to inject arbitrary keystrokes into any application.

  The code at line 644:
  ```python
  self._uinput = evdev.UInput(
      name="VoicePaste Virtual Keyboard",
      phys="voicepaste/uinput",
  )
  ```

- **Remediation**: Restrict the UInput device capabilities to only the keycodes needed for paste simulation:

  ```python
  from evdev import ecodes

  _ALLOWED_UINPUT_KEYS = {
      ecodes.KEY_LEFTCTRL,
      ecodes.KEY_LEFTSHIFT,
      ecodes.KEY_V,
  }

  self._uinput = evdev.UInput(
      events={ecodes.EV_KEY: list(_ALLOWED_UINPUT_KEYS)},
      name="VoicePaste Virtual Keyboard",
      phys="voicepaste/uinput",
  )
  ```

  Additionally, consider restricting `send_key()` to an allowlist of permitted key combinations:
  ```python
  _ALLOWED_COMBOS = {"ctrl+v", "ctrl+shift+v"}

  def send_key(self, key_name: str) -> bool:
      if key_name.lower() not in _ALLOWED_COMBOS:
          logger.warning("UInput: combo '%s' not in allowlist.", key_name)
          return False
      # ... existing logic
  ```

  Note: If `send_key()` is also used for the generic `send_key()` function in `_linux.py` (line 661) which handles arbitrary keys like "enter" or "escape", the allowlist must be expanded accordingly, or the capability restriction should include those additional keycodes.

---

### SEC-083: UInput Device Not Cleaned Up on Shutdown -- RESOLVED

- **Severity**: Low
- **Status**: **RESOLVED** -- `cleanup_uinput()` now called in `_shutdown()` after `stop_monitor()`
- **Category**: Resource Management
- **Location**: `/home/mrkruemel/Projects/VoicePaste/VoicePaste/src/main.py`, line 1667-1673
- **Description**: The `_shutdown()` method calls `stop_monitor()` to stop the evdev keyboard monitor (line 1671), but does not call `cleanup_uinput()` to close the UInput virtual keyboard device. While the Linux kernel reclaims the device on process exit, explicit cleanup is preferable for:
  1. Clean device removal from `/sys/devices/virtual/input/`
  2. Ensuring no stale virtual keyboards if the process is kept alive
  3. Consistency with the cleanup pattern used for the monitor

  Current shutdown code (main.py lines 1667-1673):
  ```python
  # v1.3: Stop evdev monitor if running (Wayland)
  if sys.platform == "linux":
      try:
          from evdev_hotkey import stop_monitor
          stop_monitor()
      except ImportError:
          pass
  ```

- **Remediation**: Add `cleanup_uinput()` call:
  ```python
  if sys.platform == "linux":
      try:
          from evdev_hotkey import stop_monitor, cleanup_uinput
          stop_monitor()
          cleanup_uinput()
      except ImportError:
          pass
  ```

---

### SEC-078: Clipboard Write Return Code Not Checked (carried forward)

- **Severity**: Low
- **Category**: Clipboard Security
- **Location**: `/home/mrkruemel/Projects/VoicePaste/VoicePaste/src/platform_impl/_linux.py`, lines 255, 485
- **Description**: `subprocess.run()` for clipboard write operations (`clipboard_restore` at line 255, `paste_text` at line 485) does not check the return code. If `wl-copy`, `xclip`, or `xsel` fails silently (returns non-zero), the clipboard may contain stale data from the previous operation, leading to pasting the wrong content.

  Line 255:
  ```python
  subprocess.run(cmd, input=backup, text=True, timeout=2)
  ```

  Line 485:
  ```python
  subprocess.run(cmd, input=text, text=True, timeout=2)
  ```

- **Remediation**: Check return code and log warning on failure:
  ```python
  result = subprocess.run(cmd, input=text, text=True, timeout=2)
  if result.returncode != 0:
      logger.warning(
          "Clipboard write failed (exit code %d) using %s.",
          result.returncode, tool_name,
      )
      return False
  ```

---

## Previously Identified Findings -- Status Review

### Resolved Since Last Review

| ID | Description | Status |
|----|-------------|--------|
| SEC-069 | config.toml no chmod 0600 on Linux | **RESOLVED**: `config.py` lines 504, 576 now set `stat.S_IRUSR \| stat.S_IWUSR` |
| SEC-070 | Linux clipboard no input length limit | **RESOLVED**: `_MAX_CLIPBOARD_BYTES = 1MB` enforced at line 27 of `_linux.py` |
| SEC-071 | tray.py mktemp() race condition | **RESOLVED**: Now uses `tempfile.mkstemp(suffix=".png")` at line 46 of `tray.py` |

### Carried Forward (Unchanged)

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| SEC-050 | Medium | CORS allows any localhost port (CSRF vector) | OPEN -- API disabled by default; low practical risk |
| SEC-058 | Medium | Full user text in index.json on disk (GDPR) | OPEN -- recommend storing only preview/hash |
| SEC-066 | Medium | Unbounded text in index.json | OPEN -- same as SEC-058 |
| SEC-075 | Info | requirements.txt lacks --require-hashes | OPEN -- acceptable for desktop app |
| SEC-076 | Info | No code signing for Linux binary | OPEN -- recommend for future release |
| SEC-077 | Info | Dummy av module: confusing AttributeError | OPEN -- recommend `__getattr__` for clearer error |

---

## Detailed Audit Results by File

### 1. `src/evdev_hotkey.py` -- Evdev Monitor + UInput Controller

**Security Assessment: APPROVED (SEC-082 and SEC-083 resolved)**

Positive findings:
- Per-keystroke debug logging explicitly removed (privacy fix documented in docstring).
- `_held_keys` stores only integer keycodes, not character values.
- Device file descriptors properly closed on stop (`_close_devices`) and disconnect (lines 443-447).
- Wake pipe for clean select() interruption (lines 243, 268-270).
- Thread-safe: all shared state protected by locks.
- Callbacks fired in separate daemon threads (line 501-506) -- prevents blocking the event loop.
- Stale device cleanup handles bad file descriptors (lines 450-466).
- `_held_keys` cleared on device disconnect (line 448) -- prevents phantom modifier state.

Concerns addressed:
- **Keylogger potential**: The monitor inherently sees all keystrokes. This is unavoidable on Wayland. The code does NOT log, store, or transmit keystroke content. Only keycodes are compared against registered combos. The `_get_relevant_keycodes()` method (lines 468-483) exists but is not used for filtering events -- it was likely a helper for the now-removed per-keystroke logging. It poses no risk.
- **UInput capabilities**: See SEC-082 above.
- **UInput cleanup**: See SEC-083 above.

### 2. `src/platform_impl/_linux.py` -- Clipboard + Paste + System

**Security Assessment: APPROVED**

Positive findings:
- All subprocess calls use list arguments (no shell=True). Verified: zero instances of `shell=True` in entire `src/` directory.
- Clipboard content passed via stdin pipe, never as command arguments.
- Tool paths resolved to absolute via `shutil.which()`.
- All subprocess calls have 2-second timeouts.
- 1MB clipboard size limit (`_MAX_CLIPBOARD_BYTES`, line 27).
- Clipboard content never logged -- only tool name and character count.
- `_simulate_wayland_keystroke()` cascade: UInput -> ydotool -> wtype, with clear error logging.
- `_combo_to_ydotool_args()` and `_combo_to_wtype_args()` use only integer scancodes or whitelisted modifier names -- no injection possible.
- Terminal detection (`_is_terminal_focused`) uses xdotool/xprop with hardcoded argument lists.

Concerns:
- SEC-078: Clipboard write return code not checked (see finding above).
- Lock file at line 615 uses default permissions (carried forward from SEC-074, Low).

### 3. `src/hotkey.py` -- Hotkey Registration

**Security Assessment: APPROVED**

Positive findings:
- Clean platform dispatch: Windows (keyboard lib), Linux X11 (pynput), Linux Wayland (evdev).
- `_is_wayland()` check properly queries `XDG_SESSION_TYPE` environment variable.
- Debounce logic (lines 388-400) prevents rapid-fire callback execution.
- Cancel hotkey has no debounce (line 379) -- correct for user experience.
- All hotkey callbacks wrapped in try/except with exception logging.
- `_HotkeySlot` dataclass eliminates copy-paste patterns, reducing risk of inconsistent handling.

No concerns.

### 4. `src/local_stt.py` -- Local STT + av Stub

**Security Assessment: APPROVED**

Positive findings:
- av stub injection (lines 56-69): conditional, well-documented, fail-closed.
- Stub only injected if `av` is not already in `sys.modules` AND `import av` raises ImportError.
- `_wav_bytes_to_float32()` operates entirely in memory (BytesIO, numpy).
- REQ-S09 compliance: no disk writes.
- REQ-S24/S25 compliance: transcript content never logged, only metadata (char count, duration, language).
- Log handler flush before native code (lines 309-325): ensures diagnostics survive native crashes.
- ORT_DISABLE_ALL_TELEMETRY set for frozen builds (line 112): prevents onnxruntime network calls.

No concerns.

### 5. `rthook_av_stub.py` -- PyInstaller Runtime Hook

**Security Assessment: APPROVED**

Positive findings:
- Minimal code (7 functional lines).
- Conditional injection (only if `av` not in sys.modules).
- Module has `__version__`, `__path__` -- enough for faster-whisper's import to succeed.
- No executable logic in the stub module.

SEC-077 (Informational): Adding `__getattr__` would improve error messages if code accidentally accesses av functions.

### 6. `voice_paste_linux.spec` -- Build Configuration

**Security Assessment: APPROVED**

Positive findings:
- Comprehensive exclude list: removes test frameworks, GUI frameworks, web frameworks, SSH, Docker, system monitoring, crash reporting, etc.
- av and Cython explicitly excluded (saves ~119MB, removes unnecessary attack surface).
- system-site-packages leakage addressed: pygments, rich, flask, paramiko, etc. excluded.
- Runtime hooks properly configured: rthook_av_stub.py runs before any code can import av.
- Debug mode controlled by CLI flag, not hardcoded.
- Strip symbols in release mode (line 479).
- No UPX (correct for Linux ELF).

No concerns. The exclude list is thorough and well-documented.

### 7. `src/main.py` -- Version Logging Changes

**Security Assessment: APPROVED**

Positive findings (lines 1882-1930):
- Version logging wrapped in try/except for each library (evdev, pynput, sounddevice).
- Uses `importlib.metadata.version()` -- safe, no code execution.
- Session type logged (Wayland/X11) for diagnostics.
- No sensitive data in version logging output.
- evdev monitor shutdown added at lines 1667-1673.

SEC-083 resolved: `cleanup_uinput()` now called after `stop_monitor()` in `_shutdown()`.

---

## Privacy Compliance Verification

### Keystroke Logging: VERIFIED CLEAN

- Searched `evdev_hotkey.py` for any logging of key events, key names, or key values.
- Only logged items: hotkey combo string when matched (line 500), handle IDs for registration/removal.
- `_held_keys` set: contains integer keycodes only, never converted to characters, never logged.
- The docstring at line 28 explicitly documents: "Removed per-keystroke debug logging (privacy fix)."

### Audio Data: VERIFIED IN-MEMORY ONLY

- `audio.py`: numpy arrays -> BytesIO. No `open()`, no file writes. Buffers zeroed via `_clear_frames()`.
- `local_stt.py`: WAV decoding via BytesIO. No disk access.
- `stt.py`: audio_data sent to API via HTTPS. No disk writes.
- Searched all `src/` files for `open(.*w` and audio-related temp files: none found.

### API Keys: VERIFIED NEVER LOGGED

- All API key log entries use `config.masked_api_key()` which shows only last 4 chars.
- Keyring operations log key names (e.g., "Credential 'openai_api_key' stored"), never values.
- No raw API key strings in log output.

### Clipboard Content: VERIFIED NEVER LOGGED

- All clipboard log entries log only tool names, character counts, or operation status.
- `paste.py` (Windows) explicitly states REQ-S14 and logs only `len(text)`.
- `_linux.py` logs only tool name and operation results, never content.

### Telemetry: VERIFIED NONE

- No outbound connections other than configured STT/LLM/TTS/model-download endpoints.
- No analytics, crash reporting, or phone-home code found.
- `ORT_DISABLE_ALL_TELEMETRY` set for onnxruntime in frozen builds.

---

## Dependency Notes

### requirements.txt Status

All 33 direct dependencies are pinned with `==`. Notable:
- `Pillow==12.1.1`: Comment notes CVE-2026-25990. Verify this is the patched version.
- `keyboard==0.13.5`: Windows-only at runtime; Linux uses pynput/evdev.
- `pywin32-ctypes==0.2.3`: Windows-only; harmless on Linux but unused.
- `pynput` and `evdev`: NOT in requirements.txt (Linux-only, installed separately). This is documented in CLAUDE.md and build scripts.

### Supply Chain Observations

- Hash pinning (`--require-hashes`) not used. Acceptable for desktop application.
- No `pip-audit` or `safety check` in CI configuration observed. **Recommend adding.**
- Model downloads use SHA256 verification via `integrity.py` with `hmac.compare_digest` (timing-safe).

---

## Summary of All Open Findings

| ID | Severity | Category | Description | Remediation |
|----|----------|----------|-------------|-------------|
| ~~SEC-082~~ | ~~Medium~~ | Input Injection | ~~UInput capabilities unrestricted~~ | **RESOLVED** -- restricted to 3 keycodes |
| ~~SEC-083~~ | ~~Low~~ | Resource Management | ~~cleanup_uinput() not called on shutdown~~ | **RESOLVED** -- added to _shutdown() |
| **SEC-078** | Low | Clipboard | Write return code not checked | Check returncode, log warning |
| SEC-050 | Medium | API Security | CORS allows any localhost port | Restrict to configured port |
| SEC-058 | Medium | GDPR | Full user text in TTS cache index.json | Store only preview/hash |
| SEC-066 | Medium | GDPR | Unbounded text in index.json | Same as SEC-058 |
| SEC-075 | Info | Supply Chain | No --require-hashes in requirements.txt | Add hash pinning |
| SEC-076 | Info | Binary Security | No code signing | Sign binaries when feasible |
| SEC-077 | Info | Code Quality | av stub: confusing AttributeError | Add __getattr__ |

### By Priority

1. ~~**SEC-082** (Medium): Restrict UInput capabilities~~ -- **RESOLVED**
2. **SEC-058/066** (Medium): TTS cache GDPR -- full user text persists on disk.
3. ~~**SEC-083** (Low): Add cleanup_uinput() to shutdown~~ -- **RESOLVED**
4. **SEC-078** (Low): Check clipboard write return code -- improves reliability.
5. SEC-050 (Medium, existing): CORS port restriction -- low practical risk.
6. SEC-075/076/077 (Info): Track for future improvement.

---

## Release Recommendation

**GO (unconditional):**

1. ~~Fix SEC-082 and SEC-083 before release~~ -- **BOTH RESOLVED**.
2. Track SEC-058/066 (TTS cache GDPR) for the next release.
3. All Critical and High severity requirements from the threat model are MITIGATED.
4. No open Critical or High findings.
5. Privacy compliance verified: no keystroke logging, audio stays in memory, API keys never logged, clipboard never logged, no telemetry.

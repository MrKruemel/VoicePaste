# Test Plan: Paste Pipeline (Linux)

**Version**: 1.0
**Date**: 2026-02-22
**Author**: QA Agent
**Status**: Draft
**Scope**: Clipboard write, paste simulation, clipboard restore on Linux (X11 + Wayland)
**Related code**:
- `src/platform_impl/_linux.py` (paste_text, clipboard_backup/restore, _is_terminal_focused, _simulate_wayland_keystroke)
- `src/evdev_hotkey.py` (UInputController, uinput_send_key)
- `src/paste.py` (Windows-only, reference implementation)
- `src/main.py` (_run_pipeline, _wait_before_paste -- clipboard lifecycle)

---

## 1. Known Bugs Found During Code Review

### BUG-1: _is_terminal_focused() returns False on Wayland (CRITICAL REGRESSION)

**Severity**: Critical

**Steps to Reproduce**:
1. Run VoicePaste on a Wayland session (GNOME 44+, Ubuntu 24.04 default).
2. Open gnome-terminal.
3. Record speech and let the pipeline complete.
4. Observe that Ctrl+V is sent instead of Ctrl+Shift+V.

**Expected Behavior**: Ctrl+Shift+V is sent when a terminal emulator has focus.

**Actual Behavior**: `_is_terminal_focused()` always returns `False` on Wayland because it relies on `xdotool getactivewindow` and `xprop -id ... WM_CLASS`, which are X11 tools. On a pure Wayland session:
- `xdotool` cannot query the active window (no X11 display).
- `xprop` cannot read WM_CLASS (X11 property).
- Both tools either fail silently (exit code != 0) or `xdotool` is not installed at all.

The function catches the exception and returns `False`, so Ctrl+V is always sent. Most terminal emulators intercept Ctrl+V as "verbatim insert next char" (literally inserting the next keystroke), not "paste from clipboard". This makes pasting appear broken in terminals.

**Root Cause**: `_is_terminal_focused()` has no Wayland codepath. There is no standard Wayland protocol for querying the focused window's application class. The function should use a different approach on Wayland, or the paste strategy should be configurable.

**Environment**: Linux Wayland session (GNOME, KDE Plasma, Sway, etc.)

**Notes**: This is the most likely cause of the reported regression. Before the Wayland changes, VoicePaste only ran on X11 where xdotool/xprop work. Now it runs on Wayland but the terminal detection was not updated.

### BUG-2: Clipboard write with subprocess.run does not check return code

**Severity**: Medium

**Steps to Reproduce**:
1. In `paste_text()`, line 485: `subprocess.run(cmd, input=text, text=True, timeout=2)`
2. If wl-copy or xclip fails (e.g., Wayland compositor not responding), returncode is non-zero.
3. The code does not check returncode -- proceeds to simulate paste keystroke.

**Expected Behavior**: If clipboard write fails, paste_text should return False and log an error.

**Actual Behavior**: The text is not actually on the clipboard, but Ctrl+V is still sent. This pastes whatever was previously on the clipboard (or nothing).

**Root Cause**: Line 485 in `_linux.py` -- `subprocess.run()` called without `check=True` and without checking `result.returncode`.

### BUG-3: Race condition between clipboard write and paste simulation

**Severity**: Medium

**Steps to Reproduce**:
1. `paste_text()` writes to clipboard, sleeps 150ms, then simulates Ctrl+V.
2. On Wayland with wl-copy, the clipboard is held by the wl-copy process.
3. If UInput sends Ctrl+V before the compositor has processed the wl-copy selection offer, the paste reads stale data.

**Expected Behavior**: The paste always receives the just-written text.

**Actual Behavior**: Under load or on slow systems, the 150ms delay may be insufficient. wl-copy on Wayland works differently than xclip -- it serves clipboard requests on demand (stays running), so the timing model is different.

**Notes**: The 150ms delay was tuned for X11's xclip fork behavior. Wayland's wl-copy is a persistent process that registers as the clipboard selection owner asynchronously. Consider increasing the delay or using a readback verification.

### BUG-4: Clipboard restore timing issue with queued pipelines

**Severity**: Low

**Steps to Reproduce**:
1. Start a recording, complete the pipeline.
2. While processing, start a new recording (pipeline queueing).
3. The first pipeline calls `paste_text()` which writes text A to clipboard.
4. `time.sleep(0.1)` in the `finally` block at line 1604.
5. Clipboard restore is deferred to the second pipeline's `finally`.
6. Between the first paste and the second pipeline, the clipboard contains text A, not the user's original backup.

**Expected Behavior**: The user's original clipboard is restored after the last pipeline finishes.

**Actual Behavior**: This actually works correctly because `clip_backup` is passed through. However, the 100ms delay at line 1604 may not be enough for the paste keystroke (Ctrl+V) to be fully consumed by the target application. If clipboard_restore runs too soon, it overwrites the clipboard before the paste event is fully processed.

---

## 2. Manual Test Matrix

### 2.1 Environment Matrix

| ID | Session Type | Desktop Environment | Test Platform |
|----|-------------|---------------------|---------------|
| E1 | X11 | GNOME (Ubuntu 22.04) | Ubuntu 22.04 LTS |
| E2 | X11 | GNOME (Ubuntu 24.04) | Ubuntu 24.04 LTS |
| E3 | Wayland | GNOME (Ubuntu 24.04) | Ubuntu 24.04 LTS |
| E4 | Wayland | KDE Plasma 6 | Fedora 40+ or KDE Neon |
| E5 | Wayland | Sway/wlroots | Arch/Fedora |

### 2.2 Target Application Matrix

| ID | Application | Category | Paste Shortcut | Notes |
|----|------------|----------|----------------|-------|
| A1 | gnome-terminal | Terminal | Ctrl+Shift+V | WM_CLASS: gnome-terminal-server |
| A2 | konsole | Terminal | Ctrl+Shift+V | WM_CLASS: konsole |
| A3 | xterm | Terminal | Shift+Insert or middle-click | Does NOT use Ctrl+Shift+V! |
| A4 | alacritty | Terminal | Ctrl+Shift+V | WM_CLASS: Alacritty |
| A5 | kitty | Terminal | Ctrl+Shift+V | WM_CLASS: kitty |
| A6 | foot | Terminal (Wayland-native) | Ctrl+Shift+V | No WM_CLASS (Wayland-only) |
| A7 | wezterm | Terminal | Ctrl+Shift+V | WM_CLASS: org.wezfurlong.wezterm |
| A8 | Firefox | Browser | Ctrl+V | textarea, contenteditable |
| A9 | Chrome/Chromium | Browser | Ctrl+V | textarea, contenteditable |
| A10 | gedit/GNOME Text Editor | Text editor | Ctrl+V | |
| A11 | VS Code | IDE | Ctrl+V | Electron app |
| A12 | LibreOffice Writer | Office | Ctrl+V | Rich paste, but we send plain |
| A13 | Nautilus (file rename) | File manager | Ctrl+V | Inline text entry |

### 2.3 Fallback Path Matrix (Wayland paste simulation)

| ID | Method | Requirements | Priority |
|----|--------|-------------|----------|
| F1 | evdev UInput | evdev lib, /dev/uinput writable (udev rule), user in input group | 1 (preferred) |
| F2 | ydotool | ydotool installed, ydotoold running, /dev/uinput writable | 2 |
| F3 | wtype | wtype installed, wlroots compositor only | 3 |
| F4 | None available | All three unavailable | Should return False, log instructions |

### 2.4 Clipboard Tool Matrix

| ID | Session | Write Tool | Read Tool | Priority |
|----|---------|-----------|-----------|----------|
| C1 | Wayland | wl-copy | wl-paste | 1 (native) |
| C2 | Wayland | xclip (XWayland) | xclip (XWayland) | 2 (fallback) |
| C3 | Wayland | xsel (XWayland) | xsel (XWayland) | 3 (fallback) |
| C4 | X11 | xclip | xclip | 1 (native) |
| C5 | X11 | xsel | xsel | 2 (fallback) |

---

## 3. Test Scenarios

### 3.1 Happy Path Tests

| TC | Scenario | Steps | Expected | Env | App |
|----|----------|-------|----------|-----|-----|
| HP-01 | Basic paste into text editor (X11) | 1. Open gedit. 2. Press record hotkey, speak, press again. 3. Wait for processing. | Text appears at cursor within 3s (cloud) or 10s (local). | E1/E2 | A10 |
| HP-02 | Basic paste into text editor (Wayland+UInput) | Same as HP-01 but on Wayland. | Same result. Logs show "Keystroke simulated via evdev UInput". | E3 | A10 |
| HP-03 | Basic paste into browser (X11) | 1. Open Firefox, click a textarea. 2. Record + paste. | Text appears in textarea. | E1/E2 | A8 |
| HP-04 | Basic paste into browser (Wayland) | Same as HP-03 on Wayland. | Same result. | E3 | A9 |
| HP-05 | Paste into VS Code (Wayland) | 1. Open VS Code, open a file. 2. Record + paste. | Text inserted at cursor. | E3 | A11 |

### 3.2 Terminal Paste Tests (REGRESSION FOCUS)

| TC | Scenario | Steps | Expected | Env | App |
|----|----------|-------|----------|-----|-----|
| TP-01 | Paste into gnome-terminal (X11) | 1. Open gnome-terminal, focus it. 2. Record + paste. | Ctrl+Shift+V is sent. Text appears in terminal. | E1/E2 | A1 |
| TP-02 | Paste into gnome-terminal (Wayland) | Same on Wayland session. | **EXPECTED FAIL (BUG-1)**: Ctrl+V sent instead of Ctrl+Shift+V. Terminal may show "^V" or insert literal next char. | E3 | A1 |
| TP-03 | Paste into alacritty (X11) | Focus alacritty, record + paste. | Ctrl+Shift+V used, text appears. | E1/E2 | A4 |
| TP-04 | Paste into alacritty (Wayland) | Same on Wayland. | **EXPECTED FAIL (BUG-1)**. | E3 | A4 |
| TP-05 | Paste into kitty (Wayland) | Focus kitty, record + paste. | **EXPECTED FAIL (BUG-1)**. | E3 | A5 |
| TP-06 | Paste into foot (Wayland-native) | Focus foot terminal, record + paste. | **EXPECTED FAIL (BUG-1)**. foot has no X11 presence at all. | E3 | A6 |
| TP-07 | Paste into xterm (X11) | Focus xterm, record + paste. | Ctrl+Shift+V is sent but xterm does NOT support Ctrl+Shift+V for paste. Text may not appear. xterm uses Shift+Insert. | E1/E2 | A3 |
| TP-08 | Paste into konsole (Wayland) | Focus konsole on Plasma Wayland, record + paste. | **EXPECTED FAIL (BUG-1)**. | E4 | A2 |
| TP-09 | Auto-Enter in terminal (Wayland) | Set paste_auto_enter=True. Paste into gnome-terminal. | After paste, Enter is sent via send_key("enter"). Verify the command runs. | E3 | A1 |
| TP-10 | Paste into wezterm (Wayland) | Focus wezterm, record + paste. | **EXPECTED FAIL**: wezterm WM_CLASS is "org.wezfurlong.wezterm", NOT in terminal_classes set. Even on X11 this would fail. | E3 | A7 |

### 3.3 Clipboard Lifecycle Tests

| TC | Scenario | Steps | Expected | Env |
|----|----------|-------|----------|-----|
| CL-01 | Clipboard backup + restore (X11) | 1. Copy "original" to clipboard. 2. Record + paste. 3. After paste, check clipboard. | Clipboard contains "original" (restored). | E1/E2 |
| CL-02 | Clipboard backup + restore (Wayland) | Same on Wayland. | Same result. | E3 |
| CL-03 | Empty clipboard backup | 1. Clear clipboard. 2. Record + paste. 3. Check clipboard after. | No crash. Clipboard may contain the pasted text (no backup to restore). | E1-E3 |
| CL-04 | Large clipboard backup | 1. Copy >1MB of text. 2. Record + paste. | Backup skipped (SEC-070 size limit). Logs show warning. Pasted text remains on clipboard. | E1-E3 |
| CL-05 | Clipboard modified during processing | 1. Copy "original". 2. Start recording. 3. While processing, copy "modified". 4. Processing completes, pastes, restores. | Clipboard restored to "original" (backup was taken before processing started). | E1-E3 |
| CL-06 | Pipeline queueing clipboard preservation | 1. Copy "original". 2. Record + process. 3. During processing, start second recording. 4. Both pipelines complete. | After both pipelines, clipboard is "original". | E1-E3 |
| CL-07 | Clipboard restore timing | 1. Copy "long original text". 2. Paste short result into a slow application (e.g., LibreOffice). 3. Check clipboard. | Pasted text is the pipeline result, not the restored clipboard. 100ms + PASTE_DELAY_MS must be sufficient. | E1-E3 |

### 3.4 Edge Case Tests

| TC | Scenario | Steps | Expected | Env |
|----|----------|-------|----------|-----|
| EC-01 | Empty transcription | 1. Record silence or very short noise. | "No speech detected" notification. State returns to IDLE. No paste attempted. | E1-E3 |
| EC-02 | Unicode/umlauts | 1. Speak German text with umlauts. 2. Paste into gedit. | Characters preserved correctly. | E1-E3 |
| EC-03 | Multiline text | 1. Speak multiple sentences. 2. Paste. | Newlines preserved in the pasted text. | E1-E3 |
| EC-04 | Very long text (5+ min recording) | 1. Record for 5 minutes. 2. Wait for processing. | Text pastes correctly. Memory does not spike excessively. | E1-E3 |
| EC-05 | Whitespace-only result | 1. All filler words removed by summarizer. | "No speech detected" notification. No paste. | E1-E3 |
| EC-06 | Paste cancelled by user | 1. Set paste_require_confirmation=True. 2. Record + process. 3. Press Escape during confirmation. | "Paste cancelled" notification. Clipboard restored. State returns to IDLE. | E1-E3 |
| EC-07 | Paste confirmation timeout | 1. Set paste_require_confirmation=True, timeout=5s. 2. Record + process. 3. Wait without pressing Enter/Escape. | "Paste timed out" notification after 5s. Clipboard restored. | E1-E3 |
| EC-08 | No clipboard write tool installed | 1. Remove xclip, xsel, wl-clipboard. 2. Record + paste. | paste_text returns False. Error logged. Text not pasted. | E1-E3 |
| EC-09 | Clipboard tool timeout | 1. Mock wl-copy to hang. 2. Paste. | Timeout after 2s. paste_text returns False. | E3 |
| EC-10 | Rapid toggle (double hotkey) | 1. Press hotkey twice within 300ms. | Debounce prevents double trigger. No crash. | E1-E3 |

### 3.5 Fallback Path Tests (Wayland)

| TC | Scenario | Steps | Expected | Env |
|----|----------|-------|----------|-----|
| FB-01 | UInput available | 1. Ensure /dev/uinput writable. 2. Paste on Wayland. | UInput used. Logs: "Keystroke simulated via evdev UInput". | E3 |
| FB-02 | UInput unavailable, ydotool present | 1. Remove udev rule (or chmod 000 /dev/uinput). 2. Start ydotoold. 3. Paste on Wayland. | ydotool used. Logs: "Keystroke simulated via ydotool". | E3 |
| FB-03 | UInput+ydotool unavailable, wtype present | 1. Same as FB-02 but also stop ydotoold. 2. Install wtype. 3. Paste on Wayland (wlroots compositor). | wtype used. Logs: "Keystroke simulated via wtype". | E5 |
| FB-04 | All unavailable | 1. No /dev/uinput, no ydotool, no wtype. 2. Paste on Wayland. | paste_text returns False. Error logged with setup instructions. "Text was written to clipboard -- paste manually with Ctrl+V." | E3 |
| FB-05 | ydotool timeout | 1. ydotoold not running (ydotool hangs). | 2s timeout, falls through to wtype or False. | E3 |
| FB-06 | UInput send_key fails | 1. /dev/uinput writable but write() fails. | Falls through to ydotool. Logs warning. | E3 |

### 3.6 Clipboard Fallback Path Tests

| TC | Scenario | Steps | Expected | Env |
|----|----------|-------|----------|-----|
| CF-01 | Wayland + wl-clipboard installed | 1. Verify wl-copy, wl-paste present. 2. Backup + restore. | Native Wayland tools used. | E3 |
| CF-02 | Wayland + no wl-clipboard, xclip available | 1. Remove wl-clipboard. 2. Backup + restore. | xclip used via XWayland. Fallback warning logged. | E3 |
| CF-03 | Wayland + no wl-clipboard, no xclip, xsel available | 1. Remove wl-clipboard and xclip. 2. Backup + restore. | xsel used via XWayland. Fallback warning logged. | E3 |
| CF-04 | X11 + xclip installed | Standard X11 setup. | xclip used directly. | E1/E2 |
| CF-05 | X11 + no xclip, xsel available | Remove xclip. | xsel used. Fallback info logged. | E1/E2 |
| CF-06 | No clipboard tool at all | Remove all. | clipboard_backup returns None. paste_text returns False. | E1-E3 |
| CF-07 | Clipboard tool cache | 1. First call detects tool. 2. Second call uses cache. | Tool detection runs once per session type. No repeated fallback warnings. | E1-E3 |

---

## 4. Automated Test Coverage Gaps

The following scenarios are NOT covered by existing unit/integration tests and should be added:

### 4.1 Missing in test_linux_paste.py

| Gap | Description | Priority |
|-----|-------------|----------|
| **_is_terminal_focused() unit tests** | Zero tests for this function. Need to test: xdotool/xprop available, various WM_CLASS values (matching, non-matching), xdotool not found, subprocess failure, Wayland session (should return False). | HIGH |
| **paste_text() on X11 path** | No test for the X11 (non-Wayland) path of paste_text. Need: xdotool called with correct key, terminal detection on X11, xdotool not found. | HIGH |
| **paste_text() clipboard write failure** | No test for when clipboard write subprocess fails (returncode != 0, timeout). | HIGH |
| **paste_text() terminal detection + X11 paste key** | Test that X11 paste uses "ctrl+shift+v" when terminal is detected. | HIGH |
| **clipboard_backup() unit tests for Linux** | No Linux-specific clipboard_backup tests. Need: tool detection, subprocess output parsing, timeout, SEC-070 size check, empty clipboard, tool not found. | MEDIUM |
| **clipboard_restore() unit tests for Linux** | Same as above for restore. | MEDIUM |
| **_detect_session_type() tests** | No tests for XDG_SESSION_TYPE env var handling. | LOW |
| **Clipboard tool cache behavior** | No test verifying _clipboard_read_cache / _clipboard_write_cache work correctly. | LOW |

### 4.2 Missing in test_uinput_controller.py

| Gap | Description | Priority |
|-----|-------------|----------|
| **_inject_combo time.sleep calls** | No assertion that sleep is called between modifier down and main key (compositor needs time). | LOW |
| **Concurrent send_key calls** | No test for thread safety of send_key (lock contention). | LOW |
| **_ensure_device with evdev=None** | Test that ImportError is raised when evdev is not installed. | LOW |

### 4.3 Missing integration tests

| Gap | Description | Priority |
|-----|-------------|----------|
| **Full paste_text() -> clipboard_restore() lifecycle** | No integration test that runs the complete cycle: backup, paste, restore. All tests mock the subsystems independently. | MEDIUM |
| **Pipeline queueing + clipboard** | No test for queued pipeline clipboard preservation. | MEDIUM |

---

## 5. Proposed Fixes

### Fix for BUG-1: Terminal detection on Wayland

**Option A (Recommended): Make paste key configurable**
Add a `paste_shortcut` config option with values:
- `"auto"` (default): detect terminal if possible, otherwise use Ctrl+V
- `"ctrl+v"`: always use Ctrl+V (GUI apps)
- `"ctrl+shift+v"`: always use Ctrl+Shift+V (terminal users)
This gives the user control and avoids unreliable detection.

**Option B: Wayland terminal detection heuristics**
On Wayland, there is no standard way to query the focused app class. Possible approaches:
1. Check `$WAYLAND_DISPLAY` + parse `/proc/$(xdotool getactivewindow --pid)/comm` -- but xdotool does not work on Wayland.
2. Use D-Bus to query GNOME Shell for focused window via `org.gnome.Shell.Eval` -- fragile, GNOME-specific.
3. Use `kdotool` on KDE Plasma -- KDE-specific.
4. Always send Ctrl+Shift+V on Wayland (many GUI apps accept it too) -- may cause issues in some apps that interpret Shift differently.

**Option C: Always use Ctrl+Shift+V everywhere**
Most modern Linux GUI applications accept Ctrl+Shift+V as paste. Terminals require it. The main risk is that some apps (like LibreOffice) may interpret Ctrl+Shift+V differently (e.g., "paste special/unformatted").

**Recommendation**: Implement Option A with a sensible default. For the immediate fix, on Wayland sessions where terminal detection is impossible, default to Ctrl+Shift+V since:
1. All terminals require it.
2. Most GUI apps also accept it (or at least paste plain text, which is what we want per REQ-S18).

### Fix for BUG-2: Check clipboard write return code

```python
result = subprocess.run(cmd, input=text, text=True, timeout=2, capture_output=True)
if result.returncode != 0:
    logger.error(
        "Clipboard write failed (exit %d): %s",
        result.returncode,
        result.stderr.strip(),
    )
    return False
```

### Fix for BUG-3: Increase Wayland clipboard delay or add readback

Increase the delay from 150ms to 250ms on Wayland, or add a clipboard readback verification:
```python
# After writing, verify text is on clipboard
time.sleep(0.15)
verify = clipboard_backup()  # Read back
if verify != text[:100]:  # Spot check first 100 chars
    time.sleep(0.2)  # Additional wait
```

### Fix for BUG-10 (wezterm WM_CLASS): Add missing terminal entries

The `terminal_classes` set is missing several popular terminals:
- `org.wezfurlong.wezterm` (wezterm)
- `rio` (Rio terminal)
- `ghostty` (Ghostty)
- `tabby` (Tabby terminal)
- `cool-retro-term`

---

## 6. Test Execution Checklist

### Pre-test Setup
- [ ] Verify session type: `echo $XDG_SESSION_TYPE`
- [ ] Verify clipboard tools: `which xclip xsel wl-copy wl-paste`
- [ ] Verify paste tools: `which xdotool ydotool wtype`
- [ ] Verify /dev/uinput access: `ls -la /dev/uinput`, `groups | grep input`
- [ ] Check udev rule: `cat /etc/udev/rules.d/99-voicepaste-uinput.rules`
- [ ] Verify app starts without errors: check tray icon appears

### Execution

For each test case, record:
- [ ] TC ID
- [ ] Pass / Fail / Blocked / Skipped
- [ ] Environment (E1-E5)
- [ ] Application (A1-A13)
- [ ] Fallback path used (from logs)
- [ ] Actual behavior (if different from expected)
- [ ] Log excerpt (if failure)
- [ ] Timestamp

### Post-test
- [ ] Collect log files: `~/.local/share/VoicePaste/voice-paste.log`
- [ ] Note any orphan processes: `ps aux | grep -E 'wl-copy|xclip|ydotool'`
- [ ] Verify clean shutdown: no leftover hotkey hooks or UInput devices

---

## 7. Regression Test Subset (Quick Smoke)

For quick verification of a paste fix, run at minimum:

1. **TP-01**: Terminal paste on X11 (gnome-terminal)
2. **TP-02**: Terminal paste on Wayland (gnome-terminal) -- the reported regression
3. **HP-01**: GUI paste on X11 (gedit)
4. **HP-02**: GUI paste on Wayland (gedit)
5. **CL-01**: Clipboard backup/restore on X11
6. **CL-02**: Clipboard backup/restore on Wayland
7. **EC-02**: Unicode/umlaut preservation
8. **FB-01**: UInput fallback path confirmation

---

## 8. Automated Test Suite

Run automated tests:
```bash
# All paste-related tests
python3 -m pytest tests/test_linux_paste.py tests/test_uinput_controller.py -v

# Full suite (excludes Windows-only tests on Linux)
python3 -m pytest tests/ -v --tb=short

# With coverage for paste modules
python3 -m pytest tests/ --cov=src/platform_impl --cov=src/evdev_hotkey --cov-report=term-missing
```

---

## Appendix A: Terminal Paste Shortcut Reference

| Terminal | Ctrl+V Behavior | Ctrl+Shift+V Behavior | Correct Paste Key |
|----------|----------------|----------------------|-------------------|
| gnome-terminal | Verbatim insert (next char literal) | Paste from clipboard | Ctrl+Shift+V |
| konsole | Verbatim insert | Paste from clipboard | Ctrl+Shift+V |
| xterm | No special handling | No special handling | Shift+Insert |
| alacritty | Configurable (default: nothing) | Paste from clipboard | Ctrl+Shift+V |
| kitty | Nothing | Paste from clipboard | Ctrl+Shift+V |
| foot | Nothing | Paste from clipboard | Ctrl+Shift+V |
| wezterm | Nothing (or custom) | Paste from clipboard | Ctrl+Shift+V |
| tilix | Verbatim insert | Paste from clipboard | Ctrl+Shift+V |
| terminator | Verbatim insert | Paste from clipboard | Ctrl+Shift+V |

**Note**: xterm is a special case. It does not support Ctrl+Shift+V. The only reliable paste method for xterm is Shift+Insert or middle-click (X11 primary selection). VoicePaste cannot reliably paste into xterm without additional configuration.

## Appendix B: Wayland Focus Detection Alternatives

| Method | Works on | Pros | Cons |
|--------|----------|------|------|
| xdotool + xprop | X11 only | Reliable, well-known | Fails on Wayland |
| GNOME Shell D-Bus (`org.gnome.Shell.Eval`) | GNOME Wayland | Can query focused window | GNOME-only, requires eval permission |
| kdotool | KDE Plasma Wayland | Drop-in xdotool replacement | KDE-only |
| swaymsg | Sway | Can query focused window | Sway-only |
| wlrctl | wlroots compositors | Can query toplevel info | wlroots-only |
| /proc inspection | Any Linux | No compositor dependency | Cannot reliably determine focused PID |
| Configuration-based | Any | User controls behavior | Not automatic |

# Manual End-to-End Test Plan

## Voice-to-Summary Paste Tool v0.2.0

**Document Version**: 1.0
**Date**: 2026-02-13
**Author**: QA Engineer
**Target Platform**: Windows 10 (22H2+), Windows 11 (23H2+)
**Python Version**: 3.14.0

---

## Prerequisites

Before running this test plan, ensure the following are available:

- Windows 10 or 11 machine (non-admin user account is acceptable)
- Working microphone connected and set as default input device
- Valid OpenAI API key with access to `whisper-1` and `gpt-4o-mini` models
- Speakers or headphones for audio cue verification
- Internet connection for cloud API calls
- The following target applications installed:
  - Notepad (built-in)
  - Microsoft Word (or WordPad)
  - Google Chrome (latest stable)
  - Visual Studio Code (latest stable)
  - Microsoft Teams or Slack
- A second copy of the application binary (for single-instance testing)
- Process Explorer or Task Manager for resource monitoring

**Test Data**:
- German speech sample: "Hallo, das ist ein Test der Spracherkennung. Ich moechte sicherstellen, dass die Transkription korrekt funktioniert."
- English speech sample: "Hello, this is a test of the speech recognition system."
- German speech with fillers: "Also aehm, ich wollte halt sagen, dass aehm, das Projekt sozusagen fast fertig ist, ja genau."

---

## How to Use This Checklist

- Mark each test case as it is executed:
  - `[x]` -- PASS
  - `[F]` -- FAIL (add bug number or notes)
  - `[S]` -- SKIPPED (with reason)
  - `[N]` -- NOT APPLICABLE
- Record the tester name, date, and environment at the end of each section.
- Any FAIL result must have a corresponding bug report filed using the project bug template.

---

## Section 1: Setup Verification

### 1.1 First-Time Setup (from config.example.toml)

- [ ] **TC-1.1.1**: Copy `config.example.toml` to `config.toml` in the same directory as the application.
- [ ] **TC-1.1.2**: Open `config.toml` in a text editor and verify it is valid, human-readable TOML with comments explaining each option.
- [ ] **TC-1.1.3**: Set `openai_api_key` to a valid API key. Verify the key is accepted when the app starts (no "API key not configured" error in logs).
- [ ] **TC-1.1.4**: Verify `config.toml` contains sections: `[api]`, `[summarization]`, `[feedback]`, `[logging]`.
- [ ] **TC-1.1.5**: Verify default values: `summarization.enabled = true`, `feedback.audio_cues = true`, `logging.level = "INFO"`.

### 1.2 Application Startup

- [ ] **TC-1.2.1**: Launch the application (`python src/main.py` or the `.exe`). Verify no error dialogs, no crash, no console errors.
- [ ] **TC-1.2.2**: Verify a system tray icon appears in the Windows notification area. The icon should be a **grey circle**.
- [ ] **TC-1.2.3**: Hover over the tray icon. Verify the tooltip reads: **"Voice Paste - Ready (Ctrl+Win)"**.
- [ ] **TC-1.2.4**: Right-click the tray icon. Verify the context menu appears with:
  - A greyed-out status line showing **"Status: Idle"**
  - A separator
  - A **"Quit"** option
- [ ] **TC-1.2.5**: Verify the application does **not** appear in the Windows taskbar.
- [ ] **TC-1.2.6**: Verify the application does **not** open any main window.
- [ ] **TC-1.2.7**: Open `voice-paste.log` in the application directory. Verify it contains startup messages including:
  - Application name and version ("Voice Paste v0.2.0")
  - "Configuration loaded. API key: ****XXXX" (masked, showing only last 4 characters)
  - "Single-instance mutex acquired"
  - "Global hotkey registered: ctrl+windows"
  - "System tray icon started"

### 1.3 Log File Verification

- [ ] **TC-1.3.1**: Verify the log file is named `voice-paste.log` and located in the application root directory.
- [ ] **TC-1.3.2**: Verify each log line has the format: `YYYY-MM-DD HH:MM:SS [LEVEL] module: message`.
- [ ] **TC-1.3.3**: Set `logging.level = "DEBUG"` in `config.toml`, restart, and verify DEBUG-level messages appear in the log.
- [ ] **TC-1.3.4**: **SECURITY**: Verify the log file does **not** contain the full API key anywhere (search for the full key string). Only the masked version (last 4 chars) should appear. **(REQ-S01)**
- [ ] **TC-1.3.5**: After performing several transcriptions, verify the log file does **not** contain any transcript text or clipboard contents. **(REQ-S14, REQ-S24)**

---

## Section 2: Happy Path Tests

### 2.1 Basic Recording and Transcription (German)

- [ ] **TC-2.1.1**: Open Notepad and place the cursor in the text area.
- [ ] **TC-2.1.2**: Press **Ctrl+Win**. Verify:
  - A **rising two-tone beep** (440Hz then 880Hz) plays within 200ms.
  - The tray icon changes to a **red circle**.
  - The tooltip changes to **"Voice Paste - Recording..."**.
- [ ] **TC-2.1.3**: Speak the German test phrase clearly for 5-10 seconds.
- [ ] **TC-2.1.4**: Press **Ctrl+Win** again. Verify:
  - A **falling two-tone beep** (880Hz then 440Hz) plays within 200ms.
  - The tray icon changes to a **yellow circle**.
  - The tooltip changes to **"Voice Paste - Processing..."**.
- [ ] **TC-2.1.5**: Wait for processing to complete. Verify:
  - Text appears at the cursor position in Notepad.
  - The text is in German and accurately reflects what was spoken.
  - The tray icon returns to **grey**.
  - The tooltip returns to **"Voice Paste - Ready (Ctrl+Win)"**.
- [ ] **TC-2.1.6**: Verify total pipeline time from stop-recording to text-appearing is within **18 seconds** (for a 5-10 second recording with summarization).

### 2.2 Basic Recording and Transcription (English)

- [ ] **TC-2.2.1**: Repeat TC-2.1.1 through TC-2.1.5 but speak the English test phrase instead.
- [ ] **TC-2.2.2**: Verify the pasted text is in English and accurately reflects what was spoken.
- [ ] **TC-2.2.3**: Verify the summarizer preserves the language (English input produces English output).

### 2.3 Summarization Verification (German with Fillers)

- [ ] **TC-2.3.1**: Open Notepad and place the cursor.
- [ ] **TC-2.3.2**: Record the German filler-word test phrase: "Also aehm, ich wollte halt sagen, dass aehm, das Projekt sozusagen fast fertig ist, ja genau."
- [ ] **TC-2.3.3**: Verify the pasted summary:
  - Filler words ("also", "aehm", "halt", "sozusagen", "ja genau") are removed or reduced.
  - Grammar is corrected.
  - Core meaning is preserved (the project is almost finished).
  - Output is in German.
  - Output is shorter than the original spoken text.

### 2.4 Summarization Disabled

- [ ] **TC-2.4.1**: Set `summarization.enabled = false` in `config.toml` and restart the app.
- [ ] **TC-2.4.2**: Record a German phrase with filler words.
- [ ] **TC-2.4.3**: Verify the pasted text is the **raw transcript** (filler words are present, no cleanup applied).
- [ ] **TC-2.4.4**: Verify the log shows "Summarization disabled (PassthroughSummarizer)."
- [ ] **TC-2.4.5**: Restore `summarization.enabled = true` and restart.

### 2.5 Paste Target Application Compatibility

For each target application below, perform a full record-and-paste cycle (press Ctrl+Win, speak 3-5 seconds of German, press Ctrl+Win, wait for paste):

- [ ] **TC-2.5.1**: **Notepad** -- Text appears correctly at cursor position.
- [ ] **TC-2.5.2**: **Microsoft Word** (or WordPad) -- Text appears correctly. Verify it is pasted as **plain text** (no unexpected formatting, no embedded objects).
- [ ] **TC-2.5.3**: **Google Chrome -- address bar** -- Text appears in the address bar.
- [ ] **TC-2.5.4**: **Google Chrome -- web form textarea** -- Open any page with a textarea (e.g., Google search box). Verify text appears in the field.
- [ ] **TC-2.5.5**: **Google Chrome -- contenteditable div** -- Open a page with a contenteditable element (e.g., Gmail compose). Verify text appears.
- [ ] **TC-2.5.6**: **Visual Studio Code** -- Open a file and place cursor. Verify text is pasted at cursor position.
- [ ] **TC-2.5.7**: **Microsoft Teams or Slack** -- Open a chat input. Verify text appears in the message field. (Note: Teams may require Enter to send -- verify text is placed but not auto-sent.)

### 2.6 Clipboard Preservation

- [ ] **TC-2.6.1**: Copy the text "ORIGINAL_CLIPBOARD_CONTENT" to the clipboard using Ctrl+C in any application.
- [ ] **TC-2.6.2**: Perform a full record-and-paste cycle.
- [ ] **TC-2.6.3**: After the transcript is pasted, wait 1 second, then press Ctrl+V in Notepad.
- [ ] **TC-2.6.4**: Verify that **"ORIGINAL_CLIPBOARD_CONTENT"** is pasted (not the transcript). This confirms the clipboard was restored. **(US-0.2.5)**
- [ ] **TC-2.6.5**: Repeat with an empty clipboard (clear clipboard before recording). Verify the tool does not crash and the transcript is still pasted correctly.
- [ ] **TC-2.6.6**: Copy an image to the clipboard (e.g., screenshot with Print Screen). Perform a record-and-paste cycle. Verify the transcript pastes correctly. Verify the image is **not** restored (this is an accepted limitation per UX-SPEC 4.7 -- only text backup is supported). Check the log for a relevant debug message.

---

## Section 3: Audio Cue Tests

### 3.1 Recording Start Cue

- [ ] **TC-3.1.1**: With `feedback.audio_cues = true` in config, press Ctrl+Win. Verify a **short rising two-tone beep** plays.
- [ ] **TC-3.1.2**: Verify the beep consists of two tones: lower pitch (440Hz) then higher pitch (880Hz), each approximately 75ms.
- [ ] **TC-3.1.3**: Verify the cue plays within **200ms** of the hotkey press.

### 3.2 Recording Stop Cue

- [ ] **TC-3.2.1**: While recording, press Ctrl+Win to stop. Verify a **short falling two-tone beep** plays.
- [ ] **TC-3.2.2**: Verify the beep consists of two tones: higher pitch (880Hz) then lower pitch (440Hz), each approximately 75ms.
- [ ] **TC-3.2.3**: Verify the cue is audibly distinct from the start cue (descending vs ascending).

### 3.3 Cancel Cue

- [ ] **TC-3.3.1**: Start a recording with Ctrl+Win, then press Escape to cancel.
- [ ] **TC-3.3.2**: Verify **two short low beeps** play (330Hz each, approximately 75ms per beep, with a 50ms gap).
- [ ] **TC-3.3.3**: Verify the cancel cue is audibly distinct from both start and stop cues.

### 3.4 Error Cue

- [ ] **TC-3.4.1**: Configure an invalid API key in `config.toml` and restart the app. Record and stop. Verify a **single low buzz** plays (220Hz, approximately 300ms).
- [ ] **TC-3.4.2**: Verify the error cue is clearly distinct from start, stop, and cancel cues.
- [ ] **TC-3.4.3**: Restore the valid API key.

### 3.5 Audio Cues Disabled

- [ ] **TC-3.5.1**: Set `feedback.audio_cues = false` in `config.toml` and restart.
- [ ] **TC-3.5.2**: Perform a full record-and-paste cycle. Verify **no audio cues** play at any point (no start beep, no stop beep).
- [ ] **TC-3.5.3**: Cancel a recording with Escape. Verify **no cancel beep** plays.
- [ ] **TC-3.5.4**: Trigger an error (e.g., disconnect internet during processing). Verify **no error buzz** plays.
- [ ] **TC-3.5.5**: Restore `feedback.audio_cues = true`.

---

## Section 4: Edge Case Tests

### 4.1 Rapid Double-Press (Debounce)

- [ ] **TC-4.1.1**: Press Ctrl+Win twice rapidly (within 300ms). Verify only the **first** press is registered (recording starts). The second press is debounced and ignored.
- [ ] **TC-4.1.2**: Verify no crash, no error notification, no invalid state.
- [ ] **TC-4.1.3**: After the debounce window (wait >300ms), press Ctrl+Win again. Verify it correctly stops recording and triggers processing.
- [ ] **TC-4.1.4**: Check the log for the debounce message: "Hotkey debounced (Xms < 300ms)."

### 4.2 Cancel Recording with Escape

- [ ] **TC-4.2.1**: Press Ctrl+Win to start recording. While recording, press **Escape**.
- [ ] **TC-4.2.2**: Verify:
  - The cancel audio cue plays (two low beeps, if audio cues enabled).
  - The tray icon returns to **grey** (idle).
  - No transcription occurs.
  - No text is pasted anywhere.
  - The tooltip returns to "Voice Paste - Ready (Ctrl+Win)".
- [ ] **TC-4.2.3**: Verify the log shows: "Recording cancelled by user."
- [ ] **TC-4.2.4**: After cancellation, press Ctrl+Win again. Verify a new recording starts normally.
- [ ] **TC-4.2.5**: Press Escape while in **IDLE** state (not recording). Verify nothing happens -- no error, no crash, no state change. Check log for "Cancel pressed outside RECORDING state, ignored."

### 4.3 Very Short Recording (<0.5 seconds)

- [ ] **TC-4.3.1**: Press Ctrl+Win, then immediately press Ctrl+Win again (within 0.5 seconds, but after the 300ms debounce window).
- [ ] **TC-4.3.2**: Verify the app shows a **toast notification**: "No speech detected."
- [ ] **TC-4.3.3**: Verify no text is pasted.
- [ ] **TC-4.3.4**: Verify the app returns to idle state (grey icon).
- [ ] **TC-4.3.5**: Check the log for: "Recording too short (X.Xs < 0.5s). Discarding."

### 4.4 Silent Recording (No Speech)

- [ ] **TC-4.4.1**: Press Ctrl+Win. Remain silent for 5 seconds. Press Ctrl+Win to stop.
- [ ] **TC-4.4.2**: Observe the result:
  - If Whisper returns an empty string, verify the toast notification "No speech detected" appears and no text is pasted.
  - If Whisper returns background noise text (this can happen), verify the summarizer handles it gracefully and the result is pasted.
- [ ] **TC-4.4.3**: Verify the app returns to idle state in either case.

### 4.5 Hotkey During Processing

- [ ] **TC-4.5.1**: Perform a recording (5-10 seconds of speech). After pressing Ctrl+Win to stop, while the tray icon is **yellow** (processing), press Ctrl+Win again.
- [ ] **TC-4.5.2**: Verify the hotkey press is **ignored**. No new recording starts.
- [ ] **TC-4.5.3**: Verify no crash, no error notification.
- [ ] **TC-4.5.4**: Check the log for: "Hotkey pressed during processing, ignored."
- [ ] **TC-4.5.5**: Verify processing completes normally and text is pasted.

### 4.6 Application Focus Change During Processing

- [ ] **TC-4.6.1**: Open Notepad and VS Code side by side.
- [ ] **TC-4.6.2**: Click into Notepad. Press Ctrl+Win, speak, press Ctrl+Win to stop.
- [ ] **TC-4.6.3**: While the tray icon shows **yellow** (processing), click into VS Code to give it focus.
- [ ] **TC-4.6.4**: Verify the transcript is pasted into **VS Code** (the currently focused application at paste time), **not** Notepad.
- [ ] **TC-4.6.5**: This is correct behavior per UX-SPEC 4.6 -- the paste targets whatever app has focus at paste time.

### 4.7 Escape During Processing

- [ ] **TC-4.7.1**: Start a recording and stop it with Ctrl+Win. While in **PROCESSING** state, press Escape.
- [ ] **TC-4.7.2**: Verify Escape is **ignored** (cancel is only active during RECORDING). No crash, no error.
- [ ] **TC-4.7.3**: Verify processing completes normally.

### 4.8 Maximum Recording Duration (Auto-Stop)

- [ ] **TC-4.8.1**: Press Ctrl+Win to start recording. Let it run without stopping for **5 minutes**.
- [ ] **TC-4.8.2**: After exactly 5 minutes (300 seconds), verify:
  - The recording **auto-stops**.
  - A toast notification appears: "Recording auto-stopped after 5 minutes."
  - The tray icon transitions to **yellow** (processing).
  - Processing begins automatically.
- [ ] **TC-4.8.3**: Verify the transcript of the 5-minute recording is pasted successfully.
- [ ] **TC-4.8.4**: Check the log for: "Max recording duration reached (300 seconds). Auto-stopping."

### 4.9 Right-Click Menu During Recording

- [ ] **TC-4.9.1**: Start a recording with Ctrl+Win.
- [ ] **TC-4.9.2**: Right-click the tray icon. Verify the context menu shows **"Status: Recording"** (greyed out).
- [ ] **TC-4.9.3**: Verify the recording continues while the context menu is open.
- [ ] **TC-4.9.4**: Close the menu (click away) and stop the recording normally.

---

## Section 5: Error Handling Tests

### 5.1 Invalid API Key

- [ ] **TC-5.1.1**: Set `openai_api_key` to `"sk-invalid-key-12345"` in `config.toml`. Restart the app.
- [ ] **TC-5.1.2**: Perform a recording and stop it.
- [ ] **TC-5.1.3**: Verify:
  - A **toast notification** appears with a message about API authentication or invalid key.
  - The **error audio cue** plays (low buzz, if cues enabled).
  - The tray icon returns to **grey** (idle).
  - No text is pasted.
- [ ] **TC-5.1.4**: Check the log for an error related to 401 authentication failure.
- [ ] **TC-5.1.5**: Restore the valid API key and restart.

### 5.2 Empty API Key

- [ ] **TC-5.2.1**: Set `openai_api_key = ""` in `config.toml`. Launch the app.
- [ ] **TC-5.2.2**: Verify the app **exits gracefully** with a log message: "OpenAI API key not configured in config.toml."
- [ ] **TC-5.2.3**: Verify no tray icon appears (app exits before tray setup).

### 5.3 No Internet Connection

- [ ] **TC-5.3.1**: Disconnect from the network (disable Wi-Fi/Ethernet).
- [ ] **TC-5.3.2**: Perform a recording and stop it.
- [ ] **TC-5.3.3**: Verify:
  - A **toast notification** appears with a network error message.
  - The **error audio cue** plays (if cues enabled).
  - The app returns to idle state.
  - Clipboard contents are restored if they were backed up.
- [ ] **TC-5.3.4**: Reconnect to the network. Perform another recording. Verify it succeeds.
- [ ] **TC-5.3.5**: Check the log for connection error details.

### 5.4 No Microphone

- [ ] **TC-5.4.1**: Disconnect or disable all microphones (Device Manager or Settings > Sound).
- [ ] **TC-5.4.2**: Launch the app (or keep it running -- microphone is checked at recording start, not at launch per UX-SPEC 4.1).
- [ ] **TC-5.4.3**: Press Ctrl+Win to attempt recording.
- [ ] **TC-5.4.4**: Verify:
  - A **toast notification** appears: "No microphone detected. Check your audio settings."
  - The **error audio cue** plays (if cues enabled).
  - The tray icon stays **grey** (idle) -- no transition to recording.
  - No crash.
- [ ] **TC-5.4.5**: Reconnect the microphone. Press Ctrl+Win. Verify recording starts normally without restarting the app.

### 5.5 Microphone Removed During Recording

- [ ] **TC-5.5.1**: Start a recording with Ctrl+Win.
- [ ] **TC-5.5.2**: While recording, physically disconnect the USB microphone (or disable it in Device Manager).
- [ ] **TC-5.5.3**: Verify the app handles this gracefully:
  - Either: recording stops with an error notification, returning to idle.
  - Or: recording continues capturing silence from a fallback device and can be stopped normally.
- [ ] **TC-5.5.4**: Verify no crash or unhandled exception.
- [ ] **TC-5.5.5**: Reconnect the microphone and verify the app functions normally for subsequent recordings.

### 5.6 API Timeout

- [ ] **TC-5.6.1**: Record a very long phrase (30+ seconds of continuous speech).
- [ ] **TC-5.6.2**: If the API takes longer than 30 seconds (the configured timeout), verify:
  - A toast notification appears indicating a timeout or API error.
  - The app returns to idle state.
  - Clipboard is restored.
- [ ] **TC-5.6.3**: (Alternative) If the API succeeds within 30 seconds, note the response time and verify it meets the performance target.

### 5.7 Config File Errors

- [ ] **TC-5.7.1**: **Missing config.toml**: Delete `config.toml`. Launch the app.
  - Verify a **template** `config.toml` is created in the application directory.
  - Verify the app **exits** with a log message: "Created config.toml template... Please add your OpenAI API key and restart."
  - Verify the created template is valid TOML with empty API key.
- [ ] **TC-5.7.2**: **Malformed TOML**: Replace `config.toml` contents with `[api\ninvalid syntax!!!`. Launch the app.
  - Verify the app exits with a log message containing "config.toml has invalid syntax".
- [ ] **TC-5.7.3**: **Missing [api] section**: Remove the `[api]` section entirely from `config.toml`. Launch the app.
  - Verify the app exits with a log message about the missing API key.
- [ ] **TC-5.7.4**: **Extra unknown keys**: Add `[unknown_section]\nfoo = "bar"` to `config.toml`. Launch the app.
  - Verify the app starts normally (unknown keys are silently ignored).
- [ ] **TC-5.7.5**: Restore the correct `config.toml` after these tests.

---

## Section 6: System Integration Tests

### 6.1 Single Instance Enforcement (REQ-S27)

- [ ] **TC-6.1.1**: Launch the application normally. Verify the tray icon appears.
- [ ] **TC-6.1.2**: In a separate terminal, attempt to launch a **second instance** of the application.
- [ ] **TC-6.1.3**: Verify the second instance:
  - **Exits immediately** (process terminates with exit code 1).
  - Logs: "Another instance of Voice Paste is already running (mutex ... exists)."
  - Does **not** create a second tray icon.
- [ ] **TC-6.1.4**: Verify the **first instance** continues to function normally (record, transcribe, paste).
- [ ] **TC-6.1.5**: Quit the first instance via the tray menu. Launch a new instance. Verify it starts normally (mutex was released).

### 6.2 System Tray Menu Functionality

- [ ] **TC-6.2.1**: Right-click the tray icon. Verify the menu appears near the icon (not at screen corner or off-screen).
- [ ] **TC-6.2.2**: Verify the **"Status: Idle"** menu item is displayed and **greyed out** (non-clickable).
- [ ] **TC-6.2.3**: Start a recording. Right-click the tray icon. Verify **"Status: Recording"** is displayed.
- [ ] **TC-6.2.4**: Click **"Quit"** in the tray menu. Verify:
  - If recording was active, it is stopped.
  - The tray icon disappears.
  - The application process terminates cleanly (no orphan process in Task Manager).
  - The log shows "Shutting down Voice Paste..." and "shutdown complete."
  - The single-instance mutex is released (verify by launching a new instance).
- [ ] **TC-6.2.5**: Verify no hotkey hooks remain after quit (press Ctrl+Win -- nothing should happen, the Windows Start menu may appear instead).

### 6.3 Log File Rotation (REQ-S26)

- [ ] **TC-6.3.1**: Verify the log file `voice-paste.log` is being written to during normal operation.
- [ ] **TC-6.3.2**: Artificially inflate the log file to just under 5 MB. Perform operations that generate log output.
- [ ] **TC-6.3.3**: Verify that when `voice-paste.log` exceeds **5 MB**, a rotation occurs:
  - `voice-paste.log` is renamed to `voice-paste.log.1`.
  - A new empty `voice-paste.log` is created for fresh logging.
- [ ] **TC-6.3.4**: Verify a maximum of **3 backup files** are kept (`voice-paste.log.1`, `.2`, `.3`). Older backups are deleted.
- [ ] **TC-6.3.5**: Verify the application continues to log normally after rotation.

### 6.4 Clean Shutdown

- [ ] **TC-6.4.1**: Start the app. Perform a recording. Use "Quit" from the tray menu.
- [ ] **TC-6.4.2**: Open Task Manager. Verify no `python.exe` or `voice_paste.exe` process remains.
- [ ] **TC-6.4.3**: Press Ctrl+Win. Verify nothing happens (no global hotkey hook remains).
- [ ] **TC-6.4.4**: Start the app again. Verify it starts cleanly (mutex was properly released).

### 6.5 Startup with No Speaker/Audio Output

- [ ] **TC-6.5.1**: Disable all audio output devices in Windows Sound settings.
- [ ] **TC-6.5.2**: Start the app. Verify it starts normally (audio cues fail silently, app does not crash).
- [ ] **TC-6.5.3**: Perform a record-and-paste cycle. Verify transcription and pasting still work (only audio cues are affected).
- [ ] **TC-6.5.4**: Re-enable audio output.

---

## Section 7: Security Verification Tests

### 7.1 API Key Protection

- [ ] **TC-7.1.1**: **(REQ-S01)** Set log level to DEBUG. Perform several recordings. Search the entire log file for the full API key string. Verify it is **never present**. Only the masked version (e.g., `****abcd`) should appear.
- [ ] **TC-7.1.2**: **(REQ-S02)** Open all source files in `src/`. Search for the literal API key or any hardcoded key string. Verify none are found.
- [ ] **TC-7.1.3**: **(REQ-S03)** Verify `.gitignore` contains `config.toml` (prevents accidental commit).
- [ ] **TC-7.1.4**: **(REQ-S05)** Check file permissions on `config.toml`. Note whether they restrict access to the current user (not required, but recommended).

### 7.2 Audio Data Security

- [ ] **TC-7.2.1**: **(REQ-S09)** Perform a recording. Use Process Monitor (Sysinternals) or similar tool to monitor file system writes during the recording and processing. Verify **no audio data is written to disk** (no `.wav`, `.tmp`, or other audio files created).
- [ ] **TC-7.2.2**: **(REQ-S11)** Set log level to DEBUG. Perform a recording. Search the log file for any raw audio bytes, base64-encoded data, or WAV file paths. Verify none are present.
- [ ] **TC-7.2.3**: **(REQ-S10)** After cancelling a recording with Escape, verify (via code review or memory profiling) that audio buffers are cleared (filled with zeros and released).

### 7.3 Network Security

- [ ] **TC-7.3.1**: **(REQ-S06)** Using a network monitoring tool (Wireshark, Fiddler), capture traffic during a transcription. Verify all API calls go to `https://api.openai.com` (HTTPS, never HTTP).
- [ ] **TC-7.3.2**: **(REQ-S07)** Verify TLS certificate validation is enabled (the `openai` Python client should do this by default; confirm no `verify=False` in the codebase).

### 7.4 Clipboard Security

- [ ] **TC-7.4.1**: **(REQ-S12)** Measure the time between the transcript being written to clipboard and the original clipboard being restored. Verify this window is **<500ms**.
- [ ] **TC-7.4.2**: **(REQ-S13)** Simulate an error during paste (e.g., lock the clipboard from another app). Verify the original clipboard contents are still restored (the `finally` block runs).
- [ ] **TC-7.4.3**: **(REQ-S18)** Perform a paste into a rich text editor (e.g., Word). Verify the pasted content is **plain text only** (CF_UNICODETEXT) -- no rich formatting, no HTML.

### 7.5 Hotkey Security

- [ ] **TC-7.5.1**: **(REQ-S15)** Review the code in `hotkey.py`. Verify only `ctrl+windows` and `escape` are registered via `keyboard.add_hotkey()`. No blanket keyboard hooks or key listeners are used.

---

## Section 8: Performance Tests

### 8.1 Recording Start Latency

- [ ] **TC-8.1.1**: Press Ctrl+Win and measure the time until the recording start audio cue plays. Verify it is **<500ms** (target from US-0.1.2).
- [ ] **TC-8.1.2**: Repeat 5 times and record the average latency.
  - Run 1: ___ ms
  - Run 2: ___ ms
  - Run 3: ___ ms
  - Run 4: ___ ms
  - Run 5: ___ ms
  - Average: ___ ms

### 8.2 Tray Icon State Change Latency

- [ ] **TC-8.2.1**: Observe the tray icon when pressing Ctrl+Win to start recording. Verify the icon changes from grey to red within **200ms**.
- [ ] **TC-8.2.2**: Observe the tray icon when pressing Ctrl+Win to stop recording. Verify the icon changes from red to yellow within **200ms**.

### 8.3 End-to-End Pipeline Latency

Record the total time from pressing Ctrl+Win (stop) to text appearing at the cursor:

- [ ] **TC-8.3.1**: **5-second recording, summarization enabled**: ___ seconds (target: <18s)
- [ ] **TC-8.3.2**: **10-second recording, summarization enabled**: ___ seconds (target: <18s)
- [ ] **TC-8.3.3**: **30-second recording, summarization enabled**: ___ seconds (target: <18s)
- [ ] **TC-8.3.4**: **5-second recording, summarization disabled**: ___ seconds (target: <15s)
- [ ] **TC-8.3.5**: **30-second recording, summarization disabled**: ___ seconds (target: <15s)

### 8.4 Memory Usage

Use Task Manager (Details tab, add "Memory (Private Working Set)" column) or Resource Monitor:

- [ ] **TC-8.4.1**: **Idle baseline**: Record memory usage after startup with the app idle for 30 seconds. ___ MB
- [ ] **TC-8.4.2**: **During recording**: Record memory usage while recording for 30 seconds. ___ MB. Verify it stays within reasonable bounds (baseline + <50 MB for 30s of 16kHz mono audio).
- [ ] **TC-8.4.3**: **After recording**: Record memory usage 10 seconds after processing completes. ___ MB. Verify it returns close to the idle baseline (audio buffers released).
- [ ] **TC-8.4.4**: **5-minute recording**: Record memory usage at 1-minute intervals during a 5-minute continuous recording session.
  - 1 min: ___ MB
  - 2 min: ___ MB
  - 3 min: ___ MB
  - 4 min: ___ MB
  - 5 min: ___ MB
  - After processing: ___ MB
  - Verify no excessive memory growth (should remain under 200 MB total for a 5-minute 16kHz mono recording).
- [ ] **TC-8.4.5**: **Memory leak check**: Perform 10 consecutive record-paste cycles. Measure memory after each cycle. Verify no upward trend indicating a memory leak.
  - After cycle 1: ___ MB
  - After cycle 5: ___ MB
  - After cycle 10: ___ MB

### 8.5 Clipboard Exposure Window

- [ ] **TC-8.5.1**: Using a clipboard monitoring tool (or a script that polls the clipboard every 50ms), measure how long the transcript remains on the clipboard before the original content is restored. **(REQ-S12)**
- [ ] **TC-8.5.2**: Verify the exposure window is **<500ms**.

---

## Section 9: PyInstaller .exe Build Tests

These tests are only applicable after creating a `.exe` build with PyInstaller.

### 9.1 Basic .exe Launch

- [ ] **TC-9.1.1**: On a machine **without Python installed**, double-click the `.exe` file. Verify it launches without errors.
- [ ] **TC-9.1.2**: Verify the system tray icon appears (grey circle, "Voice Paste - Ready (Ctrl+Win)").
- [ ] **TC-9.1.3**: Verify the log file is created in the same directory as the `.exe`.
- [ ] **TC-9.1.4**: Verify no "missing DLL" or "module not found" errors in the console or log.

### 9.2 .exe Configuration

- [ ] **TC-9.2.1**: Place `config.toml` in the **same directory** as the `.exe`. Verify the app reads it correctly.
- [ ] **TC-9.2.2**: Delete `config.toml` and launch the `.exe`. Verify a template `config.toml` is created next to the `.exe` (not in a temp directory or `_internal` folder).
- [ ] **TC-9.2.3**: Verify the created template has the same content as `config.example.toml`.

### 9.3 .exe Happy Path

- [ ] **TC-9.3.1**: Perform a full happy-path test (TC-2.1.1 through TC-2.1.6) using the `.exe` build.
- [ ] **TC-9.3.2**: Verify identical behavior to the Python script version.
- [ ] **TC-9.3.3**: Verify audio cues play correctly from the `.exe`.
- [ ] **TC-9.3.4**: Verify toast notifications appear correctly from the `.exe`.

### 9.4 .exe Single Instance

- [ ] **TC-9.4.1**: Launch the `.exe`. Attempt to launch a second copy. Verify the second instance exits immediately with the mutex error.
- [ ] **TC-9.4.2**: Verify no second tray icon appears.

### 9.5 .exe Shutdown

- [ ] **TC-9.5.1**: Use "Quit" from the tray menu. Verify the `.exe` process terminates cleanly (check Task Manager).
- [ ] **TC-9.5.2**: Verify no orphan sub-processes remain.

### 9.6 .exe Antivirus Interaction

- [ ] **TC-9.6.1**: Note whether Windows Defender (or installed AV) flags the `.exe`. Record any alerts.
- [ ] **TC-9.6.2**: If flagged, verify the app still functions after allowing/whitelisting.
- [ ] **TC-9.6.3**: Document the false positive for inclusion in README (REQ-S16).

---

## Section 10: Multi-Environment Compatibility

### 10.1 Windows 10 (if available)

- [ ] **TC-10.1.1**: Repeat TC-2.1 (happy path) on a Windows 10 machine.
- [ ] **TC-10.1.2**: Verify tray icon appears correctly in the Windows 10 system tray.
- [ ] **TC-10.1.3**: Verify toast notifications display correctly on Windows 10.
- [ ] **TC-10.1.4**: Verify Ctrl+Win hotkey does not conflict with Windows 10 shortcuts.

### 10.2 Windows 11

- [ ] **TC-10.2.1**: Repeat TC-2.1 (happy path) on a Windows 11 machine.
- [ ] **TC-10.2.2**: Verify tray icon appears correctly in the Windows 11 system tray (may require "Show hidden icons" expansion).
- [ ] **TC-10.2.3**: Verify toast notifications display correctly on Windows 11.
- [ ] **TC-10.2.4**: Verify Ctrl+Win hotkey does not conflict with Windows 11 shortcuts (note: Windows 11 may use Win key combos differently).

### 10.3 Non-Admin User

- [ ] **TC-10.3.1**: Run the application from a **standard (non-administrator)** user account.
- [ ] **TC-10.3.2**: Verify the global hotkey registers successfully.
- [ ] **TC-10.3.3**: Verify the full happy path works without elevation.

### 10.4 Multi-Monitor Setup

- [ ] **TC-10.4.1**: With multiple monitors connected, verify the tray icon appears on the **primary monitor** taskbar.
- [ ] **TC-10.4.2**: Move the active application to a secondary monitor. Perform a record-and-paste cycle. Verify text is pasted correctly in the application on the secondary monitor.
- [ ] **TC-10.4.3**: Verify toast notifications appear and are readable (not off-screen or cut off).

### 10.5 Sleep/Wake Cycle

- [ ] **TC-10.5.1**: Start the app. Verify it is idle and functional.
- [ ] **TC-10.5.2**: Put the machine to sleep (Start > Power > Sleep).
- [ ] **TC-10.5.3**: Wake the machine.
- [ ] **TC-10.5.4**: Press Ctrl+Win. Verify the hotkey still works (recording starts).
- [ ] **TC-10.5.5**: Complete a full record-and-paste cycle. Verify it works normally.
- [ ] **TC-10.5.6**: If the hotkey does not work after wake, document this as a known issue.

---

## Test Execution Record

| Section | Test Count | Pass | Fail | Skip | N/A | Tester | Date |
|---------|-----------|------|------|------|-----|--------|------|
| 1. Setup Verification | 17 | | | | | | |
| 2. Happy Path | 25 | | | | | | |
| 3. Audio Cues | 14 | | | | | | |
| 4. Edge Cases | 24 | | | | | | |
| 5. Error Handling | 21 | | | | | | |
| 6. System Integration | 16 | | | | | | |
| 7. Security | 12 | | | | | | |
| 8. Performance | 14 | | | | | | |
| 9. .exe Build | 14 | | | | | | |
| 10. Multi-Environment | 14 | | | | | | |
| **TOTAL** | **171** | | | | | | |

---

## Quality Gate Checklist

Before signing off on a release, all of the following must be verified:

- [ ] **QG-01**: All automated unit tests pass (0 failures, 0 errors).
- [ ] **QG-02**: All automated integration tests pass.
- [ ] **QG-03**: E2E happy path works on Windows 10.
- [ ] **QG-04**: E2E happy path works on Windows 11.
- [ ] **QG-05**: Paste verified in at least 5 target applications (Notepad, Word, Chrome, VS Code, Teams/Slack).
- [ ] **QG-06**: No open Critical or High severity bugs.
- [ ] **QG-07**: Pipeline latency <18s (cloud+summarization) for 30-second recording.
- [ ] **QG-08**: Memory stays under 200 MB during 5-minute continuous recording.
- [ ] **QG-09**: PyInstaller `.exe` passes happy path and single-instance tests.
- [ ] **QG-10**: Config error handling is graceful (missing file, malformed file, missing keys).
- [ ] **QG-11**: Application starts and shuts down cleanly -- no orphan processes, no leftover hotkey hooks.
- [ ] **QG-12**: Security checklist complete -- no secrets in logs, HTTPS only, audio never on disk.
- [ ] **QG-13**: Clipboard restored in all code paths (happy path, error, cancel).
- [ ] **QG-14**: Single-instance enforcement works (second instance exits).
- [ ] **QG-15**: Log rotation configured and verified (5 MB max, 3 backups).

---

## Appendix A: Bug Report Template

```
## Bug: [Concise Title]

**Severity**: Critical / High / Medium / Low

**Steps to Reproduce**:
1. [Precise step 1]
2. [Precise step 2]
3. [Continue as needed]

**Expected Behavior**: [What should happen]

**Actual Behavior**: [What actually happens]

**Environment**:
- Windows version: [10/11, build number]
- Python version: [e.g., 3.14.0]
- App version: [e.g., 0.2.0]
- Mode: [cloud / local]
- Running as: [.exe / python script]
- Audio device: [device name or "no device"]

**Logs**:
[Relevant log output, trimmed to essential lines]

**Notes**: [Additional context, potential root cause hypothesis]
```

---

## Appendix B: Environment Setup Notes

### Required Python Packages
Refer to `requirements.txt` in the project root for the full dependency list. Key packages: `openai`, `sounddevice`, `numpy`, `keyboard`, `pystray`, `Pillow`.

### Audio Device Verification
Before testing, verify the default input device in Windows Settings > System > Sound > Input. Use the Windows "Sound Recorder" app to confirm the microphone captures audio.

### Network Verification
Verify connectivity to `https://api.openai.com` by running:
```
curl -s -o /dev/null -w "%{http_code}" https://api.openai.com/v1/models -H "Authorization: Bearer YOUR_KEY"
```
Expected response: `200`.

### API Key Quota
Ensure the OpenAI API key has sufficient quota for test execution. A full test run may consume approximately 50-100 API calls (Whisper transcriptions + GPT-4o-mini summarizations).

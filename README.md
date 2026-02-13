# Voice-to-Summary Paste Tool

A Windows desktop utility that records your speech, transcribes it with cloud AI, optionally summarizes it, and pastes the result at your cursor—all with a single hotkey.

## Features

- **One-hotkey workflow**: Press Ctrl+Shift+V to record, press again to transcribe and paste.
- **Cloud transcription**: OpenAI Whisper API for accurate speech-to-text.
- **Automatic summarization**: GPT-4o-mini cleans up filler words and grammar (v0.2+).
- **Silent operation**: Runs entirely in the system tray. Never steals focus.
- **Audio feedback**: Beeps confirm recording start/stop. Disable in config if you prefer silence.
- **Clipboard preservation**: Original clipboard contents are restored after pasting (v0.2+).
- **Cancel anytime**: Press Escape during recording to discard and return to idle.
- **Toast notifications**: Errors appear as Windows notifications, not modal dialogs (v0.2+).

## Requirements

- **Windows 10 or 11**
- **Python 3.11+** (for running from source)
- **Microphone**: Connected and working
- **OpenAI API key**: Required for cloud transcription. Get one at https://platform.openai.com/api-keys
- **Internet connection**: Required for Whisper API and summarization

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Create Configuration

Copy `config.example.toml` to `config.toml` and add your OpenAI API key:

```bash
copy config.example.toml config.toml
```

Edit `config.toml` in a text editor and fill in your API key:

```toml
[api]
openai_api_key = "sk-..."
```

### 3. Run the Application

```bash
python src/main.py
```

The application starts in the system tray. You'll see a balloon notification: "Voice Paste is running!"

### 4. Find the Tray Icon

The Voice Paste icon appears in your system tray (bottom right of taskbar). If you don't see it immediately:

1. **Check the overflow area**: Click the **^** (arrow) icon in the taskbar to reveal hidden system tray icons.
2. **Pin the icon** (Windows 11): Right-click the taskbar → **Taskbar settings** → **Other system tray icons** → Turn on **VoicePaste**.

Once visible, press **Ctrl+Shift+V** to start recording.

## How It Works

```
Press Ctrl+Shift+V
    ↓
Record audio from microphone (in-memory only)
    ↓
Press Ctrl+Shift+V to stop recording
    ↓
Send audio to OpenAI Whisper API
    ↓
(Optional) Send transcript to GPT-4o-mini for cleanup
    ↓
Write result to clipboard
    ↓
Simulate Ctrl+V to paste at cursor
    ↓
Return to idle
```

## Configuration Reference

All options go in `config.toml`. See `config.example.toml` for a full template.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `[hotkey]` `combination` | string | `"ctrl+shift+v"` | Global hotkey to start/stop recording. Use format: `"ctrl+..."`, `"shift+..."`, `"alt+..."`, `"windows+..."`. |
| `[api]` `openai_api_key` | string | *(required)* | Your OpenAI API key. Must be set to use the tool. |
| `[summarization]` `enabled` | boolean | `true` | Enable GPT-4o-mini summarization. Set `false` to paste raw transcript. |
| `[feedback]` `audio_cues` | boolean | `true` | Play audio beeps on recording start/stop. Set `false` for silent operation. |
| `[logging]` `level` | string | `"INFO"` | Log level: `"DEBUG"`, `"INFO"`, `"WARNING"`, or `"ERROR"`. |

### Customizing the Hotkey

To change the hotkey from the default `Ctrl+Shift+V`, edit the `[hotkey]` section in your `config.toml`:

```toml
[hotkey]
combination = "ctrl+alt+v"
```

Use these modifier prefixes: `ctrl`, `shift`, `alt`, `windows`. Separate multiple modifiers with `+`. Key names are case-insensitive.

Examples:
- `"ctrl+shift+v"` – Ctrl+Shift+V (default)
- `"ctrl+win+v"` – Ctrl+Windows+V
- `"alt+shift+a"` – Alt+Shift+A
- `"ctrl+alt+m"` – Ctrl+Alt+M

**Caution**: Avoid hotkeys that conflict with your applications or Windows shortcuts. If your hotkey does not work, check the log file for conflict warnings.

## Keyboard Shortcuts

| Hotkey | Action |
|--------|--------|
| **Ctrl+Shift+V** | Start recording. Press again to stop and transcribe. |
| **Escape** | Cancel active recording (discard audio, don't paste). |

## Troubleshooting

### Tray Icon Not Visible

**Problem**: After launching the application, you don't see the Voice Paste icon in the system tray.

**Solutions**:
1. **Check the overflow area**: Click the **^** (arrow) icon in the taskbar to reveal hidden icons. Voice Paste may be there.
2. **Pin the icon**: Right-click your taskbar → **Taskbar settings** → **Other system tray icons** → Enable **VoicePaste** to keep it always visible.
3. **Verify the app is running**: Open Task Manager (Ctrl+Shift+Esc) and look for `python.exe` or `voice_paste.exe` in the Processes tab. If you don't see it, the app may have crashed.
4. **Debug mode**: Run the application with debug output to see startup messages:
   ```bash
   python src/main.py --debug
   ```
   Or for a built .exe:
   ```cmd
   VoicePaste.exe --debug
   ```

If the app appears to be running but the icon is missing, check `voice-paste.log` for tray initialization errors.

### Hotkey Not Working

**Problem**: Your configured hotkey (default Ctrl+Shift+V) does nothing when you press it.

**Solutions**:
1. **Run as Administrator**: The `keyboard` library requires elevated permissions to register global hotkeys. Run Command Prompt as Administrator and start the tool from there. Without admin privileges, hotkeys may not work in elevated windows (like Administrator PowerShell or UAC prompts).
2. **Verify the configuration**: Check that `[hotkey] combination` in `config.toml` is set correctly. Restart the tool after any config changes.
3. **Keyboard library issue**: Some Windows configurations have trouble with the `keyboard` library. Antivirus software may block it. See below.
4. **Hotkey conflict**: Your hotkey may conflict with other Windows shortcuts or applications. Try changing it in `config.toml` to a different combination (e.g., `"ctrl+alt+v"`). Check Windows Settings > Keyboard > Advanced > App Shortcuts for conflicts.

**Windows Defender / Antivirus Blocking**

The `keyboard` library uses low-level Windows hooks (same as system-level hotkey managers). Some antivirus software flags this as suspicious.

**Solutions**:
1. **Whitelist the tool**: Add the `.exe` or `python.exe` to your antivirus whitelist.
2. **Disable temporarily**: Temporarily disable your antivirus while testing.
3. **Use Settings > Virus & threat protection > Manage settings** to exclude the application folder.

If you cannot resolve antivirus issues, the tool will not work until the library is whitelisted.

### Microphone Not Detected

**Problem**: Recording fails with "No microphone detected."

**Solutions**:
1. **Check Settings > System > Sound** to verify your microphone is connected and enabled.
2. **Restart the tool** after plugging in a microphone (the tool checks for devices at recording start, not at launch).
3. **Test your microphone** in Windows Sound Settings or another application first.
4. **Check microphone permissions**: Some applications (especially in sandboxed environments) require explicit microphone access.

### API Errors

**Problem**: Toast shows "API error" or logs contain API failures.

**Solutions**:
1. **Check your API key**:
   - Make sure you copied it correctly into `config.toml`.
   - API keys start with `sk-`. If yours doesn't, you have the wrong key.
   - Log in to https://platform.openai.com/api-keys to verify your key is valid.

2. **Check API billing**:
   - Visit https://platform.openai.com/account/billing/overview.
   - Ensure you have available credits or a valid payment method.

3. **Check internet connection**:
   - Verify you can reach https://api.openai.com in a browser.
   - Check if your firewall or proxy is blocking the connection.

4. **Rate limiting**:
   - If you see "Rate limit exceeded", wait a moment and try again.
   - The tool retries failed API calls automatically (up to 2 retries with exponential backoff).

5. **Check log file**:
   - Look at `voice-paste.log` in your application directory for detailed error messages.
   - Search for `ERROR` to find the root cause.

### Paste Not Working in Terminal or Terminal Emulator

**Problem**: Text appears on clipboard but doesn't paste into a terminal.

**Cause**: Many terminal emulators (Windows Terminal, ConEmu, PowerShell ISE) use Ctrl+Shift+V for paste instead of Ctrl+V.

**Solutions**:
1. **Use Ctrl+Shift+V** manually if the tool's Ctrl+V doesn't work.
2. **Right-click paste** in the terminal (if available).
3. **Check terminal settings** for a custom paste keybind.

This is a known limitation of terminal emulators, not the tool itself.

### Recording Is Too Long (Continues Past Intent)

**Problem**: Recording keeps going even though you pressed the hotkey to stop.

**Solutions**:
1. **Check the hotkey registration**: Verify your configured hotkey is registered correctly. Check `voice-paste.log` for hotkey registration messages.
2. **Slow API response**: If transcription is taking a long time, the tool appears to still be recording while processing. Check the tray icon: if it's yellow, it's processing (not recording). Wait for it to return to grey.
3. **5-minute auto-stop**: The tool automatically stops recording after 5 minutes to prevent accidental endless recordings. You'll see a notification if this triggers.

### Nothing Happens When I Paste

**Problem**: Recording and transcription complete, but no text appears.

**Causes**:
1. **Empty recording**: If you didn't speak (silence only), Whisper returns an empty transcript and nothing is pasted. This is correct behavior.
2. **Wrong focus**: Verify the window where you want the text pasted had focus when you completed the recording. If you switched windows during processing, the paste goes to the currently active window.
3. **Application doesn't accept Ctrl+V**: Some custom text controls don't respond to simulated paste. Try right-clicking and selecting Paste manually.

Check the log file for "Empty recording" messages.

### Clipboard Contents Were Lost

**Problem**: Your clipboard contents disappeared after pasting.

**Cause (v0.1)**: v0.1 does not preserve the original clipboard. The tool overwrites it with the transcript. This is expected.

**Solution (v0.2+)**: Upgrade to v0.2+, which automatically backs up and restores your clipboard. If using v0.2+, this should not happen. Report it as a bug with your log file.

**Workaround**: Use Ctrl+Z in most applications to undo the paste and recover your clipboard if you made a mistake.

## Privacy & Security

### What Data Is Collected?

- **Audio recordings**: Captured only when you press your configured hotkey. Stored in memory only (never to disk).
- **Transcripts and summaries**: Generated from your speech. Sent to OpenAI for processing.
- **API key**: Stored locally in `config.toml`. Never sent anywhere except to OpenAI's servers.
- **Logs**: Written to `voice-paste.log`. Contains state changes and errors only. Never includes audio, transcripts, or your API key.

### Where Does Data Go?

When you record and press the hotkey to stop:
1. Audio is sent to OpenAI's Whisper API via HTTPS.
2. Transcript is sent to OpenAI's GPT-4o-mini API via HTTPS (if summarization enabled).
3. Text is placed on your local clipboard.
4. Nothing is stored on disk except your config and logs (no audio, no transcripts).

OpenAI's privacy policy applies to data sent to their services. Review it at https://openai.com/policies/privacy-policy.

### API Key Safety

Your OpenAI API key grants access to your paid API account. **Protect it like a password.**

**Do NOT:**
- Share your config.toml with anyone
- Commit config.toml to version control (use `config.example.toml` instead)
- Post your config in screenshots or error reports
- Store the key in sync services (OneDrive, Google Drive) without encryption

**Do:**
- Keep config.toml readable only by your user account
- Rotate your API key if you suspect compromise (https://platform.openai.com/api-keys)
- Use a low-privilege API key if possible (OpenAI supports organization-level key scoping)

### No Telemetry

The tool does not phone home, does not collect analytics, and does not track your usage. The only external communication is to OpenAI's API when you explicitly use the tool.

## Building from Source

### Prerequisites

- Python 3.11 or later
- pip (Python package manager)
- For PyInstaller builds: PyInstaller 6.0+

### Development Build

```bash
# Clone or download the project
cd C:\develop\speachtoText

# Create a virtual environment (optional but recommended)
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run
python src/main.py
```

### Single-File Executable Build

The project includes a PyInstaller configuration for bundling into a single .exe file:

```bash
# Install PyInstaller (if not already in requirements.txt)
pip install pyinstaller==6.x

# Run the build script
build.bat
```

Output: `dist\voice_paste.exe`

**Note**: The build process may take 2-3 minutes. The resulting .exe is ~50-60 MB (includes Python runtime, dependencies, and audio libraries).

**Requirements**:
- PyInstaller must be able to find your Python installation
- All dependencies must be installed in the current environment
- Antivirus software may flag the build temporarily (common for newly built executables; it's safe)

### Build Troubleshooting

**Error**: `ModuleNotFoundError` during build

**Solution**: Ensure all dependencies are installed: `pip install -r requirements.txt`

**Error**: PyInstaller cannot find `sounddevice`

**Solution**: This is a known issue with certain sounddevice versions. The build.bat specifies hidden imports. If it fails, update your .spec file to include:

```python
hiddenimports=['_sounddevice_data', 'numpy', 'openai']
```

See `voice_paste.spec` for the current configuration.

## License

This project is provided as-is. See LICENSE file for details.

## Support

For issues, questions, or feedback:

1. Check the **Troubleshooting** section above
2. Review `voice-paste.log` for detailed error information
3. Verify your configuration in `config.toml`
4. Ensure your OpenAI API key is valid and has available credits

## Version

**Current version**: 0.2.0 (v0.2: Core Experience)

See CHANGELOG.md for release notes.

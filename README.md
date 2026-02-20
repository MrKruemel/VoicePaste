<p align="center">
  <img src="assets/icon-readme.png" alt="Voice Paste icon" width="96" />
</p>

<h1 align="center">Voice Paste</h1>

<p align="center">
  A cross-platform desktop utility (Windows + Linux) that records your speech, transcribes it with AI, optionally summarizes it, and pastes the result at your cursor—all with a hotkey. Runs entirely in the system tray.
</p>

**Current version**: 1.1.0

## Features

- **Transcribe with a hotkey**: Press Ctrl+Alt+R to record, press again to transcribe and paste.
- **Ask questions with Voice Prompt**: Press Ctrl+Alt+A to record a question, get an AI answer, and paste it.
- **Read text aloud with TTS**: Press Ctrl+Alt+T (Windows) or Ctrl+Alt+S (Linux) to read clipboard content via text-to-speech.
- **Ask AI and hear the answer**: Press Ctrl+Alt+Y to ask a question and hear the answer read aloud.
- **Hands-Free mode**: Say a wake phrase (default "Hello Cloud") to start recording without touching the keyboard. Recording auto-stops when you pause speaking. Configurable pipeline (Ask+TTS, Transcribe+Paste, Ask+Paste).
- **HTTP API**: Localhost REST API for external apps and scripts. Control recording, TTS, and status via HTTP. Secured with CORS, rate limiting, 127.0.0.1-only binding.
- **Confirm-before-paste**: Optional delay or Enter keypress before pasting. Prevents accidental pasting into wrong window.
- **Floating overlay UI**: Non-intrusive status display in bottom-right corner. Shows recording timer, processing animation, speaking feedback, and paste confirmation. Disable in Settings.
- **TTS audio caching**: Automatically deduplicates synthesized speech. Replay cached audio from tray menu. Configurable cache size and retention.
- **TTS audio export**: Save synthesized speech to files with readable filenames. Choose export directory in Settings.
- **Choose your transcription source**: Cloud (OpenAI Whisper API) or offline (local faster-whisper with Silero VAD).
- **Multiple summarization backends**: OpenAI, OpenRouter (Claude, Llama), or local Ollama.
- **Multiple TTS providers**: ElevenLabs cloud (human-quality voices) or local Piper (offline, free, 14 voices including German, English US, English GB).
- **Tabbed Settings dialog**: Organized configuration interface with Transcription, Summarization, Text-to-Speech, Hands-Free, and General tabs.
- **Secure credential storage**: API keys stored in OS credential store (Windows Credential Manager / Linux keyring), never in plain text files.
- **Silent operation**: Runs in system tray. Never steals focus.
- **Audio feedback**: Beeps confirm recording start/stop/cancel/error. Disable in settings for silent mode.
- **Visual feedback**: Tray icon color changes per state (grey=idle, red=recording, yellow=processing, teal=awaiting paste, green=pasting, blue=speaking).
- **Cancel anytime**: Press Escape during recording to discard and return to idle.
- **Clipboard safety**: Original clipboard contents restored after pasting.
- **Toast notifications**: Errors appear as system notifications, not modal dialogs.

## Requirements

### Windows
- **Windows 10 or 11**
- **Python 3.11+** (for running from source)

### Linux (Ubuntu 22.04 / 24.04)
- **Python 3.11+**
- **System packages**: `espeak-ng libportaudio2 xclip xdotool python3-tk python3-gi gir1.2-ayatanaappindicator3-0.1`
- **GNOME tray icon** (optional): `gnome-shell-extension-appindicator`
- **X11 session** (Wayland support planned for v1.2)

### All Platforms
- **Microphone**: Connected and working (required only for recording modes)
- **For cloud transcription**: OpenAI API key (get one at https://platform.openai.com/api-keys)
- **For local transcription**: Disk space for Whisper model (75MB–3GB depending on size). CUDA auto-detection avoids segfaults when GPU drivers are incomplete; frozen binaries always use CPU.
- **For ElevenLabs TTS**: ElevenLabs API key (get one at https://elevenlabs.io)
- **For Piper local TTS**: Disk space for Piper voice models (~60–120 MB per voice). Requires `espeak-ng` (installed via `espeakng-loader` on Windows, system package on Linux). No API key needed.
- **For local summarization**: Ollama running locally (http://localhost:11434) if using Ollama provider
- **Internet connection**: Required only for cloud transcription, cloud summarization, and ElevenLabs TTS

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt

# Linux only: install system dependencies
sudo apt install espeak-ng libportaudio2 xclip xdotool python3-tk
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
pip install pynput  # Linux hotkey library (not in requirements.txt)
```

### 2. Create Configuration (Optional)

Copy `config.example.toml` to `config.toml` and customize if needed:

```bash
cp config.example.toml config.toml    # Linux
copy config.example.toml config.toml  # Windows
```

Edit `config.toml` in a text editor (optional — you can do this in the Settings dialog later).

### 3. Run the Application

```bash
python src/main.py
```

The application starts in the system tray. You'll see a balloon notification: "Voice Paste is running!"

### 4. Find the Tray Icon

The Voice Paste icon appears in your system tray (bottom right of taskbar). If you don't see it immediately:

1. **Check the overflow area**: Click the **^** (arrow) icon in the taskbar to reveal hidden system tray icons.
2. **Pin the icon** (Windows 11): Right-click the taskbar → **Taskbar settings** → **Other system tray icons** → Turn on **VoicePaste**.
3. **Linux (GNOME)**: Install `gnome-shell-extension-appindicator` and restart GNOME Shell (Alt+F2, type `r`, Enter) or log out and back in.

### 5. Set Up Your API Key (First Run)

When you press Ctrl+Alt+R for the first time, the Settings dialog opens (or right-click tray → Settings). Add your OpenAI API key:

1. Click **Credentials** tab
2. Paste your API key in the OpenAI field
3. Click **Save**

Your key is stored securely in the OS credential store (not in config.toml).

### 6. Start Recording

Press **Ctrl+Alt+R** to start recording. Speak into your microphone. Press **Ctrl+Alt+R** again to stop, transcribe, and paste.

## How It Works

### Normal Mode (Ctrl+Alt+R)

```
Press Ctrl+Alt+R
    ↓
Record audio from microphone (in-memory only)
    ↓
Press Ctrl+Alt+R to stop recording
    ↓
Send audio to Whisper (cloud or local)
    ↓
(Optional) Send transcript to LLM for cleanup/summarization
    ↓
Write result to clipboard
    ↓
Simulate Ctrl+V to paste at cursor
    ↓
Restore original clipboard contents
    ↓
Return to idle
```

### Voice Prompt Mode (Ctrl+Alt+A)

```
Press Ctrl+Alt+A
    ↓
Record speech as a question or command
    ↓
Press Ctrl+Alt+A to stop recording
    ↓
Send audio to Whisper for transcription
    ↓
Send transcript as a prompt to LLM
    ↓
LLM generates an answer
    ↓
Write answer to clipboard
    ↓
Simulate Ctrl+V to paste answer at cursor
    ↓
Return to idle
```

## Configuration Reference

All options can be set via the **Settings dialog** (right-click tray → Settings) or by editing `config.toml` directly.

### Hotkeys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `[hotkey]` `combination` | string | `"ctrl+alt+r"` | Global hotkey to start/stop recording. Use format: `"ctrl+alt+r"`, `"ctrl+shift+v"`, `"windows+shift+a"`, etc. |
| `[hotkey]` `prompt_combination` | string | `"ctrl+alt+a"` | Voice Prompt hotkey: record speech, send as question to LLM, paste answer. Set to empty string to disable. |

### Transcription (Speech-to-Text)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `[transcription]` `backend` | string | `"cloud"` | Transcription source: `"cloud"` (OpenAI Whisper API) or `"local"` (faster-whisper, offline). |
| `[transcription]` `model_size` | string | `"base"` | Local model size: `"tiny"` (~75MB, fast), `"base"` (~145MB, recommended), `"small"` (~480MB), `"medium"` (~1.5GB), `"large-v2"` (~3GB), `"large-v3"` (~3GB). Only used when `backend = "local"`. |
| `[transcription]` `device` | string | `"cpu"` | Compute device: `"cpu"` (works everywhere) or `"cuda"` (NVIDIA GPU, faster). Only for local backend. |
| `[transcription]` `compute_type` | string | `"int8"` | Quantization: `"int8"` (fastest, CPU), `"float16"` (GPU), `"float32"` (highest quality). Only for local backend. |
| `[transcription]` `vad_filter` | boolean | `true` (script) / `false` (.exe) | Voice Activity Detection: skip silence before transcription. Improves accuracy. Auto-disabled in frozen .exe. |

### Summarization (Text Cleanup & Summarization)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `[summarization]` `enabled` | boolean | `true` | Enable text cleanup and summarization. Set `false` for raw transcript. |
| `[summarization]` `provider` | string | `"openai"` | LLM provider: `"openai"` (GPT-4o-mini), `"openrouter"` (Claude, Llama, etc.), or `"ollama"` (local). |
| `[summarization]` `model` | string | `"gpt-4o-mini"` | Model name. Examples: `"gpt-4o-mini"` (OpenAI), `"claude-3-haiku"` (OpenRouter), `"llama3.2"` (Ollama). |
| `[summarization]` `base_url` | string | (empty) | Custom API endpoint (for proxies, self-hosted, or OpenRouter). Leave empty to use provider default. |
| `[summarization]` `custom_prompt` | string | (empty) | Custom system prompt for LLM. Leave empty to use the default cleanup prompt. |

### Text-to-Speech

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `[tts]` `enabled` | boolean | `false` | Enable text-to-speech output. Disabled by default; enable in Settings or set to `true`. |
| `[tts]` `provider` | string | `"elevenlabs"` | TTS provider: `"elevenlabs"` (cloud, high quality, requires API key) or `"piper"` (local, offline, free). |
| `[tts]` `voice_id` | string | `"pFZP5JQG7iQjIQuC4Bku"` | ElevenLabs voice ID (Lily by default). Browse voices at https://elevenlabs.io/voice-library. Only used when `provider = "elevenlabs"`. |
| `[tts]` `model_id` | string | `"eleven_flash_v2_5"` | ElevenLabs model ID. Default: `"eleven_flash_v2_5"` (fast, low latency). Only used when `provider = "elevenlabs"`. |
| `[tts]` `output_format` | string | `"mp3_44100_128"` | ElevenLabs output format. Only used when `provider = "elevenlabs"`. |
| `[tts]` `local_voice` | string | `"de_DE-thorsten-medium"` | Piper voice model name. See below for available voices. Download models via Settings. Only used when `provider = "piper"`. |
| `[tts]` `speed` | float | `1.0` | Piper speech speed (length_scale). `0.5` = slow, `1.0` = normal, `2.0` = fast. Range: 0.25–4.0. Only used when `provider = "piper"`. |

### Feedback & Logging

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `[feedback]` `audio_cues` | boolean | `true` | Play audio beeps on recording start/stop/cancel/error and TTS completion. Set `false` for silent operation. |
| `[feedback]` `show_overlay` | boolean | `true` | Show floating overlay UI in bottom-right corner with state feedback. Set `false` to disable overlay. |
| `[logging]` `level` | string | `"INFO"` | Log level: `"DEBUG"`, `"INFO"`, `"WARNING"`, or `"ERROR"`. |

## Choosing a Transcription Backend

### Cloud Transcription (Default: OpenAI Whisper API)

**Pros:**
- Excellent quality for all languages, including German
- Fast (2–5 seconds per minute of audio)
- Works immediately, no setup
- No local disk space needed

**Cons:**
- Requires internet connection
- Requires OpenAI API key and account credit
- ~$0.006 per minute of audio ($0.36 per hour)
- Audio leaves your machine (sent to OpenAI)

**Best for:** Quick transcription, high quality, German language.

### Local Transcription (faster-whisper)

**Pros:**
- Works offline (no internet needed)
- No per-use cost
- Audio stays on your machine
- Supports all languages
- Customizable model size/quality tradeoff

**Cons:**
- Slower (3–60 seconds depending on model size and CPU)
- Uses disk space (75MB–3GB)
- Requires more RAM (150MB–1.2GB depending on model)
- Requires onnxruntime (bundled with PyInstaller)

**Best for:** Privacy, offline use, cost-sensitive workflows.

**How to enable:**
1. Open Settings (right-click tray → Settings)
2. Go to **Transcription** tab
3. Change `backend` from "Cloud" to "Local"
4. Select a model size (recommended: Base ~145MB)
5. Click **Download Model** (one-time, ~2–5 minutes)
6. Click **Save**

## Available Piper TTS Voices

When using local Piper TTS, the following voices are available. Each voice is approximately 60–120 MB and is downloaded on demand.

### German Voices
- `de_DE-thorsten-medium` (recommended, ~63 MB) — Neutral male voice, good balance of quality and size.
- `de_DE-thorsten-high` (~114 MB) — Higher quality version of Thorsten.
- `de_DE-thorsten_emotional-medium` (~77 MB) — Multi-emotion support, expressive.
- `de_DE-mls-medium` (~95 MB) — Alternative male voice.

### English (US) Voices
- `en_US-ryan-high` (~114 MB) — Male voice, high quality.
- `en_US-ryan-medium` (~64 MB) — Male voice, medium quality.
- `en_US-lessac-high` (~114 MB) — Male voice, high quality.
- `en_US-lessac-medium` (~64 MB) — Male voice, medium quality.
- `en_US-amy-medium` (~64 MB) — Female voice, medium quality.

### English (GB) Voices
- `en_GB-cori-high` (~114 MB) — Female voice, high quality.
- `en_GB-cori-medium` (~64 MB) — Female voice, medium quality.
- `en_GB-alba-medium` (~64 MB) — Female voice, medium quality.
- `en_GB-jenny_dioco-medium` (~64 MB) — Female voice, medium quality.
- `en_GB-alan-medium` (~64 MB) — Male voice, medium quality.

Voice models are cached in `%LOCALAPPDATA%\VoicePaste\models\tts\` (Windows) or `~/.cache/VoicePaste/models/tts/` (Linux) and auto-downloaded on demand in Settings > Text-to-Speech > Download Model.

## Keyboard Shortcuts

| Hotkey | Action |
|--------|--------|
| **Ctrl+Alt+R** | Start/stop recording for transcription and paste. |
| **Ctrl+Alt+A** | Start/stop recording for voice prompt (question → answer). |
| **Ctrl+Alt+T** (Win) / **Ctrl+Alt+S** (Linux) | Read clipboard content aloud (TTS). Requires TTS enabled in Settings. |
| **Ctrl+Alt+Y** | Ask AI a question and hear the answer (record → summarize → TTS). Requires TTS enabled. |
| **Escape** | Cancel active recording or TTS playback (discard audio, don't paste). |
| **Right-click tray** | Show menu (Settings, Quit). |

## Settings Dialog

Right-click the tray icon and select **Settings** to open the configuration dialog. Tabs:

- **Transcription**: Choose cloud (OpenAI Whisper) or local (faster-whisper) backend. Download local models, set device/compute type, enable/disable VAD filter.
- **Summarization**: Enable/disable text cleanup. Choose provider (OpenAI, OpenRouter, Ollama). Custom prompts.
- **Text-to-Speech**: Enable/disable TTS. Choose provider (ElevenLabs cloud or Piper local). Download Piper voice models, select voice. Adjust speech speed. Configure TTS caching (deduplication, retention) and export settings (save to files).
- **Hands-Free**: Enable/disable wake word detection. Configure wake phrase, matching mode, pipeline, silence timeout, max recording duration.
- **General**: Toggle audio cues, set log level, manage API credentials (OpenAI, OpenRouter, ElevenLabs) via OS credential store. Toggle floating overlay display.

Changes save immediately. No restart needed (hot-reload).

### TTS Audio Caching & Export

When TTS is enabled, the application can cache synthesized speech and save it to files.

**Audio Caching**: Automatically deduplicates repeated text. When you synthesize the same text again, the cached audio plays immediately without re-synthesizing, saving API calls and latency.

- **Access cached audio**: Right-click the tray icon > **Recent TTS Audio** to see and replay up to 10 most recent synthesized clips.
- **Configure cache**: Settings > Text-to-Speech > Cache settings. You can set maximum cache size (default 200 MB), retention period (default 30 days), and maximum number of entries (default 500).

**Audio Export**: Save synthesized speech to files with readable, timestamped filenames.

- **Export TTS**: Settings > Text-to-Speech > Export settings. Choose an export folder. When you synthesize speech, click "Export" in the notification (if shown) or right-click tray > **Export Recent** to save to the chosen folder.
- **File format**: Files use format `YYYYMMDD_HHMMSS_[text_preview].wav` for local Piper TTS or `.mp3` for ElevenLabs cloud TTS.
- **Disable export**: Leave the export folder empty in Settings to disable this feature.

### Floating Overlay Display

The floating overlay is a non-intrusive status display in the bottom-right corner of your screen. It shows:

- **Recording**: Red dot + live timer (MM:SS format).
- **Processing**: Amber dot + animated dots (Processing. / Processing.. / Processing...).
- **Speaking** (TTS): Blue pulsing dot + "Speaking...".
- **Pasting**: Green dot + "Pasted" (auto-hides after 800ms).
- **Idle**: Overlay is hidden.

The overlay never steals focus (click-through, non-activatable). You can toggle it on/off in Settings > General > "Show floating overlay" without restarting the app.

## Troubleshooting

### Tray Icon Not Visible

**Problem**: After launching the application, you don't see the Voice Paste icon in the system tray.

**Solutions**:
1. **Check the overflow area**: Click the **^** (arrow) icon in the taskbar to reveal hidden icons. Voice Paste may be there.
2. **Pin the icon**: Right-click your taskbar → **Taskbar settings** → **Other system tray icons** → Enable **VoicePaste** to keep it always visible.
3. **Verify the app is running**: Open Task Manager (Ctrl+Shift+Esc) and look for `python.exe` or `voice_paste.exe` in the Processes tab. If you don't see it, the app may have crashed.
4. **Debug mode**: Run the application with debug output:
   ```bash
   python src/main.py --debug
   ```
   Or for a built .exe:
   ```cmd
   VoicePaste.exe --debug
   ```

If the app is running but the icon is missing, check `voice-paste.log` for tray initialization errors.

### Hotkey Not Working

**Problem**: Your configured hotkey (default Ctrl+Alt+R) does nothing when you press it.

**Solutions**:
1. **Windows: Run as Administrator**: The `keyboard` library requires elevated permissions to register global hotkeys. Run Command Prompt as Administrator and start the tool from there. Without admin privileges, hotkeys may not work in elevated windows (like Administrator PowerShell or UAC prompts).
2. **Linux: Check X11 session**: Hotkeys use pynput which requires an X11 session. Wayland is not yet supported (planned for v1.2). Run `echo $XDG_SESSION_TYPE` to check.
3. **Verify the configuration**: Check `[hotkey] combination` in `config.toml` or Settings → Hotkey tab. Restart the tool after changing the hotkey.
4. **Check for conflicts**: Your hotkey may conflict with other Windows shortcuts or applications. Try a different combination (e.g., `"ctrl+alt+v"` or `"windows+shift+r"`). See Windows Settings > Keyboard > Advanced > App Shortcuts.
5. **Keyboard library issue**: Some Windows configurations have trouble with the `keyboard` library. Antivirus software may block it. See below.

**Windows Defender / Antivirus Blocking**

The `keyboard` library uses low-level Windows hooks (same as system-level hotkey managers). Some antivirus software flags this as suspicious.

**Solutions**:
1. **Whitelist the tool**: Add the `.exe` or `python.exe` to your antivirus exclusion list.
2. **Exclude folder**: In Windows Security → Virus & threat protection → Manage settings → Exclusions, add the application folder.

If the `keyboard` library remains blocked by your antivirus, the global hotkey will not function until the application is whitelisted.

### Microphone Not Detected

**Problem**: Recording fails with "No microphone detected" error.

**Solutions**:
1. **Check Settings > System > Sound** to verify your microphone is connected and enabled.
2. **Restart the tool** after plugging in a microphone (the tool detects devices when recording starts, not at launch).
3. **Test your microphone** in Windows Sound Settings or another application first.
4. **Check microphone permissions**: Some applications require explicit microphone access. Grant permission if prompted.

### API Errors

**Problem**: Toast shows "API error" or logs contain API failures.

**Solutions**:
1. **Check your API key**:
   - Verify it's correct in Settings > Credentials tab
   - API keys start with `sk-` (OpenAI) or have a specific format per provider
   - Log in to your provider's website to confirm the key is valid

2. **Check account status**:
   - Visit https://platform.openai.com/account/billing/overview (OpenAI) or your provider's dashboard
   - Ensure you have available credits or a valid payment method

3. **Check internet connection**:
   - Verify you can reach the API in a browser (https://api.openai.com or your provider)
   - Check if your firewall or proxy is blocking the connection

4. **Rate limiting**:
   - If you see "Rate limit exceeded", wait a moment and try again
   - The tool automatically retries failed calls (up to 2 retries with exponential backoff)

5. **Check log file**:
   - Open `voice-paste.log` in your application directory for detailed error messages
   - Search for `ERROR` to find the root cause

### Transcription Empty or Failed

**Problem**: Recording completes but nothing is transcribed or pasted.

**Solutions**:
1. **Silent recording**: If you didn't speak (only silence), Whisper returns an empty transcript. This is correct behavior. Speak clearly into the microphone.
2. **Check microphone level**: Verify your microphone input level is adequate in Windows Sound Settings.
3. **Recording too short**: Minimum recording duration is 0.5 seconds. Try speaking longer or at normal volume.
4. **Wrong transcription backend**: If using local mode, verify the model is downloaded (Settings > Transcription > Download Model).

Check `voice-paste.log` for "Empty recording" messages.

### Local Transcription Crashes in .exe

**Problem**: Using local transcription in the frozen .exe causes a crash (segfault, no Python error).

**Cause**: onnxruntime (used for VAD filter) has a known issue with PyInstaller --onefile bundles.

**Solution**:
1. **Disable VAD filter**: Open Settings > Transcription tab, uncheck "Voice Activity Detection", click Save.
2. **Use cloud transcription**: Switch to cloud mode (Settings > Transcription > Backend = Cloud)
3. **Report the issue**: If disabling VAD doesn't help, see Build Troubleshooting below

For details on VAD handling, see the [Troubleshooting](README.md#troubleshooting) section and build configuration.

### Paste Not Working in Terminal

**Problem**: Text appears on clipboard but doesn't paste into a terminal window.

**Cause**: Many terminal emulators use Ctrl+Shift+V for paste instead of Ctrl+V.

**Solutions**:
1. **Linux**: Voice Paste automatically detects terminal emulators (GNOME Terminal, Konsole, Alacritty, kitty, xterm, etc.) and uses Ctrl+Shift+V. If your terminal is not detected, please report it.
2. **Windows**: Use Ctrl+Shift+V manually, right-click paste, or change terminal settings to use Ctrl+V.

### Clipboard Contents Lost

**Problem**: Your clipboard contents disappeared after using the tool.

**Cause**: The current version automatically backs up and restores clipboard contents. If this issue occurs, it may indicate a bug.

**Solution**: If your clipboard was lost, use Ctrl+Z in most applications to undo the paste and recover your clipboard. Report the issue with your `voice-paste.log` for investigation.

**Workaround**: Use Ctrl+Z in most applications to undo the paste and recover your clipboard if you made a mistake.

## Privacy & Security

### What Data Leaves Your Machine?

When you use the tool:
- **Cloud transcription**: Audio is sent to OpenAI's Whisper API via HTTPS
- **Cloud summarization**: Transcript is sent to OpenAI's GPT-4o-mini API (or your chosen provider) via HTTPS
- **Settings dialog**: No data leaves your machine. Everything is local.
- **Logs**: Stored locally in `voice-paste.log`. Never uploaded.

**Audio is never written to disk.** It stays in memory only.

### API Key Safety

Your OpenAI API key grants access to your paid API account and incurs charges. **Protect it like a password.**

**Do NOT:**
- Share your config.toml or Credential Manager with anyone
- Commit config.toml to version control (use `config.example.toml` instead)
- Post your config in screenshots or error reports
- Store the key in sync services (OneDrive, Google Drive) without encryption

**Do:**
- Use the Settings dialog (stores keys in Windows Credential Manager, encrypted by Windows)
- Keep your config.toml readable only by your user account
- Rotate your API key if you suspect compromise (https://platform.openai.com/api-keys)
- Use a low-privilege API key if possible (OpenAI supports organization-level key scoping)

### No Telemetry

The tool does not phone home, does not collect analytics, and does not track your usage. The only external communication is to your chosen API provider (OpenAI, OpenRouter, or Ollama) when you explicitly use the tool.

## Building from Source

### Prerequisites

- Python 3.11 or later
- pip (Python package manager)
- For PyInstaller builds: PyInstaller 6.0+

### Development Build (From Source)

```bash
# Clone or download the project
cd C:\path\to\speachtoText

# Create a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run
python src/main.py
```

### Single-File Executable Build

The project includes a PyInstaller configuration to bundle into a single .exe file:

```bash
# Install PyInstaller if not in requirements.txt
pip install pyinstaller==6.x

# Run the build script
build.bat
```

**Output**: `dist\voice_paste.exe`

**Build time**: 2–3 minutes.

**Binary size**: ~50–60 MB (includes Python runtime, dependencies, and audio libraries).

### Linux Build

```bash
# Install dependencies
sudo apt install espeak-ng libportaudio2 xclip xdotool python3-tk
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
pip install -r requirements.txt pyinstaller pynput

# Build
./build_linux.sh

# Output: dist/VoicePaste (~80–140 MB portable binary)
```

**Requirements**:
- PyInstaller must be able to find your Python installation
- All dependencies must be installed in the current environment (pip install -r requirements.txt)
- Antivirus software may flag the .exe temporarily (common for newly built executables; it's safe)

### Build Troubleshooting

**Error**: `ModuleNotFoundError` during build

**Solution**: Ensure all dependencies are installed: `pip install -r requirements.txt`

**Error**: PyInstaller cannot find `sounddevice` or `openai`

**Solution**: These are common hidden imports. The build.bat and voice_paste.spec already include them. If it fails, update `voice_paste.spec` to add:

```python
hiddenimports=['_sounddevice_data', 'numpy', 'openai', 'faster_whisper', 'onnxruntime']
```

See `voice_paste.spec` for the current configuration.

**Error**: `.exe crashes immediately or hangs during startup`

**Solution**: Run from command prompt to see startup errors:

```cmd
VoicePaste.exe
```

Check `voice-paste.log` for details. Common issues:
- Missing config.toml (the tool creates a template automatically)
- API key validation failure (check Credential Manager)
- Tray icon initialization error (check log for details)

**Error**: `onnxruntime` crashes in frozen .exe

**Cause**: onnxruntime has a known issue with PyInstaller --onefile when loading ONNX model files from the temporary _MEI* directory.

**Solution**: Disable VAD filter in settings or use cloud transcription. See [constants.py](src/constants.py) line 138–139 for details.

## License

This project is provided as-is. See LICENSE file for details.

## Support

For issues, questions, or feedback:

1. Check the **Troubleshooting** section above
2. Review `voice-paste.log` for detailed error information
3. Verify your configuration in Settings dialog or `config.toml`
4. Ensure your API key is valid and has available credits
5. Check the [GitHub repository](https://github.com/) for known issues and updates

## Release History

See [CHANGELOG.md](CHANGELOG.md) for detailed release notes and what changed between versions.

The application has evolved through multiple releases, accumulating features such as:
- Local and cloud transcription backends with configurable quality/speed tradeoffs
- Multiple LLM providers for text cleanup and Q&A
- Cloud and local text-to-speech with 14 voice variants
- Secure credential storage via OS credential store
- Floating overlay UI for non-intrusive status feedback
- HTTP API for external program integration
- Hands-Free mode with wake word detection
- TTS audio caching with deduplication and replay
- TTS audio export to files

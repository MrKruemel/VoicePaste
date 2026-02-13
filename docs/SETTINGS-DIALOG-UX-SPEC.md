# Settings Dialog UX Specification

## Voice-to-Summary Paste Tool

**Date**: 2026-02-13
**Author**: UX Designer
**Status**: Draft for review

---

## 1. Overview

A single-window Settings dialog, opened from the system tray context menu, that allows users to configure API keys, summarization provider, model, and general preferences. Built with tkinter (Python stdlib). Replaces the need to hand-edit config.toml for common settings.

### User Story

As a user, I want to change my API key, switch summarization providers, and toggle audio cues from a simple dialog, so that I do not have to find and edit a TOML file by hand.

---

## 2. Dialog Basics

### 2.1 Window Properties

| Property          | Value                                                      |
|-------------------|------------------------------------------------------------|
| Title             | `Voice Paste - Settings`                                   |
| Minimum size      | 480 x 520 pixels                                           |
| Default size      | 480 x 520 pixels (not resizable)                           |
| Resizable         | No (fixed size)                                            |
| Position          | Centered on the primary monitor                            |
| Modality          | Non-modal (does not block other applications)              |
| Taskbar presence  | Yes (standard tkinter Toplevel behavior on Windows)        |
| Window icon       | None (default tkinter icon is acceptable for a utility)    |
| Always on top     | No                                                         |
| Escape key        | Closes dialog without saving (same as Cancel)              |

### 2.2 Focus Behavior

- The dialog opens and takes focus. This is the ONE exception to the "never steal focus" principle, and it is justified because the user explicitly requested the dialog via the tray menu. They are not mid-workflow; they are configuring the tool.
- When the dialog is closed, focus returns to whatever was focused before. No explicit focus management is needed; Windows handles this automatically.
- The dialog does NOT block the voice paste hotkey. If the user presses Ctrl+Alt+R while Settings is open, the recording starts normally. The dialog does not interfere with core functionality.

### 2.3 Singleton Behavior

Only one Settings dialog may be open at a time. If the user clicks "Settings..." in the tray menu while the dialog is already open, bring the existing dialog to the front (call `dialog.lift()` and `dialog.focus_force()`). Do NOT open a second instance.

---

## 3. Layout Specification

The dialog uses a single scrollable column layout. All sections are visible at once (no tabs, no accordion, no wizard). Sections are separated by labeled frames (tkinter `LabelFrame`).

### 3.1 Visual Structure (top to bottom)

```
+------------------------------------------------------+
|  Voice Paste - Settings                          [X]  |
+------------------------------------------------------+
|                                                       |
|  +-- Transcription (OpenAI Whisper) ---------------+ |
|  |                                                  | |
|  |  API Key   [****************************5Abc]    | |
|  |            (i) Required for speech-to-text.      | |
|  |                Get a key at platform.openai.com  | |
|  |                                                  | |
|  +--------------------------------------------------+ |
|                                                       |
|  +-- Summarization --------------------------------+ |
|  |                                                  | |
|  |  [x] Enable summarization                       | |
|  |                                                  | |
|  |  Provider  [OpenAI           v]                  | |
|  |                                                  | |
|  |  API Key   [                              ]      | |    <-- only visible when OpenRouter
|  |            (i) Required for OpenRouter.          | |    <-- only visible when OpenRouter
|  |                                                  | |
|  |  Model     [gpt-4o-mini                   ]      | |
|  |                                                  | |
|  |  Base URL  [https://api.openai.com/v1     ]      | |
|  |            (i) Advanced. Change only if using    | |
|  |                a custom endpoint.                | |
|  |                                                  | |
|  +--------------------------------------------------+ |
|                                                       |
|  +-- General --------------------------------------+ |
|  |                                                  | |
|  |  Hotkey    Ctrl+Alt+R            (read-only)     | |
|  |            (i) Change in config.toml             | |
|  |                                                  | |
|  |  [x] Play audio cues                            | |
|  |                                                  | |
|  +--------------------------------------------------+ |
|                                                       |
|                              [ Cancel ]  [  Save  ]   |
|                                                       |
+------------------------------------------------------+
```

### 3.2 Spacing and Padding

| Element                    | Value              |
|----------------------------|--------------------|
| Window padding (all sides) | 12px               |
| Section vertical gap       | 8px                |
| LabelFrame internal padx   | 10px               |
| LabelFrame internal pady   | 8px (top), 10px (bottom) |
| Label-to-field gap         | 4px (vertical, hint text below field) |
| Field row vertical spacing | 6px                |
| Button area top padding    | 12px               |
| Button gap (Cancel to Save)| 8px                |

### 3.3 Widget Sizing

| Widget          | Width                                   |
|-----------------|-----------------------------------------|
| Entry fields    | Fill available width (sticky="ew")      |
| Dropdown        | Fill available width (sticky="ew")      |
| Labels (left)   | Fixed column, right-aligned, 70px       |
| Hint text        | Fill width, left-aligned under field    |
| Buttons         | 80px minimum width, auto-expand to text |

---

## 4. Section Details

### 4.1 Transcription (OpenAI Whisper)

This section is always visible and cannot be collapsed or disabled.

#### 4.1.1 API Key Field

| Property         | Specification                                                 |
|------------------|---------------------------------------------------------------|
| Label            | `API Key`                                                     |
| Widget           | `ttk.Entry` with `show="*"` (masked by default)              |
| Initial value    | The current key from config, shown masked as `*` characters   |
| Placeholder      | If no key is configured: `sk-...` (grey italic placeholder)  |
| Editing behavior | See Section 4.1.2 below                                      |
| Max length       | 256 characters (arbitrary safety limit)                       |
| Hint text        | `Required for speech-to-text. Get a key at platform.openai.com` |
| Hint style       | Grey (#666666), 9pt, regular weight                           |

#### 4.1.2 API Key Masking Behavior

The API key field must handle three distinct states:

**State A: Displaying existing key (dialog just opened, key exists in config)**
- The Entry widget shows the masked representation: `****5Abc` (asterisks + last 4 chars).
- The field uses `show=""` (not `show="*"`) because we are displaying a pre-masked string.
- The field is read-only (`state="readonly"`).
- A small "Edit" button (or pencil icon via Unicode character) appears to the right of the field.

**State B: Editing (user clicked Edit)**
- The field clears its contents entirely and becomes editable (`state="normal"`).
- The field switches to `show="*"` so newly typed characters are masked.
- The "Edit" button changes to "Cancel" (reverts to State A if clicked).
- Focus moves to the field.
- The user types or pastes their new key.

**State C: No key configured (first run or key was deleted)**
- The field is editable (`state="normal"`) with `show="*"`.
- Placeholder text `sk-...` is shown in grey until the user types.
- No "Edit" button is shown (field is already editable).

**Rationale**: We never load the full, unmasked API key into the Entry widget's text buffer. This prevents accidental exposure via copy-paste or screen sharing. The user must explicitly clear and re-enter the key to change it.

#### 4.1.3 Transcription Hint Link

The text `platform.openai.com` in the hint is NOT a hyperlink (tkinter Entry-based hint text does not support clickable links without significant complexity). It is simply readable text the user can type into a browser. This is acceptable for a utility.

### 4.2 Summarization

#### 4.2.1 Enable Checkbox

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Enable summarization`                                       |
| Widget           | `ttk.Checkbutton`                                            |
| Initial value    | Checked if `summarization_enabled` is true in config         |
| Behavior         | When unchecked, all other fields in this section become disabled (greyed out). They retain their values but are not editable. When re-checked, they become editable again. |

#### 4.2.2 Provider Dropdown

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Provider`                                                   |
| Widget           | `ttk.Combobox` with `state="readonly"` (no free-text entry) |
| Options          | `OpenAI`, `OpenRouter`                                       |
| Initial value    | Derived from current config (see Section 4.2.5)              |
| Disabled when    | Summarization checkbox is unchecked                          |

#### 4.2.3 Summarization API Key Field

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `API Key`                                                    |
| Widget           | `ttk.Entry` with same masking behavior as Section 4.1.2      |
| Visibility       | **Only visible when Provider is "OpenRouter"**               |
| Initial value    | The OpenRouter key from config (if configured), masked       |
| Placeholder      | `sk-or-...` (grey italic placeholder when empty)             |
| Hint text        | `Required for OpenRouter. Get a key at openrouter.ai`        |
| Hint style       | Same as Section 4.1.1                                        |
| Disabled when    | Summarization checkbox is unchecked                          |

When Provider is "OpenAI", this field and its hint are hidden entirely (not just disabled -- hidden). This is because OpenAI summarization reuses the transcription API key, and showing a disabled field would confuse users about whether they need a second key.

#### 4.2.4 Model Name Field

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Model`                                                      |
| Widget           | `ttk.Entry` (free-text, editable)                            |
| Initial value    | Current model from config, or provider default               |
| Provider defaults| OpenAI: `gpt-4o-mini` / OpenRouter: `openai/gpt-4o-mini`    |
| Disabled when    | Summarization checkbox is unchecked                          |
| Max length       | 128 characters                                               |
| Validation       | No validation on input. Invalid model names will produce an API error at runtime, which is surfaced via the normal error toast notification flow. This is acceptable because model names change frequently and local validation would be constantly outdated. |

#### 4.2.5 Base URL Field

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Base URL`                                                   |
| Widget           | `ttk.Entry` (free-text, editable)                            |
| Initial value    | Current base URL from config, or provider default            |
| Provider defaults| OpenAI: `https://api.openai.com/v1` / OpenRouter: `https://openrouter.ai/api/v1` |
| Hint text        | `Advanced. Change only if using a custom endpoint.`          |
| Hint style       | Same grey as other hints                                     |
| Disabled when    | Summarization checkbox is unchecked                          |
| Max length       | 512 characters                                               |

#### 4.2.6 Provider Change Behavior

When the user changes the Provider dropdown:

1. **Model field**: Reset to the new provider's default value. If the user had edited the model field manually, show a confirmation: No -- do NOT show a confirmation dialog. This is a utility. Simply reset the model to the new default. If the user changed provider, they almost certainly want the default model for that provider. If they want a custom model, they will type it again. The cost of retyping a model name is trivial; the cost of a modal confirmation dialog is a workflow interruption.

2. **Base URL field**: Reset to the new provider's default value. Same rationale as model.

3. **API Key field visibility**: If switching to OpenRouter, show the summarization API Key field (with its current value or empty). If switching to OpenAI, hide the summarization API Key field.

4. **No data loss**: The OpenRouter API key value is retained in memory even when the field is hidden (in case the user switches back to OpenRouter).

### 4.3 General

#### 4.3.1 Hotkey Display

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Hotkey`                                                     |
| Widget           | `ttk.Label` (not an Entry -- purely informational)           |
| Value            | The current hotkey from config, formatted (e.g., `Ctrl+Alt+R`) |
| Style            | Normal text, same font as entry fields                       |
| Hint text        | `Change in config.toml`                                      |
| Hint style       | Same grey as other hints                                     |

This is read-only for now. Hotkey capture UX is complex (must handle conflicts, display key names correctly, deal with modifier-only combos) and is deferred to a future version. The hint tells the user where to change it.

#### 4.3.2 Audio Cues Checkbox

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Play audio cues`                                            |
| Widget           | `ttk.Checkbutton`                                            |
| Initial value    | Checked if `audio_cues_enabled` is true in config            |

---

## 5. Button Bar

### 5.1 Layout

Buttons are right-aligned at the bottom of the dialog, in a horizontal row:

```
                                    [ Cancel ]  [  Save  ]
```

### 5.2 Cancel Button

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Cancel`                                                     |
| Behavior         | Close the dialog without saving any changes. No confirmation dialog (the user pressed Cancel deliberately). |
| Keyboard         | Escape key triggers Cancel                                   |
| Focus appearance | Standard ttk button focus ring                               |

### 5.3 Save Button

| Property         | Specification                                                |
|------------------|--------------------------------------------------------------|
| Label            | `Save`                                                       |
| Behavior         | Validate inputs, write to config.toml, close dialog          |
| Keyboard         | Enter key triggers Save (when no specific field has focus that consumes Enter) |
| Appearance       | Default button (visual emphasis -- slight border/color difference via ttk styling) |
| Disabled when    | Never. Save is always enabled. Validation happens on click.  |

### 5.4 Save Flow

When Save is clicked:

1. **Validate** all fields (see Section 6).
2. If validation fails, show inline error text (see Section 6) and do NOT close the dialog.
3. If validation passes:
   a. Write all settings to `config.toml` (preserving comments where possible, or rewriting the file in a clean format).
   b. Close the dialog.
   c. Show a toast notification: **"Settings saved. Restart Voice Paste to apply changes."**
   d. Do NOT attempt to hot-reload the configuration. A restart is required. This is the simplest and safest approach -- hot-reloading would require re-initializing the OpenAI client, re-registering hotkeys, and managing partial state. The complexity is not justified for a settings change that happens rarely.

---

## 6. Validation

### 6.1 Validation Rules

Validation runs when the user clicks Save. There is no real-time validation (no red borders while typing). This keeps the interaction simple and non-distracting.

| Field                       | Rule                                                         | Error message                                           |
|-----------------------------|--------------------------------------------------------------|---------------------------------------------------------|
| Transcription API Key       | Must not be empty                                            | `Transcription API key is required.`                    |
| Transcription API Key       | Must start with `sk-` (basic format check)                   | `API key should start with "sk-". Check your key.`      |
| Summarization API Key       | Must not be empty (only when Provider is OpenRouter AND summarization is enabled) | `OpenRouter API key is required when OpenRouter is selected.` |
| Model name                  | Must not be empty (when summarization is enabled)            | `Model name is required.`                               |
| Base URL                    | Must not be empty (when summarization is enabled)            | `Base URL is required.`                                 |
| Base URL                    | Must start with `https://` or `http://`                      | `Base URL must start with https:// or http://`          |

### 6.2 Validation Feedback

When validation fails:

1. The first failing field gets focus.
2. An error label appears directly below the failing field, in red (#CC0000), 9pt font.
3. The error label text is one of the messages from Section 6.1.
4. Only one error is shown at a time (the first one found, top to bottom).
5. The error label disappears when the user modifies the failing field (on `<KeyRelease>` event).

No shaking animations, no message boxes, no sound. Just a quiet red label.

### 6.3 API Key Format Note

We do NOT validate API keys against the actual API. That would require a network call from the Settings dialog, which adds complexity, latency, and a potential failure mode. The key format check (`sk-` prefix) catches the most common mistake (pasting the wrong value). Actual authentication failures are handled at runtime via the existing toast notification flow.

**Update (2026-02-13)**: OpenAI has introduced keys that start with `sk-proj-` and OpenRouter uses `sk-or-`. The `sk-` prefix check covers all these variants. If a provider introduces keys without the `sk-` prefix in the future, this validation should be softened to a warning rather than a hard block.

---

## 7. Config File Integration

### 7.1 Reading

When the dialog opens, it reads the current `config.toml` file and populates all fields. The dialog uses the same `config.py` module's `_get_app_directory()` to locate the file.

For fields that do not yet exist in the current config.toml (e.g., `summarization.provider`, `summarization.model`, `summarization.base_url`, `summarization.api_key`), the dialog uses hardcoded defaults:

| Config key                    | Default value                     |
|-------------------------------|-----------------------------------|
| `summarization.provider`      | `openai`                          |
| `summarization.model`         | `gpt-4o-mini`                     |
| `summarization.base_url`      | `https://api.openai.com/v1`       |
| `summarization.api_key`       | (empty string)                    |

### 7.2 Writing

The dialog writes a complete, clean config.toml file. The file format is:

```toml
# Voice Paste Configuration
# Saved by Settings dialog on 2026-02-13 14:30:05

[api]
openai_api_key = "sk-abc...xyz"

[hotkey]
combination = "ctrl+alt+r"

[summarization]
enabled = true
provider = "openai"
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key = ""

[feedback]
audio_cues = true

[logging]
level = "INFO"
```

Notes:
- The hotkey and log level are preserved from the existing config (they are displayed read-only or not shown in the dialog).
- Comments from the original file are NOT preserved. The dialog writes a clean file with a timestamp comment. This is acceptable because the dialog is now the primary way to edit settings -- users who need comments are power users who will hand-edit anyway.
- File encoding: UTF-8, no BOM.
- The API key is stored as a plain string in the TOML file (same as current behavior). Keyring storage is a future enhancement.

### 7.3 Write Failure

If writing config.toml fails (e.g., file is locked, permissions error):

1. Show an error label at the top of the dialog: `Could not save settings. The config file may be locked or read-only.`
2. The error label is red (#CC0000), appears above the first section.
3. The dialog stays open so the user can try again or Cancel.
4. Log the full error to the log file.

---

## 8. Edge Cases

### 8.1 Dialog Opened During Recording

| Scenario | Behavior |
|----------|----------|
| User right-clicks tray icon while RECORDING | pystray shows the context menu. The "Settings..." item is available. |
| User clicks "Settings..." while RECORDING | Dialog opens normally. Recording continues in the background. The hotkey still works. |
| User presses Ctrl+Alt+R while dialog is open | Recording starts/stops normally. Dialog is unaffected. |

**Rationale**: The dialog is non-modal and does not interfere with the hotkey listener. There is no reason to block access to settings during recording.

### 8.2 Dialog Opened with No Config File

| Scenario | Behavior |
|----------|----------|
| config.toml does not exist | This should not happen in practice (main.py creates a template on first run and exits). But if it does: dialog opens with all fields empty/default. Save creates the file. |

### 8.3 Dialog Opened with Corrupted Config

| Scenario | Behavior |
|----------|----------|
| config.toml has TOML syntax errors | The dialog should attempt to read the file. If `tomllib` raises an error, open the dialog with all defaults and show a non-blocking warning label at the top: `Could not read config.toml (syntax error). Showing defaults. Saving will overwrite the file.` Warning label color: orange (#CC6600). |

### 8.4 Config File Changed Externally

| Scenario | Behavior |
|----------|----------|
| User edits config.toml in a text editor while dialog is open | The dialog does NOT detect external changes. It uses the values it loaded when it opened. If the user saves from the dialog, external changes are overwritten. This is acceptable -- the user should not be editing both simultaneously. |

### 8.5 Multiple Rapid Save Clicks

| Scenario | Behavior |
|----------|----------|
| User clicks Save multiple times quickly | Debounce: disable the Save button for 500ms after click. The first click processes normally. Subsequent clicks within 500ms are ignored. |

### 8.6 Very Long API Key

| Scenario | Behavior |
|----------|----------|
| Key exceeds 256 characters | Truncate to 256 characters silently on paste. The entry widget's `validate` command handles this. |

### 8.7 Paste into API Key Field

| Scenario | Behavior |
|----------|----------|
| User pastes a key containing leading/trailing whitespace | Whitespace is stripped on Save (not on paste, to avoid confusing the user mid-edit). |

### 8.8 App Quit While Dialog Is Open

| Scenario | Behavior |
|----------|----------|
| User clicks Quit in tray menu while dialog is open | The dialog is destroyed as part of app shutdown. No save prompt. Unsaved changes are lost. This is acceptable -- Quit means quit. |

---

## 9. Tray Menu Integration

### 9.1 Updated Context Menu

The tray context menu gains a new "Settings..." item:

```
Right-click tray icon:
+------------------------+
| Status: Idle           |    (greyed out, informational)
+------------------------+
| Settings...            |
+------------------------+
| Quit                   |
+------------------------+
```

### 9.2 Menu Item Specification

| Property    | Value                                        |
|-------------|----------------------------------------------|
| Label       | `Settings...`                                |
| Position    | After the status line, before the separator  |
| Enabled     | Always (even during RECORDING or PROCESSING) |
| Ellipsis    | Yes (the `...` follows Windows convention for menu items that open a dialog) |

---

## 10. Keyboard Navigation

| Key       | Behavior                                                   |
|-----------|------------------------------------------------------------|
| Tab       | Move focus forward through fields (standard tkinter order) |
| Shift+Tab | Move focus backward through fields                         |
| Escape    | Close dialog without saving (same as Cancel click)         |
| Enter     | Trigger Save (when focus is not in a multi-line field -- there are none in this dialog, so Enter always saves) |
| Space     | Toggle checkbox (when checkbox is focused)                 |
| Alt+S     | Accelerator for Save (underline the S in Save label)       |
| Alt+C     | Accelerator for Cancel (underline the C in Cancel label)   |

---

## 11. Typography and Visual Style

The dialog uses the default `ttk` theme (`'clam'` on Linux, native on Windows). No custom fonts or colors except where specified.

| Element            | Font / Style                                          |
|--------------------|-------------------------------------------------------|
| Section headers    | `LabelFrame` label, default ttk font (typically 9pt Segoe UI on Windows) |
| Field labels       | Default ttk font, regular weight                       |
| Entry fields       | Default ttk entry font                                 |
| Hint text          | 1pt smaller than default, color #666666                |
| Error text         | Same size as hint, color #CC0000, regular weight       |
| Warning text       | Same size as hint, color #CC6600, regular weight       |
| Buttons            | Default ttk button font                                |

No bold, no italics, no custom fonts. This is a utility.

---

## 12. Restart Notification

### 12.1 Why Restart?

Changing settings requires a restart because:
- The OpenAI client is initialized once with the API key at startup.
- The summarizer instance (CloudLLM vs Passthrough) is chosen at startup.
- Hot-reloading would require thread-safe re-initialization of multiple components.
- Settings changes are rare (once during initial setup, then almost never).

The cost-benefit does not justify hot-reload complexity.

### 12.2 Notification Spec

| Property   | Value                                                       |
|------------|-------------------------------------------------------------|
| Type       | Toast notification (pystray `icon.notify`)                  |
| Title      | `Voice Paste`                                               |
| Body       | `Settings saved. Restart Voice Paste to apply changes.`     |
| Timing     | Shown immediately after successful save                     |
| Duration   | 5 seconds (Windows default for toast)                       |

---

## 13. Open Questions

1. **TOML writing library**: Python's stdlib has `tomllib` for reading but no writer. The implementation will need either `tomli-w` (third-party) or manual string formatting. UX impact: none, but the developer should choose. Manual formatting is recommended to avoid a new dependency.

2. **Keyring for API key storage**: Currently keys are stored in plaintext in config.toml. A future enhancement could use `keyring` to store keys in Windows Credential Manager. The Settings dialog design already supports this (the masking behavior prevents accidental exposure). If keyring is added later, the dialog logic changes only in the read/write layer, not the UI.

3. **Hotkey capture widget**: The hotkey field is read-only in this version. A future version could add a "Press new hotkey..." capture widget. This is non-trivial UX (conflict detection, modifier-only prevention, display formatting) and is explicitly deferred.

4. **Log level dropdown**: Not included in this version. Power users who need DEBUG logging can edit config.toml. Adding a log level dropdown adds visual clutter for zero benefit to 99% of users.

---

## 14. Principle Compliance Check

| Principle                | Status  | Notes                                             |
|--------------------------|---------|---------------------------------------------------|
| Invisible by default     | PASS    | Dialog only appears on explicit user action.       |
| Instant feedback         | PASS    | Validation errors appear immediately on Save.      |
| Zero learning curve      | PASS    | Standard form layout. No novel interaction patterns. |
| Graceful failure         | PASS    | Config read/write errors shown inline. No silent failures. |
| Respect the workflow     | PASS    | Non-modal. Hotkey works while dialog is open. No clipboard changes. Escape closes without saving. |

---

## 15. Implementation Guidance for Developers

### 15.1 File Structure

Create a new file: `src/settings_dialog.py`. This module should:
- Expose a single function: `open_settings_dialog(config: AppConfig, tray: TrayManager) -> None`
- Handle the singleton check internally (track whether a dialog is already open)
- Use `threading` to run tkinter on a separate thread if needed (pystray owns the main thread)

### 15.2 Config Changes Required

The `AppConfig` dataclass and `config.toml` template in `config.py` need these new fields:

```python
# In AppConfig dataclass:
summarization_provider: str = "openai"       # "openai" or "openrouter"
summarization_model: str = "gpt-4o-mini"
summarization_base_url: str = "https://api.openai.com/v1"
summarization_api_key: str = ""              # Only used for OpenRouter
```

### 15.3 Tray Menu Changes

In `tray.py`, add the "Settings..." menu item to `_build_menu()`:

```python
pystray.MenuItem("Settings...", self._handle_settings),
```

The `_handle_settings` callback calls `open_settings_dialog()`.

### 15.4 tkinter Threading Note

pystray runs its own Win32 message loop on the main thread. tkinter also wants to run its own message loop. These cannot share a thread. The recommended approach:

- Run tkinter's `Tk()` / `Toplevel()` on a dedicated daemon thread.
- Use `root.mainloop()` on that thread.
- Use `root.after()` for any cross-thread communication.

Alternatively, use tkinter's `Tk()` as a standalone window (no mainloop integration with pystray). Since the dialog is short-lived and user-initiated, a simple blocking approach on a daemon thread is acceptable.

### 15.5 Provider Defaults Table

For the developer to reference when implementing provider switching:

| Provider    | Model Default       | Base URL Default                  |
|-------------|---------------------|-----------------------------------|
| OpenAI      | `gpt-4o-mini`       | `https://api.openai.com/v1`      |
| OpenRouter  | `openai/gpt-4o-mini`| `https://openrouter.ai/api/v1`   |

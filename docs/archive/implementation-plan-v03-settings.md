# Implementation Plan: v0.3 Settings & Secure Credential Storage

**Date**: 2026-02-13
**Author**: Solution Architect
**Status**: PLAN (not yet implemented)
**Scope**: US-CFG-01 through US-CFG-07

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [File-by-File Change Plan](#2-file-by-file-change-plan)
3. [New Module: `src/keyring_store.py`](#3-new-module-srckeyring_storepy)
4. [Config Evolution: `src/config.py`](#4-config-evolution-srcconfigpy)
5. [Settings Dialog Architecture](#5-settings-dialog-architecture)
6. [Tray Integration: `src/tray.py`](#6-tray-integration-srctraypy)
7. [Hot-Reload Mechanism](#7-hot-reload-mechanism)
8. [Migration Logic](#8-migration-logic)
9. [OpenRouter Integration](#9-openrouter-integration)
10. [Constants Changes: `src/constants.py`](#10-constants-changes-srcconstantspy)
11. [Main Orchestrator Changes: `src/main.py`](#11-main-orchestrator-changes-srcmainpy)
12. [Dependency Changes](#12-dependency-changes)
13. [Implementation Order](#13-implementation-order)
14. [Test Plan](#14-test-plan)
15. [Risk Register](#15-risk-register)

---

## 1. Architecture Overview

### Current Architecture (v0.2)

```
main.py
  |-- AppConfig (frozen dataclass, loaded once from config.toml)
  |-- CloudWhisperSTT(api_key)         -- OpenAI client, fixed config
  |-- CloudLLMSummarizer(api_key)      -- OpenAI client, fixed config
  |-- TrayManager(on_quit, hotkey_label)
  |-- HotkeyManager(hotkey)
  |-- AudioRecorder
```

All configuration is immutable. The OpenAI API key lives in config.toml as
plaintext. There is no UI for editing settings. The summarizer is hardcoded
to OpenAI GPT-4o-mini. No provider abstraction exists.

### Target Architecture (v0.3)

```
main.py
  |-- AppConfig (mutable dataclass, writable back to config.toml)
  |       |-- reads non-secret fields from config.toml
  |       |-- reads secrets from KeyringStore (fallback: config.toml)
  |
  |-- KeyringStore
  |       |-- get/set/delete credentials in Windows Credential Manager
  |       |-- service name: "VoicePaste"
  |       |-- graceful fallback if keyring unavailable
  |
  |-- CloudWhisperSTT(api_key)         -- always OpenAI Whisper
  |-- CloudLLMSummarizer(api_key, model, base_url)
  |       |-- works with OpenAI OR OpenRouter via base_url
  |
  |-- TrayManager(on_quit, on_settings, hotkey_label, get_state)
  |       |-- "Settings..." menu item (disabled during RECORDING/PROCESSING)
  |       |-- spawns SettingsDialog on a dedicated tkinter thread
  |
  |-- SettingsDialog (tkinter.Toplevel on a dedicated thread)
  |       |-- reads/writes AppConfig + KeyringStore
  |       |-- calls on_save callback for hot-reload
  |
  |-- HotkeyManager(hotkey)
  |-- AudioRecorder
```

### Key Architectural Decisions

**ADR-1: tkinter on a dedicated thread, not the main thread.**
pystray owns the main thread (it runs a Win32 message pump via
`Shell_NotifyIcon`). tkinter also needs a message loop (`mainloop`).
These two loops CANNOT share one thread. The solution: spawn a new
daemon thread that creates a `tkinter.Tk` root (withdrawn/hidden),
opens a `Toplevel` dialog, runs `mainloop`, and destroys the root
when the dialog closes. Only one such thread exists at a time
(single-instance guard via a threading.Lock).

**ADR-2: AppConfig becomes non-frozen (mutable).**
The current `frozen=True` dataclass prevents any field modification.
To support hot-reload of settings, we change to `frozen=False`. The
tradeoff is loss of accidental-mutation protection, but the gain is
avoiding the complexity of "replace the entire AppConfig object and
propagate the new reference to all components."

**ADR-3: keyring with fallback to config.toml.**
If `keyring.get_password` raises or returns None and config.toml has
a non-empty key, we use that. This handles:
- First run (no keyring entry yet, key in config.toml)
- Environments where keyring is broken (e.g., headless, locked vault)
- User preference to keep key in config.toml (documented escape hatch)

**ADR-4: OpenRouter via OpenAI client's base_url parameter.**
The `openai.OpenAI(base_url=...)` parameter redirects all API calls
to a different endpoint. OpenRouter is API-compatible with OpenAI's
chat completions endpoint. This means zero code changes to the
summarizer's HTTP logic -- only the client constructor changes.
STT always uses `api.openai.com` (OpenRouter does not offer Whisper).

---

## 2. File-by-File Change Plan

### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `src/keyring_store.py` | Credential CRUD via keyring library | ~120 |
| `src/settings_dialog.py` | tkinter settings UI + thread management | ~350 |
| `tests/test_keyring_store.py` | Unit tests for keyring store | ~150 |
| `tests/test_settings_dialog.py` | Unit tests for settings dialog | ~100 |

### Modified Files

| File | Nature of Changes |
|------|-------------------|
| `src/config.py` | Add provider/model/base_url fields; remove `frozen=True`; add `save()` method; integrate keyring loading; add migration |
| `src/constants.py` | Add provider enum, default base URLs, keyring service name |
| `src/summarizer.py` | Accept `base_url` parameter in `CloudLLMSummarizer.__init__` |
| `src/tray.py` | Add "Settings..." menu item; accept `on_settings` + `get_state` callbacks; disable Settings during RECORDING/PROCESSING |
| `src/main.py` | Wire up settings callback; implement hot-reload; pass new config fields to summarizer |
| `requirements.txt` | Add `keyring` dependency |

### Unchanged Files

| File | Reason |
|------|--------|
| `src/audio.py` | No config changes affect audio capture |
| `src/hotkey.py` | Hotkey is not hot-reloadable (requires restart) |
| `src/paste.py` | No changes needed |
| `src/notifications.py` | No changes needed |
| `src/stt.py` | STT always uses OpenAI; api_key reload handled by main.py recreating the client |

---

## 3. New Module: `src/keyring_store.py`

### Design

```python
"""Secure credential storage via Windows Credential Manager (keyring).

Uses the `keyring` library to store and retrieve API keys securely.
Falls back gracefully if keyring is unavailable.

Service name: "VoicePaste"
Key names: "openai_api_key", "openrouter_api_key"
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "VoicePaste"

# Sentinel to track whether keyring is functional
_keyring_available: Optional[bool] = None


def is_available() -> bool:
    """Check if keyring backend is functional.

    Tests by attempting a no-op read. Caches the result after first call.

    Returns:
        True if keyring is usable, False otherwise.
    """
    ...


def get_credential(key: str) -> Optional[str]:
    """Retrieve a credential from the keyring.

    Args:
        key: Credential identifier (e.g., "openai_api_key").

    Returns:
        The stored credential string, or None if not found or keyring
        is unavailable.
    """
    ...


def set_credential(key: str, value: str) -> bool:
    """Store a credential in the keyring.

    Args:
        key: Credential identifier.
        value: The secret value to store.

    Returns:
        True if stored successfully, False otherwise.
    """
    ...


def delete_credential(key: str) -> bool:
    """Delete a credential from the keyring.

    Args:
        key: Credential identifier.

    Returns:
        True if deleted (or did not exist), False on error.
    """
    ...
```

### Key Implementation Details

1. **Availability check**: Call `keyring.get_password(KEYRING_SERVICE, "__probe__")`
   inside a try/except on import. If it raises `keyring.errors.NoKeyringError`
   or any `Exception`, mark keyring as unavailable. Cache the result in
   `_keyring_available` module-level variable.

2. **Lazy import**: `import keyring` inside each function (not at module level)
   so that if keyring is not installed, the module still loads and
   `is_available()` returns False cleanly.

3. **Never log credential values**: Only log success/failure and key names.

4. **Thread safety**: `keyring` operations are inherently thread-safe (they
   call into the Windows Credential Manager API which is process-global).
   No additional locking needed.

---

## 4. Config Evolution: `src/config.py`

### AppConfig Field Changes

```python
@dataclass  # NOTE: frozen=False (was frozen=True)
class AppConfig:
    # --- Existing fields (unchanged semantics) ---
    openai_api_key: str = ""
    hotkey: str = DEFAULT_HOTKEY
    log_level: str = "INFO"
    summarization_enabled: bool = True
    audio_cues_enabled: bool = True
    app_directory: Path = field(default_factory=_get_app_directory)

    # --- New fields (v0.3) ---
    openrouter_api_key: str = ""
    summarization_provider: str = "openai"        # "openai" | "openrouter"
    summarization_model: str = SUMMARIZE_MODEL     # e.g., "gpt-4o-mini"
    summarization_base_url: str = ""               # empty = use provider default
```

### Removing `frozen=True`

The `@dataclass(frozen=True)` decorator must change to `@dataclass`. This is
necessary because:
- The settings dialog writes back to config fields.
- Hot-reload modifies the live config object.

**Mitigation for lost immutability**: We add a `_lock: threading.Lock` field
(excluded from `__eq__` via `field(compare=False, repr=False)`) to protect
concurrent reads/writes during hot-reload. However, since Python's GIL
already protects simple attribute assignments, and our hot-reload is
a controlled single-writer pattern, the lock is primarily for documentation
intent. We will NOT add the lock in the initial implementation to keep
things simple; if concurrency bugs appear, we add it.

**Impact on existing tests**: `test_config.py::TestAppConfig::test_frozen_dataclass`
will need to be updated. This test explicitly checks that `AppConfig` is
frozen. We change it to verify that `AppConfig` is a dataclass (the
important invariant) and add a new test for the `save()` method.

### New `save()` Method

```python
def save_to_toml(self) -> bool:
    """Write non-secret configuration fields back to config.toml.

    Secrets (API keys) are NOT written -- they belong in keyring.
    Only writes fields that have a corresponding TOML section.

    Returns:
        True if file was written successfully, False otherwise.
    """
    ...
```

This method writes a TOML string manually (Python 3.11 has `tomllib` for
reading but no `tomli_w` for writing). We construct the TOML by string
formatting, which is safe for our simple flat-value config structure.

### New `load_config()` Behavior

The load sequence changes from:

```
1. Read config.toml
2. Extract openai_api_key from [api] section
3. If empty, return None (fatal)
```

To:

```
1. Read config.toml for non-secret fields
2. Try keyring for openai_api_key
3. If keyring empty/unavailable, try config.toml [api] section
4. If config.toml has key AND keyring is available, trigger migration
5. Read openrouter_api_key from keyring (no config.toml fallback)
6. Read summarization_provider, model, base_url from [summarization] section
7. If NO api key found anywhere, return None (fatal) -- UNLESS we are in
   "first-run" mode where the settings dialog can be opened to enter a key
```

**Critical change**: In v0.3, a missing API key is NO LONGER fatal at
startup. The app can start with an empty key and show the settings dialog.
The API key is only required when the user actually presses the hotkey to
record. This dramatically improves the first-run experience.

### config.toml Template Update

```toml
# Voice-to-Summary Paste Tool Configuration
# See README.md for full documentation of all options.

# NOTE: API keys are stored securely in Windows Credential Manager.
# Use the Settings dialog (right-click tray icon > Settings) to manage keys.
# If you prefer to store keys in this file, add them below and they will
# be migrated to Credential Manager on next startup.

[api]
# Legacy API key location (migrated to Credential Manager automatically)
# openai_api_key = ""

[hotkey]
# Global hotkey to start/stop recording (default: "ctrl+alt+r")
combination = "ctrl+alt+r"

[summarization]
# Enable text cleanup and summarization (default: true)
enabled = true
# Provider: "openai" or "openrouter" (default: "openai")
provider = "openai"
# Model name (default: "gpt-4o-mini")
model = "gpt-4o-mini"
# Custom base URL (leave empty to use provider default)
# For OpenRouter: "https://openrouter.ai/api/v1"
base_url = ""

[feedback]
# Play audio cues on recording start/stop (default: true)
audio_cues = true

[logging]
# Log level: DEBUG, INFO, WARNING, ERROR
level = "INFO"
```

### Computed Properties

```python
@property
def active_summarization_api_key(self) -> str:
    """Return the API key for the configured summarization provider."""
    if self.summarization_provider == "openrouter":
        return self.openrouter_api_key
    return self.openai_api_key

@property
def active_summarization_base_url(self) -> str | None:
    """Return the base URL for the summarization provider.

    Returns None to use the openai library default (api.openai.com).
    """
    if self.summarization_base_url:
        return self.summarization_base_url
    if self.summarization_provider == "openrouter":
        return OPENROUTER_DEFAULT_BASE_URL
    return None  # Use openai library default
```

---

## 5. Settings Dialog Architecture

### The Threading Problem

This is the most architecturally sensitive part of the implementation.

**pystray threading model**: On Windows, `pystray.Icon.run()` calls
`Icon._run_win32()` which creates a hidden window, registers a window
class, and enters a Win32 message loop (`GetMessage`/`DispatchMessage`).
This loop MUST run on the thread that created the window. In our app,
this is the main thread.

**tkinter threading model**: tkinter's `Tk()` constructor creates a Tcl
interpreter. The Tcl interpreter is NOT thread-safe -- all Tcl/Tk calls
must happen on the thread that created the `Tk` instance. `mainloop()`
runs the Tcl event loop on that thread.

**Consequence**: We cannot create a tkinter `Tk` on the main thread
(pystray owns it) or call tkinter from the main thread. We must create
a SEPARATE thread that:
1. Creates its own `Tk` instance.
2. Opens the settings dialog as a `Toplevel`.
3. Runs `mainloop()`.
4. Destroys `Tk` when the dialog closes.

### Thread Lifecycle

```
Main Thread (pystray)           Settings Thread (tkinter)
=========================       ==============================
User clicks "Settings..."
  |
  v
on_settings() called by
pystray from main thread
  |
  v
Check: is _settings_lock
acquired? If yes, return
(dialog already open).
  |
  v
Spawn daemon Thread:
settings-dialog-thread  ------> Thread starts
                                  |
                                  v
                                root = tk.Tk()
                                root.withdraw()  # hide root
                                  |
                                  v
                                dialog = SettingsDialog(root, config, keyring_store, on_save)
                                dialog.protocol("WM_DELETE_WINDOW", on_close)
                                  |
                                  v
                                root.mainloop()  # blocks until root.quit()
                                  |
                                  v
                                root.destroy()
                                  |
                                  v
                                Release _settings_lock
                                Thread exits
```

### Single-Instance Guard

```python
_settings_lock = threading.Lock()
_settings_thread: threading.Thread | None = None

def open_settings_dialog(config: AppConfig, keyring_store, on_save: Callable) -> bool:
    """Open the settings dialog on a dedicated tkinter thread.

    Returns False if a dialog is already open (single-instance).
    """
    if not _settings_lock.acquire(blocking=False):
        logger.info("Settings dialog already open.")
        return False

    def _run_dialog():
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            dialog = SettingsDialog(root, config, keyring_store, on_save)

            def _on_close():
                root.quit()

            dialog.protocol("WM_DELETE_WINDOW", _on_close)
            root.mainloop()
            root.destroy()
        except Exception:
            logger.exception("Settings dialog error.")
        finally:
            _settings_lock.release()

    thread = threading.Thread(target=_run_dialog, daemon=True, name="settings-dialog")
    thread.start()
    return True
```

### Dialog Layout

```
+--------------------------------------------------+
|  Voice Paste Settings                        [X]  |
+--------------------------------------------------+
|                                                    |
|  -- API Keys -----------------------------------  |
|                                                    |
|  OpenAI API Key:                                   |
|  [********cdef                    ] [Show] [Test]  |
|                                                    |
|  OpenRouter API Key (optional):                    |
|  [                                ] [Show] [Test]  |
|                                                    |
|  Keys are stored in Windows Credential Manager.    |
|                                                    |
|  -- Summarization ------------------------------  |
|                                                    |
|  Provider:  ( ) OpenAI   ( ) OpenRouter            |
|                                                    |
|  Model:     [gpt-4o-mini                  ]        |
|                                                    |
|  Base URL:  [                             ]        |
|             (Leave empty for provider default)     |
|                                                    |
|  -- General ------------------------------------  |
|                                                    |
|  [x] Enable summarization                         |
|  [x] Enable audio cues                            |
|                                                    |
|  +----------+    +---------+                       |
|  |   Save   |    |  Cancel |                       |
|  +----------+    +---------+                       |
+--------------------------------------------------+
```

### SettingsDialog Class (Skeleton)

```python
class SettingsDialog(tk.Toplevel):
    """Settings dialog for Voice Paste configuration.

    Created on a dedicated tkinter thread. Reads current config and
    keyring values on open, writes them back on Save.

    All tkinter widget operations happen on the tkinter thread that
    owns this dialog's Tk root. No cross-thread tkinter calls.
    """

    def __init__(
        self,
        parent: tk.Tk,
        config: AppConfig,
        keyring_store: ModuleType,  # the keyring_store module
        on_save: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(parent)
        self.title("Voice Paste Settings")
        self.resizable(False, False)
        self._config = config
        self._keyring_store = keyring_store
        self._on_save = on_save

        # Track show/hide state for password fields
        self._openai_key_visible = False
        self._openrouter_key_visible = False

        self._build_ui()
        self._populate_from_config()

        # Center on screen
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"+{x}+{y}")

        # Make dialog modal-like (grab focus, stay on top)
        self.attributes("-topmost", True)
        self.focus_force()
        self.grab_set()

    def _build_ui(self) -> None:
        """Build all UI widgets."""
        ...

    def _populate_from_config(self) -> None:
        """Fill widget values from current config and keyring."""
        ...

    def _on_save_clicked(self) -> None:
        """Handle Save button click.

        Validates inputs, stores secrets in keyring, stores non-secrets
        in config.toml, and calls the on_save callback with changed values.
        """
        ...

    def _on_cancel_clicked(self) -> None:
        """Handle Cancel button click. Close without saving."""
        self.master.quit()

    def _toggle_key_visibility(self, field: str) -> None:
        """Toggle between showing and hiding an API key field."""
        ...

    def _test_api_key(self, provider: str) -> None:
        """Test an API key by making a lightweight API call.

        Runs in a background thread to avoid freezing the dialog.
        Shows result via a tkinter messagebox.
        """
        ...
```

### API Key Test Implementation

The "Test" button makes a lightweight API call to verify the key works:

- **OpenAI**: `client.models.list()` (minimal cost, fast response)
- **OpenRouter**: `GET https://openrouter.ai/api/v1/models` with auth header

The test runs in a daemon thread. On completion, it uses `self.after(0, ...)`
to schedule the result display back on the tkinter thread:

```python
def _test_api_key(self, provider: str) -> None:
    key = self._get_key_for_provider(provider)
    if not key:
        messagebox.showwarning("Test", "No API key entered.", parent=self)
        return

    def _do_test():
        try:
            if provider == "openai":
                client = openai.OpenAI(api_key=key, timeout=10)
                client.models.list()
                self.after(0, lambda: messagebox.showinfo(
                    "Test", "OpenAI API key is valid.", parent=self
                ))
            elif provider == "openrouter":
                client = openai.OpenAI(
                    api_key=key,
                    base_url="https://openrouter.ai/api/v1",
                    timeout=10,
                )
                client.models.list()
                self.after(0, lambda: messagebox.showinfo(
                    "Test", "OpenRouter API key is valid.", parent=self
                ))
        except openai.AuthenticationError:
            self.after(0, lambda: messagebox.showerror(
                "Test", "Authentication failed. Key is invalid.", parent=self
            ))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                "Test", f"Connection error: {type(e).__name__}", parent=self
            ))

    threading.Thread(target=_do_test, daemon=True, name="key-test").start()
```

---

## 6. Tray Integration: `src/tray.py`

### Changes to `TrayManager.__init__`

```python
def __init__(
    self,
    on_quit: Optional[Callable[[], None]] = None,
    on_settings: Optional[Callable[[], None]] = None,  # NEW
    hotkey_label: str = "Ctrl+Alt+R",
    get_state: Optional[Callable[[], AppState]] = None,  # NEW
) -> None:
```

- `on_settings`: Callback invoked when "Settings..." is clicked. This is
  called by pystray from a pystray worker thread (NOT the main thread).
  The callback must be safe to call from any thread.

- `get_state`: Callable that returns the current AppState, used to
  determine whether the Settings menu item should be enabled or disabled.

### Changes to `_build_menu`

```python
def _build_menu(self) -> pystray.Menu:
    return pystray.Menu(
        # Hidden default action (existing)
        pystray.MenuItem(
            "Open",
            self._handle_default_action,
            default=True,
            visible=False,
        ),
        # Status display (existing)
        pystray.MenuItem(
            lambda _: self._get_status_text(),
            None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        # NEW: Settings menu item
        pystray.MenuItem(
            "Settings...",
            self._handle_settings,
            enabled=lambda _: self._is_settings_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", self._handle_quit),
    )
```

### Settings Enablement Logic

```python
def _is_settings_enabled(self) -> bool:
    """Settings menu is disabled during RECORDING and PROCESSING.

    Uses the get_state callback to check current state without
    needing a direct reference to the app's state variable.
    """
    if self._get_state is None:
        return True
    state = self._get_state()
    return state in (AppState.IDLE, AppState.PASTING)
```

**Why also allow PASTING?** The PASTING state is extremely brief (< 200ms).
By the time the user navigates the context menu and clicks "Settings...",
the state will have returned to IDLE. Disabling during PASTING would cause
a confusing flicker in the menu enablement.

### pystray Menu Item Enabled Callback

pystray supports dynamic `enabled` via a callable. The callable receives
the menu item as its argument and returns a bool. pystray calls it each
time the menu is about to be displayed. This is evaluated on pystray's
internal thread, which is safe for our `_is_settings_enabled()` since
it only reads the state via a thread-safe callback.

---

## 7. Hot-Reload Mechanism

### What Can Be Hot-Reloaded

| Setting | Hot-Reloadable? | Mechanism |
|---------|----------------|-----------|
| OpenAI API key | YES | Recreate STT + Summarizer clients |
| OpenRouter API key | YES | Recreate Summarizer client |
| Summarization provider | YES | Recreate Summarizer client |
| Summarization model | YES | Recreate Summarizer client |
| Summarization base_url | YES | Recreate Summarizer client |
| Summarization enabled | YES | Swap Summarizer instance |
| Audio cues enabled | YES | Read from config at cue time |
| Hotkey | NO | Requires re-registering with keyboard library; documented as restart-required |
| Log level | NO | Low priority; restart-required |

### Hot-Reload Flow

```
SettingsDialog._on_save_clicked()
    |
    v
1. Write secrets to keyring via keyring_store.set_credential()
2. Update config fields: config.openai_api_key = new_key, etc.
3. Write non-secrets to config.toml via config.save_to_toml()
4. Call on_save(changed_fields: dict) callback
    |
    v
VoicePasteApp._on_settings_saved(changed_fields)
    |
    v
5. If api keys or provider/model/base_url changed:
   a. Recreate self._stt = CloudWhisperSTT(api_key=config.openai_api_key)
   b. Recreate self._summarizer based on provider:
      - If provider == "openai":
          CloudLLMSummarizer(api_key=config.openai_api_key,
                             model=config.summarization_model,
                             base_url=config.active_summarization_base_url)
      - If provider == "openrouter":
          CloudLLMSummarizer(api_key=config.openrouter_api_key,
                             model=config.summarization_model,
                             base_url=config.active_summarization_base_url)
      - If summarization disabled:
          PassthroughSummarizer()
6. Log the change at INFO level (without secrets)
```

### Thread Safety During Hot-Reload

The `_on_settings_saved` method is called from the tkinter thread (via the
`on_save` callback). It mutates `self._stt` and `self._summarizer`. These
references are read by the pipeline worker thread in `_run_pipeline`.

**Race condition window**: If a pipeline is running (PROCESSING state) at the
exact moment settings are saved, the pipeline thread may hold a reference to
the old STT/summarizer objects. This is safe because:
1. The settings menu is DISABLED during PROCESSING state.
2. Even if the user somehow triggers a save during processing (e.g., dialog
   was opened before recording started), the old objects remain valid until
   garbage collected. The pipeline will complete with the old config and
   the next pipeline run will use the new objects.

Therefore, no lock is needed for the hot-reload. Simple attribute assignment
under the GIL is atomic for reference replacement.

---

## 8. Migration Logic

### Trigger Conditions

Migration runs during `load_config()` if ALL of these are true:
1. `keyring_store.is_available()` returns True.
2. config.toml contains a non-empty `openai_api_key` in the `[api]` section.
3. `keyring_store.get_credential("openai_api_key")` returns None (not yet migrated).

### Step-by-Step Flow

```
load_config()
    |
    v
1. Read config.toml as normal
2. Extract api_key from [api] section
3. Check: is keyring available?
   |
   +-- No --> Use api_key from config.toml (legacy mode). DONE.
   |
   +-- Yes --> Check: does keyring already have "openai_api_key"?
       |
       +-- Yes --> Use keyring value. Ignore config.toml value.
       |           Log: "API key loaded from Credential Manager."
       |
       +-- No --> Is config.toml api_key non-empty?
           |
           +-- No --> No key anywhere. App may start without key
           |          (settings dialog can be used to enter one).
           |
           +-- Yes --> MIGRATE:
               a. keyring_store.set_credential("openai_api_key", api_key)
               b. If migration succeeded:
                  - Remove/comment out api_key from config.toml
                  - Log: "API key migrated from config.toml to
                          Credential Manager."
               c. If migration failed:
                  - Keep using config.toml value
                  - Log warning: "Could not migrate API key to
                    Credential Manager. Key remains in config.toml."
```

### Removing Key from config.toml After Migration

After successful migration, we rewrite config.toml with the `openai_api_key`
line commented out:

```python
def _remove_api_key_from_toml(config_path: Path) -> bool:
    """Comment out the openai_api_key line in config.toml after migration."""
    try:
        content = config_path.read_text(encoding="utf-8")
        # Replace the line, preserving the structure
        new_content = content.replace(
            'openai_api_key = "',
            '# MIGRATED to Credential Manager\n# openai_api_key = "',
            1,  # Only first occurrence
        )
        if new_content != content:
            config_path.write_text(new_content, encoding="utf-8")
            return True
        return False
    except OSError:
        return False
```

This approach is safe because:
- It only modifies the specific line, not the entire file structure.
- The `# MIGRATED` comment makes the change visible and reversible.
- If the file write fails, the migration still succeeded (key is in keyring)
  and the duplicate in config.toml is harmless (keyring takes priority).

---

## 9. OpenRouter Integration

### How It Works

OpenRouter provides an OpenAI-compatible API at `https://openrouter.ai/api/v1`.
The `openai` Python SDK's `OpenAI(base_url=...)` parameter redirects ALL
requests to that base URL. OpenRouter supports the `/chat/completions`
endpoint with the same request/response format as OpenAI.

### Client Construction

```python
# Current (v0.2) -- OpenAI only:
self._client = openai.OpenAI(api_key=api_key, timeout=timeout)

# New (v0.3) -- with optional base_url:
self._client = openai.OpenAI(
    api_key=api_key,
    base_url=base_url,  # None = default (api.openai.com)
    timeout=timeout,
)
```

When `base_url` is None (or not provided), the openai library uses its
default: `https://api.openai.com/v1`. This is the zero-change path for
OpenAI users.

### Changes to `CloudLLMSummarizer.__init__`

```python
def __init__(
    self,
    api_key: str,
    model: str = SUMMARIZE_MODEL,
    temperature: float = SUMMARIZE_TEMPERATURE,
    max_tokens: int = SUMMARIZE_MAX_TOKENS,
    timeout: int = SUMMARIZE_TIMEOUT_SECONDS,
    system_prompt: str = SUMMARIZE_SYSTEM_PROMPT,
    base_url: str | None = None,  # NEW
) -> None:
    client_kwargs = {
        "api_key": api_key,
        "timeout": timeout,
    }
    if base_url:
        client_kwargs["base_url"] = base_url

    self._client = openai.OpenAI(**client_kwargs)
    ...
```

### STT Client (No Change)

The STT client (`CloudWhisperSTT`) always uses `api.openai.com` because:
- OpenRouter does not offer a Whisper/transcription endpoint.
- Deepgram and other STT providers have incompatible APIs.
- Keeping STT on OpenAI simplifies the architecture.

The STT client is recreated during hot-reload with the (possibly updated)
`openai_api_key` from config. It never receives a custom `base_url`.

### OpenRouter-Specific Considerations

1. **Model names**: OpenRouter uses its own model identifiers, e.g.,
   `openai/gpt-4o-mini`, `anthropic/claude-3-haiku`. The user must enter
   the correct model name for their chosen provider. The settings dialog
   placeholder text will indicate this.

2. **Rate limits and pricing**: Different from OpenAI. Not our concern, but
   worth documenting in the README.

3. **Required headers**: OpenRouter recommends setting `HTTP-Referer` and
   `X-Title` headers. The `openai` SDK allows custom headers via
   `default_headers`. We set these in the client constructor:
   ```python
   if base_url and "openrouter.ai" in base_url:
       client_kwargs["default_headers"] = {
           "HTTP-Referer": "https://github.com/voice-paste",
           "X-Title": "Voice Paste",
       }
   ```

---

## 10. Constants Changes: `src/constants.py`

### New Constants

```python
# Keyring configuration (v0.3)
KEYRING_SERVICE_NAME = "VoicePaste"
KEYRING_OPENAI_KEY = "openai_api_key"
KEYRING_OPENROUTER_KEY = "openrouter_api_key"

# Provider defaults (v0.3)
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Summarization provider options (v0.3)
SUMMARIZATION_PROVIDERS = ("openai", "openrouter")
DEFAULT_SUMMARIZATION_PROVIDER = "openai"
```

We intentionally do NOT use an Enum for providers. A simple string with
a tuple of valid values keeps the config.toml format human-readable and
avoids serialization complexity.

---

## 11. Main Orchestrator Changes: `src/main.py`

### VoicePasteApp.__init__ Changes

```python
def __init__(self, config: AppConfig) -> None:
    self.config = config
    self._state = AppState.IDLE
    self._state_lock = threading.Lock()

    # Initialize components
    self._recorder = AudioRecorder(on_auto_stop=self._on_auto_stop)
    self._stt = CloudWhisperSTT(api_key=config.openai_api_key)

    # v0.3: Summarizer uses provider-specific config
    self._rebuild_summarizer()

    self._hotkey_manager = HotkeyManager(hotkey=config.hotkey)

    # v0.3: TrayManager gets settings callback and state accessor
    self._tray_manager = TrayManager(
        on_quit=self._shutdown,
        on_settings=self._open_settings,     # NEW
        hotkey_label=config.hotkey,
        get_state=lambda: self.state,         # NEW
    )

    self._shutdown_event = threading.Event()
    self._pipeline_thread: threading.Thread | None = None
```

### New Methods

```python
def _rebuild_summarizer(self) -> None:
    """(Re)create the summarizer based on current config.

    Called on init and after settings changes.
    """
    if not self.config.summarization_enabled:
        self._summarizer = PassthroughSummarizer()
        logger.info("Summarization disabled (PassthroughSummarizer).")
        return

    api_key = self.config.active_summarization_api_key
    if not api_key:
        self._summarizer = PassthroughSummarizer()
        logger.warning(
            "No API key for summarization provider '%s'. "
            "Using PassthroughSummarizer.",
            self.config.summarization_provider,
        )
        return

    self._summarizer = CloudLLMSummarizer(
        api_key=api_key,
        model=self.config.summarization_model,
        base_url=self.config.active_summarization_base_url,
    )
    logger.info(
        "Summarizer configured: provider=%s, model=%s, base_url=%s",
        self.config.summarization_provider,
        self.config.summarization_model,
        self.config.active_summarization_base_url or "(default)",
    )


def _open_settings(self) -> None:
    """Open the settings dialog. Called from tray menu."""
    from settings_dialog import open_settings_dialog
    import keyring_store

    open_settings_dialog(
        config=self.config,
        keyring_store=keyring_store,
        on_save=self._on_settings_saved,
    )


def _on_settings_saved(self, changed_fields: dict[str, Any]) -> None:
    """Handle settings save. Recreate API clients as needed.

    Called from the tkinter settings thread. Thread-safe because
    we only replace object references (atomic under GIL) and the
    pipeline thread checks are guarded by state.

    Args:
        changed_fields: Dict of field names that were changed.
    """
    logger.info("Settings saved. Changed fields: %s", list(changed_fields.keys()))

    # Determine if STT client needs rebuild
    stt_rebuild_needed = "openai_api_key" in changed_fields

    # Determine if summarizer needs rebuild
    summarizer_keys = {
        "openai_api_key", "openrouter_api_key",
        "summarization_provider", "summarization_model",
        "summarization_base_url", "summarization_enabled",
    }
    summarizer_rebuild_needed = bool(changed_fields.keys() & summarizer_keys)

    if stt_rebuild_needed:
        self._stt = CloudWhisperSTT(api_key=self.config.openai_api_key)
        logger.info("STT client rebuilt with updated API key.")

    if summarizer_rebuild_needed:
        self._rebuild_summarizer()
        logger.info("Summarizer rebuilt with updated settings.")
```

### Startup Flow Change

The `main()` function changes to allow starting without an API key:

```python
# BEFORE (v0.2):
config = load_config()
if config is None:
    _show_fatal_error(...)
    sys.exit(1)

# AFTER (v0.3):
config = load_config()
if config is None:
    # Config could not be read at all (file corrupt, etc.)
    _show_fatal_error(...)
    sys.exit(1)

# API key may be empty -- that is OK in v0.3.
# The user can enter it via Settings dialog.
if not config.openai_api_key:
    logger.warning(
        "No OpenAI API key configured. Use Settings to add one."
    )
```

And in `_on_hotkey()`, when transitioning from IDLE to RECORDING, we check
for a valid API key:

```python
def _start_recording(self) -> None:
    if not self.config.openai_api_key:
        self._show_error(
            "No OpenAI API key configured.\n"
            "Right-click the tray icon > Settings to add your key."
        )
        return
    # ... existing recording start logic
```

---

## 12. Dependency Changes

### requirements.txt Additions

```
# Secure credential storage (v0.3)
keyring==25.6.0             # Windows Credential Manager integration
pywin32-ctypes==0.2.3       # keyring backend dependency on Windows
```

### Why These Versions

- `keyring>=25.0` uses the `WinVaultKeyring` backend by default on Windows,
  which stores credentials in Windows Credential Manager. No configuration
  needed.
- `pywin32-ctypes` is a pure-Python alternative to `pywin32` that keyring
  uses on Windows. It is lighter than full `pywin32` and avoids DLL
  bundling issues with PyInstaller.

### PyInstaller Considerations

- `keyring` uses entry points to discover backends. PyInstaller may not
  collect these automatically. The `.spec` file will need:
  ```python
  hiddenimports=['keyring.backends.Windows']
  ```
- `tkinter` is part of the Python stdlib. When bundled with PyInstaller on
  Windows, it requires the Tcl/Tk runtime files. PyInstaller usually handles
  this automatically, but we should verify in the build.

### No New tkinter Dependency

tkinter is part of the Python standard library. It ships with the official
CPython Windows installer. It does NOT need to be listed in requirements.txt.
However, PyInstaller needs the Tcl/Tk data files -- these are collected
automatically by PyInstaller's tkinter hook.

---

## 13. Implementation Order

The implementation should proceed in this order, with each step being
independently testable:

### Phase 1: Foundation (no UI changes)

**Step 1: `src/keyring_store.py` + `tests/test_keyring_store.py`**
- Implement the keyring abstraction module.
- Write tests with mocked keyring backend.
- Verify: `pytest tests/test_keyring_store.py` passes.

**Step 2: `src/constants.py` updates**
- Add keyring constants, provider constants, OpenRouter base URL.
- No behavior change, just new constants.
- Verify: Existing tests still pass.

**Step 3: `src/config.py` evolution**
- Remove `frozen=True`.
- Add new fields (openrouter_api_key, summarization_provider, model, base_url).
- Add computed properties (active_summarization_api_key, active_summarization_base_url).
- Integrate keyring loading into `load_config()`.
- Add migration logic.
- Add `save_to_toml()` method.
- Update `CONFIG_TEMPLATE`.
- Update `tests/test_config.py` (remove frozen test, add new field tests, add migration tests).
- Verify: `pytest tests/test_config.py` passes.

### Phase 2: API Client Changes

**Step 4: `src/summarizer.py` -- add `base_url` parameter**
- Add `base_url: str | None = None` to `CloudLLMSummarizer.__init__`.
- Pass through to `openai.OpenAI(base_url=...)`.
- Add OpenRouter headers when applicable.
- Update `tests/test_summarizer.py`.
- Verify: `pytest tests/test_summarizer.py` passes.

### Phase 3: Settings Dialog

**Step 5: `src/settings_dialog.py` + `tests/test_settings_dialog.py`**
- Implement the full settings dialog with tkinter.
- Implement `open_settings_dialog()` thread launcher.
- Write tests (dialog construction, field population, save logic).
- Manual test: Dialog opens, displays fields, saves to keyring + config.

### Phase 4: Tray + Main Integration

**Step 6: `src/tray.py` -- add Settings menu item**
- Add `on_settings` and `get_state` parameters.
- Add "Settings..." menu item with dynamic enable/disable.
- Update `tests/test_tray.py`.
- Verify: `pytest tests/test_tray.py` passes.

**Step 7: `src/main.py` -- wire everything together**
- Integrate settings callback, hot-reload, keyring-aware startup.
- Implement `_rebuild_summarizer()`, `_open_settings()`, `_on_settings_saved()`.
- Update startup flow to allow empty API key.
- Add API key check before recording.
- Update `tests/test_state_machine.py`, `tests/test_v02_integration.py`.

### Phase 5: Polish

**Step 8: `requirements.txt` + PyInstaller**
- Add `keyring` and `pywin32-ctypes` to requirements.txt.
- Update `voice_paste.spec` with `hiddenimports=['keyring.backends.Windows']`.
- Test build: `pyinstaller voice_paste.spec`.
- Verify: Built .exe starts, settings dialog opens, keyring works.

**Step 9: End-to-end manual testing**
- Fresh install scenario (no config.toml, no keyring entries).
- Migration scenario (key in config.toml, first run after upgrade).
- OpenRouter scenario (switch provider, enter OpenRouter key, test).
- Settings change during IDLE state.
- Verify settings disabled during RECORDING state.

---

## 14. Test Plan

### Unit Tests

| Test File | What It Covers |
|-----------|---------------|
| `tests/test_keyring_store.py` | get/set/delete credential, availability check, fallback when keyring unavailable |
| `tests/test_config.py` (updated) | New fields, computed properties, save_to_toml(), migration logic, non-frozen dataclass |
| `tests/test_summarizer.py` (updated) | base_url parameter, OpenRouter headers |
| `tests/test_tray.py` (updated) | Settings menu item, enable/disable logic, on_settings callback |
| `tests/test_settings_dialog.py` | Dialog construction, field population, save callback, single-instance guard |

### Integration Tests

| Test | What It Verifies |
|------|-----------------|
| Settings save + hot-reload | STT and summarizer clients are recreated after save |
| Migration from config.toml | Key moves to keyring, config.toml line commented out |
| Provider switch | Switching from OpenAI to OpenRouter changes base_url and model |
| Empty key startup | App starts without key, shows warning, blocks recording |

### Manual Test Checklist

- [ ] Fresh install: No config.toml, no keyring. App creates template, starts, shows settings hint.
- [ ] Enter OpenAI key via settings. Key stored in Windows Credential Manager (verify via `cmdkey /list`).
- [ ] Record + paste works with the newly entered key.
- [ ] Switch to OpenRouter provider. Enter OpenRouter key. Model field updates.
- [ ] Record + paste works with OpenRouter.
- [ ] Settings menu disabled during recording.
- [ ] Double-click tray does not open duplicate settings dialog.
- [ ] Config.toml key migration: Put key in config.toml, start app, verify key in keyring, config.toml line commented out.
- [ ] Kill app, restart. Key still in keyring. Recording works without re-entering key.
- [ ] Built .exe: All above scenarios work in the PyInstaller bundle.

---

## 15. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| tkinter not available in embedded Python | Low | High | Check `import tkinter` at startup; if unavailable, disable Settings menu item and log warning |
| keyring backend not functional (corporate lockdown) | Medium | Medium | Fallback to config.toml is already designed. Log clear warning. |
| PyInstaller fails to bundle tkinter Tcl/Tk files | Medium | High | Test build early (Phase 5 Step 8). Add explicit `datas` in .spec if needed. |
| pystray menu callback threading issues | Low | High | Thoroughly tested: pystray invokes callbacks on its own thread, not main thread. Our `open_settings_dialog` is thread-safe by design. |
| OpenRouter API incompatibility | Low | Medium | OpenRouter is widely used with the openai SDK. If specific endpoints differ, users can fall back to OpenAI. |
| Race condition during hot-reload | Very Low | Medium | Settings disabled during RECORDING/PROCESSING. GIL protects reference assignment. Documented in ADR. |
| config.toml rewrite corrupts file | Low | Medium | `save_to_toml()` writes to a temp file first, then replaces atomically. Include backup `.bak` file. |

---

## Appendix A: File Dependency Graph (v0.3)

```
main.py
  +-- config.py
  |     +-- constants.py
  |     +-- keyring_store.py (NEW)
  |
  +-- audio.py
  |     +-- constants.py
  |
  +-- stt.py
  |     +-- constants.py
  |
  +-- summarizer.py
  |     +-- constants.py
  |
  +-- paste.py
  |     +-- constants.py
  |
  +-- hotkey.py
  |     +-- constants.py
  |
  +-- tray.py
  |     +-- constants.py
  |
  +-- settings_dialog.py (NEW)
  |     +-- config.py
  |     +-- keyring_store.py (NEW)
  |     +-- constants.py
  |
  +-- notifications.py
        +-- constants.py
```

## Appendix B: Sequence Diagram -- Settings Save Flow

```
User           TrayManager     SettingsDialog    KeyringStore   AppConfig   VoicePasteApp
 |                  |                |                |             |             |
 |--right-click---->|                |                |             |             |
 |                  |                |                |             |             |
 |  click Settings  |                |                |             |             |
 |----------------->|                |                |             |             |
 |                  |--on_settings-->|                |             |             |
 |                  |  (spawn thread)|                |             |             |
 |                  |                |                |             |             |
 |                  |          [dialog opens]         |             |             |
 |                  |                |                |             |             |
 |---edit fields--->|                |                |             |             |
 |                  |                |                |             |             |
 |---click Save---->|                |                |             |             |
 |                  |                |--set_credential>             |             |
 |                  |                |                |             |             |
 |                  |                |---update fields------------>|             |
 |                  |                |                |             |             |
 |                  |                |---save_to_toml------------>|             |
 |                  |                |                |             |             |
 |                  |                |---on_save(changed)--------------------->  |
 |                  |                |                |             |             |
 |                  |                |                |             | _rebuild_   |
 |                  |                |                |             | summarizer()|
 |                  |                |                |             |             |
 |                  |          [dialog closes]        |             |             |
 |                  |                |                |             |             |
```

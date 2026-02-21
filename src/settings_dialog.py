"""Settings dialog for Voice Paste configuration.

Opens a tkinter dialog on a dedicated thread (pystray owns the main thread).
Reads current config and keyring values on open, writes them back on Save.

v0.3: Initial implementation.
v0.4: Backend toggle (Cloud / Local), local model controls.

Threading model:
    pystray runs its Win32 message pump on the main thread. tkinter must run
    its own Tcl event loop on a separate thread. This module spawns a daemon
    thread that creates Tk(), opens the dialog, runs mainloop(), and destroys
    the Tk root when the dialog closes. A threading.Lock ensures only one
    dialog can be open at a time (singleton).
"""

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Singleton guard: only one settings dialog at a time
_settings_lock = threading.Lock()

# Provider defaults for the UI
_PROVIDER_DEFAULTS = {
    "openai": {
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    "openrouter": {
        "model": "openai/gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "ollama": {
        "model": "llama3.2",
        "base_url": "http://localhost:11434/v1",
    },
}

# Dark theme color palette (single source of truth for all widgets)
_DARK_COLORS = {
    "bg": "#1c1c1c",
    "fg": "#e0e0e0",
    "field_bg": "#2d2d2d",
    "field_fg": "#e0e0e0",
    "disabled_bg": "#1a1a1a",
    "disabled_fg": "#666666",
    "select_bg": "#4a6984",
    "select_fg": "#ffffff",
    "accent": "#4a90d9",
    "border": "#444444",
    "button_bg": "#333333",
    "button_active": "#444444",
    "insert": "#e0e0e0",
}


def _apply_dark_title_bar(widget) -> None:
    """Apply dark title bar to a tkinter window using the Windows DWM API.

    On Windows 10 build 18985+ and Windows 11, the DWM (Desktop Window
    Manager) controls the title bar color. By default the title bar stays
    light even when the app content is dark. This function calls
    DwmSetWindowAttribute with DWMWA_USE_IMMERSIVE_DARK_MODE (attribute 20)
    to request a dark title bar.

    On Linux this is a no-op (GNOME/KDE handle title bar theming natively).

    Must be called AFTER the window has been created and update_idletasks()
    has run (so that a valid HWND exists).

    Args:
        widget: A tkinter Tk or Toplevel instance.
    """
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(widget.winfo_id())
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 18985+, Windows 11)
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
        )
        logger.debug("Dark title bar applied via DwmSetWindowAttribute.")
    except Exception:
        logger.debug("Could not apply dark title bar (non-fatal).")


def _configure_dark_style(root, ttk_module, sv_ttk_loaded):
    """Configure comprehensive dark theme for all tkinter/ttk widgets.

    When sv_ttk is loaded, the key fix is calling sv_ttk's ``configure_colors``
    Tcl procedure. This procedure calls ``tk_setPalette`` which sets the
    option database for ALL plain tk widgets (tk.Text, tk.Entry, tk.Toplevel
    backgrounds, etc.). Without this call, plain tk widgets retain their
    Windows system colors (SystemButtonFace, SystemWindow) and appear light.

    **Root cause**: sv_ttk binds ``configure_colors`` to the ``<<ThemeChanged>>``
    virtual event, but on Windows this event is only generated when the
    *Windows system theme* changes (light/dark mode toggle in Windows Settings),
    NOT when ``ttk::style theme use`` is called from Python. So the procedure
    never fires automatically. We must call it explicitly.

    Additionally, sv_ttk uses image-based elements (spritesheet PNGs) for
    Entry.field and Combobox.field. This means ``style.configure("TEntry",
    fieldbackground=...)`` is accepted into the style database but IGNORED
    by the rendering engine -- the spritesheet image is always drawn instead
    of any color fill. The ttk Entry/Combobox backgrounds are therefore always
    correct under sv_ttk dark theme. The foreground (text color) is handled
    by the textarea sub-element and works normally via style.configure.

    When sv_ttk is not available, applies a full manual dark theme for all
    ttk widget types.

    Args:
        root: The tk.Tk root instance.
        ttk_module: The ttk module reference.
        sv_ttk_loaded: Whether sv_ttk was successfully loaded.
    """
    c = _DARK_COLORS

    if sv_ttk_loaded:
        # CRITICAL: call sv_ttk's configure_colors proc to trigger
        # tk_setPalette for plain tk widgets. Without this, tk.Text,
        # Toplevel backgrounds, and any raw tk.* widgets keep their
        # Windows system colors (light).
        try:
            root.tk.call("configure_colors")
            logger.debug("sv_ttk configure_colors called (tk_setPalette applied).")
        except Exception:
            logger.debug("configure_colors proc not found, applying manual tk_setPalette.")
            # Fallback: call tk_setPalette directly with sv_ttk's dark colors
            root.tk.call(
                "tk_setPalette",
                "background", c["bg"],
                "foreground", c["fg"],
                "highlightColor", c["select_bg"],
                "selectBackground", c["select_bg"],
                "selectForeground", c["select_fg"],
                "activeBackground", c["select_bg"],
                "activeForeground", c["select_fg"],
            )

        # Combobox dropdown is a plain tk.Listbox -- never themed by sv_ttk.
        # tk_setPalette sets its bg to the main bg (#1c1c1c) but we want
        # a slightly lighter field background for dropdown lists.
        root.option_add("*TCombobox*Listbox.background", c["field_bg"])
        root.option_add("*TCombobox*Listbox.foreground", c["field_fg"])
        root.option_add("*TCombobox*Listbox.selectBackground", c["select_bg"])
        root.option_add("*TCombobox*Listbox.selectForeground", c["select_fg"])

        # Notebook tab styling for sv_ttk
        style = ttk_module.Style()
        style.configure("TNotebook", background=c["bg"])
        style.configure(
            "TNotebook.Tab",
            background=c["button_bg"],
            foreground=c["fg"],
            padding=[12, 4],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", c["bg"]), ("active", c["button_active"])],
            foreground=[("selected", c["fg"])],
        )

    else:
        # No sv_ttk: full manual dark theme for all ttk widget types.
        style = ttk_module.Style()

        style.configure(".", background=c["bg"], foreground=c["fg"])
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabelframe", background=c["bg"])
        style.configure("TLabelframe.Label", background=c["bg"],
                        foreground=c["fg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("TButton", background=c["button_bg"],
                        foreground=c["fg"])
        style.map("TButton",
                  background=[("disabled", c["disabled_bg"]),
                              ("active", c["button_active"]),
                              ("pressed", "#555555")],
                  foreground=[("disabled", c["disabled_fg"])])
        style.configure("TCheckbutton", background=c["bg"],
                        foreground=c["fg"])
        style.map("TCheckbutton",
                  background=[("disabled", c["bg"]),
                              ("active", c["bg"])],
                  foreground=[("disabled", c["disabled_fg"])])
        style.configure("Horizontal.TProgressbar",
                        background=c["accent"], troughcolor=c["button_bg"])
        style.configure("TSeparator", background=c["border"])

        # For non-sv_ttk, Entry/Combobox use the default "field" element
        # which DOES respect fieldbackground (it's color-based, not image).
        style.configure("TEntry",
                        fieldbackground=c["field_bg"],
                        foreground=c["field_fg"],
                        insertcolor=c["insert"])
        style.map("TEntry",
                  fieldbackground=[("readonly", c["field_bg"]),
                                   ("disabled", c["disabled_bg"])],
                  foreground=[("readonly", c["field_fg"]),
                              ("disabled", c["disabled_fg"])])

        style.configure("TCombobox",
                        fieldbackground=c["field_bg"],
                        foreground=c["field_fg"],
                        selectbackground=c["select_bg"],
                        selectforeground=c["select_fg"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["field_bg"]),
                                   ("disabled", c["disabled_bg"])],
                  foreground=[("readonly", c["field_fg"]),
                              ("disabled", c["disabled_fg"])])

        # Notebook tab styling for non-sv_ttk
        style.configure("TNotebook", background=c["bg"])
        style.configure(
            "TNotebook.Tab",
            background=c["button_bg"],
            foreground=c["fg"],
            padding=[12, 4],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", c["bg"]), ("active", c["button_active"])],
            foreground=[("selected", c["fg"])],
        )

        # Combobox dropdown listbox
        root.option_add("*TCombobox*Listbox.background", c["field_bg"])
        root.option_add("*TCombobox*Listbox.foreground", c["field_fg"])
        root.option_add("*TCombobox*Listbox.selectBackground", c["select_bg"])
        root.option_add("*TCombobox*Listbox.selectForeground", c["select_fg"])

        # Manual tk_setPalette for plain tk widgets
        root.tk.call(
            "tk_setPalette",
            "background", c["bg"],
            "foreground", c["fg"],
            "highlightColor", c["select_bg"],
            "selectBackground", c["select_bg"],
            "selectForeground", c["select_fg"],
            "activeBackground", c["select_bg"],
            "activeForeground", c["select_fg"],
        )


def open_settings_dialog(
    config: "AppConfig",
    on_save: Callable[[dict[str, Any]], None],
    on_close: Optional[Callable[[], None]] = None,
) -> bool:
    """Open the settings dialog on a dedicated tkinter thread.

    Args:
        config: Current application configuration.
        on_save: Callback invoked with dict of changed fields after save.
        on_close: Optional callback invoked after the dialog is fully
            destroyed and its Tk root is gone. Use this to restart other
            tkinter windows that were paused to avoid
            dual-Tk() conflicts on Windows.

    Returns:
        True if dialog was opened, False if already open (singleton).
    """
    if not _settings_lock.acquire(blocking=False):
        logger.info("Settings dialog already open.")
        return False

    def _run_dialog():
        try:
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
            root.withdraw()  # Hide the root window

            # Apply dark theme (sv_ttk + manual overrides for consistency)
            _sv_ttk_ok = False
            try:
                import sv_ttk
                sv_ttk.set_theme("dark")
                _sv_ttk_ok = True
                logger.debug("sv_ttk dark theme applied.")
            except Exception:
                logger.debug("sv_ttk not available, applying manual dark theme.")

            _configure_dark_style(root, ttk, _sv_ttk_ok)

            dialog = SettingsDialog(root, config, on_save)

            # Apply dark title bar to the dialog window via Windows DWM API.
            # Must be called after the Toplevel is created and has a valid HWND.
            dialog._dialog.update_idletasks()
            _apply_dark_title_bar(dialog._dialog)

            def _on_close():
                root.quit()

            dialog.protocol("WM_DELETE_WINDOW", _on_close)
            root.mainloop()

            try:
                root.destroy()
            except Exception:
                pass  # May already be destroyed

        except Exception:
            logger.exception("Settings dialog error.")
        finally:
            _settings_lock.release()
            if on_close is not None:
                try:
                    on_close()
                except Exception:
                    logger.debug("on_close callback error.", exc_info=True)

    thread = threading.Thread(
        target=_run_dialog,
        daemon=True,
        name="settings-dialog",
    )
    thread.start()
    return True


class SettingsDialog:
    """Settings dialog for Voice Paste configuration.

    Created on a dedicated tkinter thread. Reads current config and
    keyring values on open, writes them back on Save.

    All tkinter widget operations happen on the tkinter thread that
    owns this dialog's Tk root. No cross-thread tkinter calls.
    """

    def __init__(
        self,
        parent: "tk.Tk",
        config: "AppConfig",
        on_save: Callable[[dict[str, Any]], None],
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self._config = config
        self._on_save = on_save
        self._parent = parent

        # Dark theme colors for plain tk widgets (sv_ttk only themes ttk)
        self._bg_color = _DARK_COLORS["bg"]
        self._fg_color = _DARK_COLORS["fg"]
        self._text_bg = _DARK_COLORS["field_bg"]
        self._text_fg = _DARK_COLORS["field_fg"]
        self._text_insert = _DARK_COLORS["insert"]

        # Create the dialog window
        self._dialog = tk.Toplevel(parent)
        self._dialog.title("Voice Paste - Settings")
        self._dialog.configure(bg=self._bg_color)
        self._dialog.resizable(False, False)
        self._dialog.minsize(540, 580)

        # Track editing state for API key fields
        self._openai_key_editing = False
        self._openrouter_key_editing = False
        self._openai_key_actual = config.openai_api_key
        self._openrouter_key_actual = config.openrouter_api_key

        # Model download state
        self._download_thread: threading.Thread | None = None
        self._download_cancel = threading.Event()
        self._download_done = threading.Event()
        self._download_success = False
        self._download_error_msg: str = ""
        # Progress tracking (updated by the download thread, read by UI poll)
        self._download_bytes: int = 0
        self._download_total: int = 0
        self._download_poll_count: int = 0  # For elapsed-time display
        self._download_phase: str = "connecting"  # "connecting", "downloading"

        # Build the UI
        self._build_ui()
        self._populate_from_config()

        # Center on screen
        self._dialog.update_idletasks()
        w = self._dialog.winfo_width()
        h = self._dialog.winfo_height()
        x = (self._dialog.winfo_screenwidth() // 2) - (w // 2)
        y = (self._dialog.winfo_screenheight() // 2) - (h // 2)
        self._dialog.geometry(f"+{x}+{y}")

        # Focus and keyboard bindings
        self._dialog.focus_force()
        self._dialog.grab_set()
        self._dialog.bind("<Escape>", lambda e: self._on_cancel_clicked())
        self._dialog.bind("<Return>", lambda e: self._on_save_clicked())

        # Expose protocol method for the parent thread
        self.protocol = self._dialog.protocol

    def _build_ui(self) -> None:
        """Build all UI widgets using a tabbed Notebook layout.

        Layout structure:
            main_frame
                notebook (ttk.Notebook)
                    Tab 1: Transcription
                    Tab 2: Summarization
                    Tab 3: Text-to-Speech
                    Tab 4: General
                error_label (shown on validation failure)
                button_frame (Save / Cancel)
        """
        tk = self._tk
        ttk = self._ttk
        dialog = self._dialog

        # Main frame with padding
        main_frame = ttk.Frame(dialog, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # === Notebook (tabbed container) ===
        self._notebook = ttk.Notebook(main_frame)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        # Create tab frames with internal padding
        transcription_tab = ttk.Frame(self._notebook, padding=(10, 8))
        summarization_tab = ttk.Frame(self._notebook, padding=(10, 8))
        tts_tab = ttk.Frame(self._notebook, padding=(10, 8))
        general_tab = ttk.Frame(self._notebook, padding=(10, 8))
        handsfree_tab = ttk.Frame(self._notebook, padding=(10, 8))
        claude_code_tab = ttk.Frame(self._notebook, padding=(10, 8))

        self._notebook.add(transcription_tab, text="Transcription")
        self._notebook.add(summarization_tab, text="Summarization")
        self._notebook.add(tts_tab, text="Text-to-Speech")
        self._notebook.add(general_tab, text="General")
        self._notebook.add(handsfree_tab, text="Hands-Free")
        self._notebook.add(claude_code_tab, text="Claude Code")

        # ---------------------------------------------------------------
        # Tab 1: Transcription
        # ---------------------------------------------------------------
        self._build_transcription_tab(transcription_tab)

        # ---------------------------------------------------------------
        # Tab 2: Summarization
        # ---------------------------------------------------------------
        self._build_summarization_tab(summarization_tab)

        # ---------------------------------------------------------------
        # Tab 3: Text-to-Speech
        # ---------------------------------------------------------------
        self._build_tts_tab(tts_tab)

        # ---------------------------------------------------------------
        # Tab 4: General
        # ---------------------------------------------------------------
        self._build_general_tab(general_tab)
        self._build_handsfree_tab(handsfree_tab)

        # ---------------------------------------------------------------
        # Tab 6: Claude Code
        # ---------------------------------------------------------------
        self._build_claude_code_tab(claude_code_tab)

        # === Error label (below notebook, hidden by default) ===
        self._error_label = ttk.Label(
            main_frame, text="", foreground="#FF6B6B", wraplength=480
        )
        # Not packed initially; shown by _on_save_clicked on validation error

        # === Button Bar (below notebook) ===
        self._button_frame = ttk.Frame(main_frame)
        self._button_frame.pack(fill=tk.X, pady=(12, 0))

        ttk.Button(
            self._button_frame,
            text="Save",
            width=10,
            command=self._on_save_clicked,
        ).pack(side=tk.RIGHT)

        ttk.Button(
            self._button_frame,
            text="Cancel",
            width=10,
            command=self._on_cancel_clicked,
        ).pack(side=tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------------
    # Tab builder methods
    # ------------------------------------------------------------------

    def _build_transcription_tab(self, parent: "ttk.Frame") -> None:
        """Build widgets for the Transcription tab.

        Args:
            parent: The tab frame to populate.
        """
        tk = self._tk
        ttk = self._ttk

        # Backend selector row
        backend_row = ttk.Frame(parent)
        backend_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(backend_row, text="Backend:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._backend_var = tk.StringVar()
        self._backend_combo = ttk.Combobox(
            backend_row,
            textvariable=self._backend_var,
            values=["Cloud (OpenAI Whisper API)", "Local (faster-whisper, offline)"],
            state="readonly",
            width=35,
        )
        self._backend_combo.pack(side=tk.LEFT, padx=(4, 0))
        self._backend_combo.bind("<<ComboboxSelected>>", self._on_backend_changed)

        # Language selector row
        lang_row = ttk.Frame(parent)
        lang_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(lang_row, text="Language:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        from constants import SUPPORTED_LANGUAGES
        self._lang_var = tk.StringVar()
        self._lang_display_values = list(SUPPORTED_LANGUAGES.values())
        self._lang_code_keys = list(SUPPORTED_LANGUAGES.keys())
        self._lang_combo = ttk.Combobox(
            lang_row,
            textvariable=self._lang_var,
            values=self._lang_display_values,
            state="readonly",
            width=35,
        )
        self._lang_combo.pack(side=tk.LEFT, padx=(4, 0))

        lang_hint = ttk.Label(
            lang_row,
            text="\"Auto-detect\" lets Whisper identify the language automatically",
            foreground="#999999",
            font=("", 8),
        )
        lang_hint.pack(side=tk.LEFT, padx=(8, 0))

        # Audio input device selector row
        mic_row = ttk.Frame(parent)
        mic_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(mic_row, text="Microphone:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._audio_device_var = tk.StringVar()
        self._audio_device_combo = ttk.Combobox(
            mic_row,
            textvariable=self._audio_device_var,
            state="readonly",
            width=45,
        )
        self._audio_device_combo.pack(side=tk.LEFT, padx=(4, 0))

        # Populate device list
        self._audio_device_map: list[tuple[int | None, str]] = []
        self._refresh_audio_devices()

        refresh_btn = ttk.Button(
            mic_row, text="Refresh", width=8,
            command=self._refresh_audio_devices,
        )
        refresh_btn.pack(side=tk.LEFT, padx=(4, 0))

        # --- Cloud sub-frame (shown when backend = cloud) ---
        self._cloud_frame = ttk.Frame(parent)

        # OpenAI API Key row
        key_row = ttk.Frame(self._cloud_frame)
        key_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(key_row, text="API Key:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._openai_key_var = tk.StringVar()
        self._openai_key_entry = ttk.Entry(
            key_row, textvariable=self._openai_key_var, show="*", width=40
        )
        self._openai_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))

        self._openai_key_btn = ttk.Button(
            key_row, text="Edit", width=6, command=self._toggle_openai_key_edit
        )
        self._openai_key_btn.pack(side=tk.LEFT)

        # Cloud hint
        cloud_hint = ttk.Label(
            self._cloud_frame,
            text="Required for cloud transcription. Get a key at platform.openai.com",
            foreground="#999999",
            font=("", 8),
        )
        cloud_hint.pack(fill=tk.X, padx=(0, 0), pady=(0, 4))

        # --- Local sub-frame (shown when backend = local) ---
        self._local_frame = ttk.Frame(parent)

        # Model size row
        model_size_row = ttk.Frame(self._local_frame)
        model_size_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(model_size_row, text="Model:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        from constants import LOCAL_MODEL_DISPLAY
        self._local_model_var = tk.StringVar()
        model_display_values = [
            LOCAL_MODEL_DISPLAY[k]["label"] for k in LOCAL_MODEL_DISPLAY
        ]
        self._local_model_combo = ttk.Combobox(
            model_size_row,
            textvariable=self._local_model_var,
            values=model_display_values,
            state="readonly",
            width=45,
        )
        self._local_model_combo.pack(side=tk.LEFT, padx=(4, 0))
        self._local_model_combo.bind("<<ComboboxSelected>>", self._on_model_size_changed)

        # Device row
        device_row = ttk.Frame(self._local_frame)
        device_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(device_row, text="Device:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._local_device_var = tk.StringVar()
        self._local_device_combo = ttk.Combobox(
            device_row,
            textvariable=self._local_device_var,
            values=["cpu", "cuda", "auto"],
            state="readonly",
            width=12,
        )
        self._local_device_combo.pack(side=tk.LEFT, padx=(4, 0))

        device_hint = ttk.Label(
            device_row,
            text="cpu = works everywhere, cuda = NVIDIA GPU",
            foreground="#999999",
            font=("", 8),
        )
        device_hint.pack(side=tk.LEFT, padx=(8, 0))

        # Model status row
        status_row = ttk.Frame(self._local_frame)
        status_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(status_row, text="Status:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._model_status_label = ttk.Label(
            status_row, text="Checking...", foreground="#999999"
        )
        self._model_status_label.pack(side=tk.LEFT, padx=(4, 0))

        self._download_btn = ttk.Button(
            status_row, text="Download Model", width=16,
            command=self._on_download_clicked,
        )
        # Don't pack yet -- _update_model_status will show/hide it

        self._delete_btn = ttk.Button(
            status_row, text="Delete", width=8,
            command=self._on_delete_clicked,
        )
        # Don't pack yet -- _update_model_status will show/hide it

        # Progress bar row (hidden by default)
        self._progress_frame = ttk.Frame(self._local_frame)

        ttk.Label(self._progress_frame, text="", width=10).pack(side=tk.LEFT)
        self._progress_bar = ttk.Progressbar(
            self._progress_frame, mode="determinate", length=300,
            maximum=100,
        )
        self._progress_bar.pack(side=tk.LEFT, padx=(4, 8))

        self._progress_label = ttk.Label(
            self._progress_frame, text="Downloading...", foreground="#999999",
            font=("", 8),
        )
        self._progress_label.pack(side=tk.LEFT)

        # Local privacy note
        local_hint = ttk.Label(
            self._local_frame,
            text="Local mode: audio is never sent to any server. Requires faster-whisper.",
            foreground="#66CC66",
            font=("", 8),
        )
        local_hint.pack(fill=tk.X, pady=(2, 4))

        # Transcription error label
        self._transcription_error = ttk.Label(
            parent, text="", foreground="#FF6B6B", font=("", 8)
        )

    def _refresh_audio_devices(self) -> None:
        """Query sounddevice for available input devices and populate the combo."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
        except Exception:
            self._audio_device_map = [(None, "System Default")]
            self._audio_device_combo["values"] = ["System Default"]
            self._audio_device_var.set("System Default")
            return

        self._audio_device_map = [(None, "System Default")]
        for i, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                name = dev.get("name", f"Device {i}")
                label = f"{name} (#{i})"
                self._audio_device_map.append((i, label))

        display_values = [label for _, label in self._audio_device_map]
        self._audio_device_combo["values"] = display_values

        # Preserve current selection if still valid
        current = self._audio_device_var.get()
        if current not in display_values:
            self._audio_device_var.set("System Default")

    def _build_summarization_tab(self, parent: "ttk.Frame") -> None:
        """Build widgets for the Summarization tab.

        Args:
            parent: The tab frame to populate.
        """
        tk = self._tk
        ttk = self._ttk

        # Enable checkbox
        self._summarization_enabled_var = tk.BooleanVar()
        self._summarization_checkbox = ttk.Checkbutton(
            parent,
            text="Enable summarization",
            variable=self._summarization_enabled_var,
            command=self._on_summarization_toggled,
        )
        self._summarization_checkbox.pack(fill=tk.X, pady=(0, 6))

        # Provider row
        provider_row = ttk.Frame(parent)
        provider_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(provider_row, text="Provider:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._provider_var = tk.StringVar()
        self._provider_combo = ttk.Combobox(
            provider_row,
            textvariable=self._provider_var,
            values=["OpenAI", "OpenRouter", "Ollama"],
            state="readonly",
            width=20,
        )
        self._provider_combo.pack(side=tk.LEFT, padx=(4, 0))
        self._provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)

        # OpenRouter API Key row (hidden when provider is OpenAI)
        self._openrouter_key_frame = ttk.Frame(parent)

        or_key_row = ttk.Frame(self._openrouter_key_frame)
        or_key_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(or_key_row, text="API Key:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._openrouter_key_var = tk.StringVar()
        self._openrouter_key_entry = ttk.Entry(
            or_key_row, textvariable=self._openrouter_key_var, show="*", width=40
        )
        self._openrouter_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))

        self._openrouter_key_btn = ttk.Button(
            or_key_row, text="Edit", width=6, command=self._toggle_openrouter_key_edit
        )
        self._openrouter_key_btn.pack(side=tk.LEFT)

        or_hint = ttk.Label(
            self._openrouter_key_frame,
            text="Required for OpenRouter. Get a key at openrouter.ai",
            foreground="#999999",
            font=("", 8),
        )
        or_hint.pack(fill=tk.X, pady=(0, 4))

        # OpenRouter error label
        self._openrouter_error = ttk.Label(
            self._openrouter_key_frame, text="", foreground="#FF6B6B", font=("", 8)
        )

        # Model row
        model_row = ttk.Frame(parent)
        model_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(model_row, text="Model:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._model_var = tk.StringVar()
        self._model_entry = ttk.Entry(
            model_row, textvariable=self._model_var, width=40
        )
        self._model_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # Base URL row
        url_row = ttk.Frame(parent)
        url_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(url_row, text="Base URL:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._base_url_var = tk.StringVar()
        self._base_url_entry = ttk.Entry(
            url_row, textvariable=self._base_url_var, width=40
        )
        self._base_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        url_hint = ttk.Label(
            parent,
            text="Advanced. Change only if using a custom endpoint.",
            foreground="#999999",
            font=("", 8),
        )
        url_hint.pack(fill=tk.X, pady=(0, 6))

        # Custom Prompt section
        prompt_label_row = ttk.Frame(parent)
        prompt_label_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(
            prompt_label_row, text="Cleanup Prompt:", anchor=tk.W
        ).pack(side=tk.LEFT)

        self._prompt_reset_btn = ttk.Button(
            prompt_label_row,
            text="Reset to Default",
            width=15,
            command=self._reset_prompt,
        )
        self._prompt_reset_btn.pack(side=tk.RIGHT)

        self._prompt_text = tk.Text(
            parent,
            height=6,
            width=50,
            wrap=tk.WORD,
            font=("", 9),
            bg=self._text_bg,
            fg=self._text_fg,
            insertbackground=self._text_insert,
            selectbackground=_DARK_COLORS["select_bg"],
            selectforeground=_DARK_COLORS["select_fg"],
            highlightbackground=_DARK_COLORS["border"],
            highlightcolor=_DARK_COLORS["accent"],
            highlightthickness=1,
            relief=tk.FLAT,
            borderwidth=4,
        )
        self._prompt_text.pack(fill=tk.X, pady=(0, 2))

        prompt_hint = ttk.Label(
            parent,
            text="Instructs the LLM how to clean up the transcription. Leave empty for default.",
            foreground="#999999",
            font=("", 8),
        )
        prompt_hint.pack(fill=tk.X, pady=(0, 4))

        # Store references to summarization widgets for enable/disable
        self._summarization_widgets = [
            self._provider_combo,
            self._model_entry,
            self._base_url_entry,
            self._prompt_text,
            self._prompt_reset_btn,
            self._openrouter_key_entry,
            self._openrouter_key_btn,
        ]

    def _build_tts_tab(self, parent: "ttk.Frame") -> None:
        """Build widgets for the Text-to-Speech tab.

        Args:
            parent: The tab frame to populate.
        """
        tk = self._tk
        ttk = self._ttk

        # Enable checkbox
        self._tts_enabled_var = tk.BooleanVar()
        self._tts_checkbox = ttk.Checkbutton(
            parent,
            text="Enable Text-to-Speech",
            variable=self._tts_enabled_var,
            command=self._on_tts_toggled,
        )
        self._tts_checkbox.pack(fill=tk.X, pady=(0, 6))

        # TTS Backend selector row
        tts_backend_row = ttk.Frame(parent)
        tts_backend_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(tts_backend_row, text="Backend:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._tts_backend_var = tk.StringVar()
        self._tts_backend_combo = ttk.Combobox(
            tts_backend_row,
            textvariable=self._tts_backend_var,
            values=["Cloud (ElevenLabs API)", "Local (Piper, offline)"],
            state="readonly",
            width=35,
        )
        self._tts_backend_combo.pack(side=tk.LEFT, padx=(4, 0))
        self._tts_backend_combo.bind("<<ComboboxSelected>>", self._on_tts_backend_changed)

        # --- TTS Cloud sub-frame (ElevenLabs, shown when backend = cloud) ---
        self._tts_cloud_frame = ttk.Frame(parent)

        # ElevenLabs API Key row
        tts_key_row = ttk.Frame(self._tts_cloud_frame)
        tts_key_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(tts_key_row, text="API Key:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._elevenlabs_key_var = tk.StringVar()
        self._elevenlabs_key_entry = ttk.Entry(
            tts_key_row, textvariable=self._elevenlabs_key_var, show="*", width=40
        )
        self._elevenlabs_key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))

        self._elevenlabs_key_btn = ttk.Button(
            tts_key_row, text="Edit", width=6, command=self._toggle_elevenlabs_key_edit
        )
        self._elevenlabs_key_btn.pack(side=tk.LEFT)

        # Track editing state for ElevenLabs key
        self._elevenlabs_key_editing = False
        self._elevenlabs_key_actual = self._config.elevenlabs_api_key

        tts_key_hint = ttk.Label(
            self._tts_cloud_frame,
            text="Get a key at elevenlabs.io. Stored in Windows Credential Manager.",
            foreground="#999999",
            font=("", 8),
        )
        tts_key_hint.pack(fill=tk.X, pady=(0, 4))

        # Voice row
        tts_voice_row = ttk.Frame(self._tts_cloud_frame)
        tts_voice_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(tts_voice_row, text="Voice:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        from constants import ELEVENLABS_VOICE_PRESETS
        voice_display_values = [
            f"{info['name']} ({info['description']})"
            for info in ELEVENLABS_VOICE_PRESETS.values()
        ]
        voice_display_values.append("Custom (enter Voice ID below)")

        self._tts_voice_var = tk.StringVar()
        self._tts_voice_combo = ttk.Combobox(
            tts_voice_row,
            textvariable=self._tts_voice_var,
            values=voice_display_values,
            state="readonly",
            width=35,
        )
        self._tts_voice_combo.pack(side=tk.LEFT, padx=(4, 0))
        self._tts_voice_combo.bind("<<ComboboxSelected>>", self._on_tts_voice_changed)

        # Custom Voice ID row
        tts_custom_voice_row = ttk.Frame(self._tts_cloud_frame)
        tts_custom_voice_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(tts_custom_voice_row, text="Voice ID:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._tts_voice_id_var = tk.StringVar()
        self._tts_voice_id_entry = ttk.Entry(
            tts_custom_voice_row, textvariable=self._tts_voice_id_var, width=30
        )
        self._tts_voice_id_entry.pack(side=tk.LEFT, padx=(4, 0))

        # Model row
        tts_model_row = ttk.Frame(self._tts_cloud_frame)
        tts_model_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(tts_model_row, text="Model:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._tts_model_var = tk.StringVar()
        self._tts_model_combo = ttk.Combobox(
            tts_model_row,
            textvariable=self._tts_model_var,
            values=["eleven_flash_v2_5 (fast, low latency)", "eleven_multilingual_v2 (higher quality)"],
            state="readonly",
            width=35,
        )
        self._tts_model_combo.pack(side=tk.LEFT, padx=(4, 0))

        # --- TTS Local sub-frame (Piper, shown when backend = local) ---
        self._tts_local_frame = ttk.Frame(parent)

        # Piper voice dropdown row
        tts_piper_voice_row = ttk.Frame(self._tts_local_frame)
        tts_piper_voice_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(tts_piper_voice_row, text="Voice:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        from constants import PIPER_VOICE_MODELS
        self._tts_piper_voice_var = tk.StringVar()
        piper_display_values = [
            info["label"] for info in PIPER_VOICE_MODELS.values()
        ]
        self._tts_piper_voice_combo = ttk.Combobox(
            tts_piper_voice_row,
            textvariable=self._tts_piper_voice_var,
            values=piper_display_values,
            state="readonly",
            width=45,
        )
        self._tts_piper_voice_combo.pack(side=tk.LEFT, padx=(4, 0))
        self._tts_piper_voice_combo.bind(
            "<<ComboboxSelected>>", self._on_tts_piper_voice_changed
        )

        # Piper model status row
        tts_piper_status_row = ttk.Frame(self._tts_local_frame)
        tts_piper_status_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(tts_piper_status_row, text="Status:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._tts_model_status_label = ttk.Label(
            tts_piper_status_row, text="Checking...", foreground="#999999"
        )
        self._tts_model_status_label.pack(side=tk.LEFT, padx=(4, 0))

        self._tts_download_btn = ttk.Button(
            tts_piper_status_row, text="Download Model", width=16,
            command=self._on_tts_download_clicked,
        )
        # Don't pack yet -- _update_tts_model_status will show/hide it

        self._tts_delete_btn = ttk.Button(
            tts_piper_status_row, text="Delete", width=8,
            command=self._on_tts_delete_clicked,
        )
        # Don't pack yet -- _update_tts_model_status will show/hide it

        # Piper progress bar row (hidden by default)
        self._tts_progress_frame = ttk.Frame(self._tts_local_frame)

        ttk.Label(self._tts_progress_frame, text="", width=10).pack(side=tk.LEFT)
        self._tts_progress_bar = ttk.Progressbar(
            self._tts_progress_frame, mode="determinate", length=300,
            maximum=100,
        )
        self._tts_progress_bar.pack(side=tk.LEFT, padx=(4, 8))

        self._tts_progress_label = ttk.Label(
            self._tts_progress_frame, text="Downloading...", foreground="#999999",
            font=("", 8),
        )
        self._tts_progress_label.pack(side=tk.LEFT)

        # TTS Speed row
        tts_speed_row = ttk.Frame(self._tts_local_frame)
        tts_speed_row.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(tts_speed_row, text="Speed:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_speed_var = tk.StringVar(value="1.0")
        ttk.Spinbox(
            tts_speed_row, from_=0.5, to=2.0, increment=0.1, width=6,
            textvariable=self._tts_speed_var,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(tts_speed_row, text="(0.5 = slow, 1.0 = normal, 2.0 = fast)").pack(
            side=tk.LEFT, padx=(4, 0)
        )

        # Piper privacy hint
        tts_local_hint = ttk.Label(
            self._tts_local_frame,
            text="Local mode: audio is synthesized on your device. No internet needed.",
            foreground="#66CC66",
            font=("", 8),
        )
        tts_local_hint.pack(fill=tk.X, pady=(2, 4))

        # TTS model download state (Piper)
        self._tts_download_thread: threading.Thread | None = None
        self._tts_download_cancel = threading.Event()
        self._tts_download_done = threading.Event()
        self._tts_download_success = False
        self._tts_download_error_msg: str = ""
        self._tts_download_bytes: int = 0
        self._tts_download_total: int = 0
        self._tts_download_poll_count: int = 0

        # --- TTS Cache section ---
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 8))

        ttk.Label(
            parent, text="TTS Cache", font=("", 9, "bold"),
        ).pack(fill=tk.X, pady=(0, 4))

        self._tts_cache_enabled_var = tk.BooleanVar()
        self._tts_cache_checkbox = ttk.Checkbutton(
            parent,
            text="Enable TTS audio cache",
            variable=self._tts_cache_enabled_var,
        )
        self._tts_cache_checkbox.pack(fill=tk.X, pady=(0, 4))

        # Max size (MB)
        cache_size_row = ttk.Frame(parent)
        cache_size_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(cache_size_row, text="Max size:", width=12, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_cache_max_size_var = tk.StringVar()
        self._tts_cache_max_size_spin = ttk.Spinbox(
            cache_size_row, from_=10, to=2000, increment=10, width=6,
            textvariable=self._tts_cache_max_size_var,
        )
        self._tts_cache_max_size_spin.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(cache_size_row, text="MB").pack(side=tk.LEFT, padx=(4, 0))

        # Max age (days)
        cache_age_row = ttk.Frame(parent)
        cache_age_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(cache_age_row, text="Max age:", width=12, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_cache_max_age_var = tk.StringVar()
        self._tts_cache_max_age_spin = ttk.Spinbox(
            cache_age_row, from_=0, to=365, increment=1, width=6,
            textvariable=self._tts_cache_max_age_var,
        )
        self._tts_cache_max_age_spin.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(cache_age_row, text="days (0 = no limit)").pack(side=tk.LEFT, padx=(4, 0))

        # Max entries
        cache_entries_row = ttk.Frame(parent)
        cache_entries_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(cache_entries_row, text="Max entries:", width=12, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_cache_max_entries_var = tk.StringVar()
        self._tts_cache_max_entries_spin = ttk.Spinbox(
            cache_entries_row, from_=0, to=5000, increment=50, width=6,
            textvariable=self._tts_cache_max_entries_var,
        )
        self._tts_cache_max_entries_spin.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(cache_entries_row, text="entries (0 = no limit)").pack(side=tk.LEFT, padx=(4, 0))

        # Cache usage label
        cache_usage_row = ttk.Frame(parent)
        cache_usage_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(cache_usage_row, text="Usage:", width=12, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_cache_usage_label = ttk.Label(
            cache_usage_row, text="Calculating...", foreground="#999999",
        )
        self._tts_cache_usage_label.pack(side=tk.LEFT, padx=(4, 0))

        # Clear Cache + Open Folder buttons
        cache_btn_row = ttk.Frame(parent)
        cache_btn_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(cache_btn_row, text="", width=12).pack(side=tk.LEFT)
        self._tts_cache_clear_btn = ttk.Button(
            cache_btn_row, text="Clear Cache", width=14,
            command=self._on_tts_cache_clear_clicked,
        )
        self._tts_cache_clear_btn.pack(side=tk.LEFT, padx=(4, 4))
        self._tts_cache_open_btn = ttk.Button(
            cache_btn_row, text="Open Folder", width=14,
            command=self._on_tts_cache_open_folder,
        )
        self._tts_cache_open_btn.pack(side=tk.LEFT, padx=(0, 0))

        # --- TTS Export section ---
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 8))

        ttk.Label(
            parent, text="TTS Export", font=("", 9, "bold"),
        ).pack(fill=tk.X, pady=(0, 4))

        self._tts_export_enabled_var = tk.BooleanVar()
        self._tts_export_checkbox = ttk.Checkbutton(
            parent,
            text="Auto-export TTS audio to folder",
            variable=self._tts_export_enabled_var,
        )
        self._tts_export_checkbox.pack(fill=tk.X, pady=(0, 4))

        # Export path row
        export_path_row = ttk.Frame(parent)
        export_path_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(export_path_row, text="Export path:", width=12, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_export_path_var = tk.StringVar()
        self._tts_export_path_entry = ttk.Entry(
            export_path_row, textvariable=self._tts_export_path_var, width=30,
        )
        self._tts_export_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        self._tts_export_browse_btn = ttk.Button(
            export_path_row, text="Browse...", width=10,
            command=self._on_tts_export_browse,
        )
        self._tts_export_browse_btn.pack(side=tk.LEFT)

        ttk.Label(
            parent,
            text="When enabled, every TTS audio file is also saved to this folder.",
            foreground="#999999",
            font=("", 8),
        ).pack(fill=tk.X, pady=(0, 4))

        # Store references for enable/disable
        self._tts_widgets = [
            self._tts_backend_combo,
            self._elevenlabs_key_entry,
            self._elevenlabs_key_btn,
            self._tts_voice_combo,
            self._tts_voice_id_entry,
            self._tts_model_combo,
            self._tts_piper_voice_combo,
            self._tts_download_btn,
            self._tts_delete_btn,
            self._tts_cache_checkbox,
            self._tts_cache_max_size_spin,
            self._tts_cache_max_age_spin,
            self._tts_cache_max_entries_spin,
            self._tts_cache_clear_btn,
            self._tts_cache_open_btn,
            self._tts_export_checkbox,
            self._tts_export_path_entry,
            self._tts_export_browse_btn,
        ]

    def _build_general_tab(self, parent: "ttk.Frame") -> None:
        """Build widgets for the General tab.

        Args:
            parent: The tab frame to populate.
        """
        tk = self._tk
        ttk = self._ttk

        # --- Hotkeys section ---
        hotkeys_label = ttk.Label(
            parent, text="Hotkeys", font=("", 10, "bold")
        )
        hotkeys_label.pack(fill=tk.X, pady=(0, 6))

        # Hotkey display (read-only)
        hotkey_row = ttk.Frame(parent)
        hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(hotkey_row, text="Summarize:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._hotkey_label = ttk.Label(hotkey_row, text="", font=("", 9, "bold"))
        self._hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        prompt_hotkey_row = ttk.Frame(parent)
        prompt_hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(prompt_hotkey_row, text="Ask LLM:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._prompt_hotkey_label = ttk.Label(prompt_hotkey_row, text="", font=("", 9, "bold"))
        self._prompt_hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        # TTS hotkeys display
        tts_hotkey_row = ttk.Frame(parent)
        tts_hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(tts_hotkey_row, text="Read TTS:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_hotkey_label = ttk.Label(tts_hotkey_row, text="", font=("", 9, "bold"))
        self._tts_hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        tts_ask_hotkey_row = ttk.Frame(parent)
        tts_ask_hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(tts_ask_hotkey_row, text="Ask+TTS:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_ask_hotkey_label = ttk.Label(tts_ask_hotkey_row, text="", font=("", 9, "bold"))
        self._tts_ask_hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        hotkey_hint = ttk.Label(
            parent,
            text="Change in config.toml (requires restart)",
            foreground="#999999",
            font=("", 8),
        )
        hotkey_hint.pack(fill=tk.X, pady=(0, 8))

        # --- Separator ---
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

        # --- Audio section ---
        # Audio cues checkbox
        self._audio_cues_var = tk.BooleanVar()
        ttk.Checkbutton(
            parent,
            text="Play audio cues",
            variable=self._audio_cues_var,
        ).pack(fill=tk.X)

        # --- Separator ---
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 8))

        # --- v0.9: Paste confirmation section ---
        paste_label = ttk.Label(
            parent, text="Paste Behaviour", font=("", 10, "bold")
        )
        paste_label.pack(fill=tk.X, pady=(0, 6))

        # Confirm before paste checkbox
        self._paste_confirm_var = tk.BooleanVar()
        self._paste_confirm_cb = ttk.Checkbutton(
            parent,
            text="Confirm before pasting (Enter = paste, Escape = cancel)",
            variable=self._paste_confirm_var,
            command=self._on_paste_confirm_toggled,
        )
        self._paste_confirm_cb.pack(fill=tk.X)

        # Delay row (only active when confirmation is OFF)
        delay_row = ttk.Frame(parent)
        delay_row.pack(fill=tk.X, pady=(4, 2))
        self._paste_delay_label = ttk.Label(
            delay_row, text="Delay before paste (seconds):", anchor=tk.W
        )
        self._paste_delay_label.pack(side=tk.LEFT)
        self._paste_delay_var = tk.StringVar(value="0")
        self._paste_delay_spin = ttk.Spinbox(
            delay_row,
            from_=0,
            to=36000,
            increment=1,
            width=6,
            textvariable=self._paste_delay_var,
        )
        self._paste_delay_spin.pack(side=tk.LEFT, padx=(8, 0))

        # Timeout row (only active when confirmation is ON)
        timeout_row = ttk.Frame(parent)
        timeout_row.pack(fill=tk.X, pady=(2, 2))
        self._paste_timeout_label = ttk.Label(
            timeout_row, text="Confirmation timeout (seconds):", anchor=tk.W
        )
        self._paste_timeout_label.pack(side=tk.LEFT)
        self._paste_timeout_var = tk.StringVar(value="30")
        self._paste_timeout_spin = ttk.Spinbox(
            timeout_row,
            from_=5,
            to=120,
            increment=5,
            width=6,
            textvariable=self._paste_timeout_var,
        )
        self._paste_timeout_spin.pack(side=tk.LEFT, padx=(8, 0))

        # Auto-Enter checkbox
        self._paste_auto_enter_var = tk.BooleanVar()
        ttk.Checkbutton(
            parent,
            text="Press Enter after pasting (execute command)",
            variable=self._paste_auto_enter_var,
        ).pack(fill=tk.X, pady=(4, 0))

        paste_hint = ttk.Label(
            parent,
            text="When confirmation is on, delay is ignored (Enter triggers paste).",
            foreground="#999999",
            font=("", 8),
        )
        paste_hint.pack(fill=tk.X, pady=(2, 0))

        # --- Separator ---
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 8))

        # --- v0.9: HTTP API section ---
        api_label = ttk.Label(
            parent, text="HTTP API", font=("", 10, "bold")
        )
        api_label.pack(fill=tk.X, pady=(0, 6))

        self._api_enabled_var = tk.BooleanVar()
        ttk.Checkbutton(
            parent,
            text="Enable local HTTP API",
            variable=self._api_enabled_var,
        ).pack(fill=tk.X)

        api_port_row = ttk.Frame(parent)
        api_port_row.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(api_port_row, text="Port:", anchor=tk.W).pack(side=tk.LEFT)
        self._api_port_var = tk.StringVar(value="18923")
        self._api_port_spin = ttk.Spinbox(
            api_port_row,
            from_=1024,
            to=65535,
            increment=1,
            width=7,
            textvariable=self._api_port_var,
        )
        self._api_port_spin.pack(side=tk.LEFT, padx=(8, 0))

        api_hint = ttk.Label(
            parent,
            text="Allows external programs to control Voice Paste.\n"
                 "Binds to 127.0.0.1 only (no network exposure).",
            foreground="#999999",
            font=("", 8),
        )
        api_hint.pack(fill=tk.X, pady=(2, 0))

    def _build_handsfree_tab(self, parent: "ttk.Frame") -> None:
        """Build the Hands-Free tab UI."""
        tk = self._tk
        ttk = self._ttk

        # Enable toggle
        self._handsfree_enabled_var = tk.BooleanVar()
        ttk.Checkbutton(
            parent,
            text="Enable Hands-Free Mode",
            variable=self._handsfree_enabled_var,
        ).pack(fill=tk.X, pady=(0, 4))

        # Privacy warning
        ttk.Label(
            parent,
            text="PRIVACY: Microphone is always active while enabled.\n"
                 "Wake word detection is 100% local (no audio sent to cloud).",
            foreground="#FF9966",
            wraplength=480,
            font=("", 8),
        ).pack(fill=tk.X, pady=(0, 8))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

        # Wake phrase
        phrase_row = ttk.Frame(parent)
        phrase_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(phrase_row, text="Wake phrase:", width=16, anchor=tk.W).pack(side=tk.LEFT)
        self._wake_phrase_var = tk.StringVar()
        ttk.Entry(phrase_row, textvariable=self._wake_phrase_var, width=30).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        # Match mode
        match_row = ttk.Frame(parent)
        match_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(match_row, text="Match mode:", width=16, anchor=tk.W).pack(side=tk.LEFT)
        self._match_mode_var = tk.StringVar()
        ttk.Combobox(
            match_row,
            textvariable=self._match_mode_var,
            values=["contains (forgiving)", "startswith (strict)", "fuzzy (token overlap)"],
            state="readonly",
            width=28,
        ).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 8))

        # Pipeline selector
        pipeline_row = ttk.Frame(parent)
        pipeline_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(pipeline_row, text="After wake word:", width=16, anchor=tk.W).pack(side=tk.LEFT)
        self._handsfree_pipeline_var = tk.StringVar()
        ttk.Combobox(
            pipeline_row,
            textvariable=self._handsfree_pipeline_var,
            values=[
                "Ask AI + TTS (ask_tts)",
                "Transcribe + Paste (summary)",
                "Ask AI + Paste (prompt)",
                "Claude Code (claude_code)",
            ],
            state="readonly",
            width=28,
        ).pack(side=tk.LEFT, padx=(4, 0))

        # Silence timeout
        silence_row = ttk.Frame(parent)
        silence_row.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(silence_row, text="Silence timeout:", width=16, anchor=tk.W).pack(side=tk.LEFT)
        self._silence_timeout_var = tk.StringVar(value="3.0")
        ttk.Spinbox(
            silence_row, from_=1.0, to=10.0, increment=0.5, width=6,
            textvariable=self._silence_timeout_var,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(silence_row, text="seconds").pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(
            parent,
            text="How long to wait after you stop speaking before auto-stopping.\n"
                 "Increase for slow/thoughtful speech with pauses.",
            foreground="#999999",
            font=("", 8),
        ).pack(fill=tk.X, pady=(0, 8))

        # Max recording duration
        max_row = ttk.Frame(parent)
        max_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(max_row, text="Max recording:", width=16, anchor=tk.W).pack(side=tk.LEFT)
        self._hf_max_recording_var = tk.StringVar(value="120")
        ttk.Spinbox(
            max_row, from_=10, to=300, increment=10, width=6,
            textvariable=self._hf_max_recording_var,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(max_row, text="seconds").pack(side=tk.LEFT, padx=(4, 0))

        # Cooldown
        cooldown_row = ttk.Frame(parent)
        cooldown_row.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(cooldown_row, text="Cooldown:", width=16, anchor=tk.W).pack(side=tk.LEFT)
        self._hf_cooldown_var = tk.StringVar(value="3.0")
        ttk.Spinbox(
            cooldown_row, from_=1.0, to=10.0, increment=0.5, width=6,
            textvariable=self._hf_cooldown_var,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(cooldown_row, text="seconds").pack(side=tk.LEFT, padx=(4, 0))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 8))
        ttk.Label(
            parent,
            text="Requirements:\n"
                 "- faster-whisper must be installed (Local STT build)\n"
                 "- The 'tiny' Whisper model will be loaded for detection\n"
                 "  (~75 MB RAM, minimal CPU when no speech detected)",
            foreground="#999999",
            wraplength=480,
            font=("", 8),
            justify=tk.LEFT,
        ).pack(fill=tk.X)

    def _build_claude_code_tab(self, parent: "ttk.Frame") -> None:
        """Build the Claude Code tab UI (v1.2)."""
        tk = self._tk
        ttk = self._ttk

        # Enable toggle
        self._claude_code_enabled_var = tk.BooleanVar()
        ttk.Checkbutton(
            parent,
            text="Enable Claude Code Integration",
            variable=self._claude_code_enabled_var,
        ).pack(fill=tk.X, pady=(0, 4))

        # Status label
        self._claude_code_status_var = tk.StringVar(value="Checking...")
        status_row = ttk.Frame(parent)
        status_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(status_row, text="Status:", width=16, anchor=tk.W).pack(side=tk.LEFT)
        self._claude_code_status_label = ttk.Label(
            status_row, textvariable=self._claude_code_status_var,
        )
        self._claude_code_status_label.pack(side=tk.LEFT, padx=(4, 0))

        # Check CLI availability on tab build
        try:
            from claude_code import ClaudeCodeBackend
            if ClaudeCodeBackend.is_available():
                version = ClaudeCodeBackend.get_version() or "unknown version"
                self._claude_code_status_var.set(f"Found: {version}")
                self._claude_code_status_label.configure(foreground="#66BB6A")
            else:
                self._claude_code_status_var.set(
                    "Not found. Install: npm i -g @anthropic-ai/claude-code"
                )
                self._claude_code_status_label.configure(foreground="#FF6B6B")
        except Exception:
            self._claude_code_status_var.set("Error checking CLI availability")
            self._claude_code_status_label.configure(foreground="#FF6B6B")

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

        # Working directory
        workdir_row = ttk.Frame(parent)
        workdir_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(workdir_row, text="Working directory:", width=16, anchor=tk.W).pack(
            side=tk.LEFT
        )
        self._claude_code_workdir_var = tk.StringVar()
        ttk.Entry(
            workdir_row, textvariable=self._claude_code_workdir_var, width=30
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        ttk.Button(
            workdir_row, text="Browse...", width=8,
            command=self._browse_claude_code_workdir,
        ).pack(side=tk.LEFT)

        ttk.Label(
            parent,
            text="The project directory where Claude Code runs.\n"
                 "Claude reads CLAUDE.md and project files from this directory.\n"
                 "Leave empty to use VoicePaste's current directory.",
            foreground="#999999",
            font=("", 8),
        ).pack(fill=tk.X, pady=(0, 8))

        # System prompt
        ttk.Label(parent, text="System prompt (optional):", anchor=tk.W).pack(
            fill=tk.X, pady=(0, 2)
        )
        prompt_frame = ttk.Frame(parent)
        prompt_frame.pack(fill=tk.X, pady=(0, 8))
        self._claude_code_prompt_text = tk.Text(
            prompt_frame, height=3, width=50, wrap=tk.WORD,
        )
        self._claude_code_prompt_text.pack(fill=tk.X)

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

        # Response mode
        mode_row = ttk.Frame(parent)
        mode_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(mode_row, text="Response mode:", width=16, anchor=tk.W).pack(
            side=tk.LEFT
        )
        self._claude_code_mode_var = tk.StringVar()
        ttk.Combobox(
            mode_row,
            textvariable=self._claude_code_mode_var,
            values=["Paste", "Speak", "Both"],
            state="readonly",
            width=12,
        ).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(
            parent,
            text="Paste = insert at cursor, Speak = read aloud via TTS, Both = speak + paste.",
            foreground="#999999",
            font=("", 8),
        ).pack(fill=tk.X, pady=(0, 8))

        # Timeout
        timeout_row = ttk.Frame(parent)
        timeout_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(timeout_row, text="Timeout:", width=16, anchor=tk.W).pack(
            side=tk.LEFT
        )
        self._claude_code_timeout_var = tk.StringVar(value="120")
        ttk.Spinbox(
            timeout_row, from_=10, to=600, increment=10, width=6,
            textvariable=self._claude_code_timeout_var,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(timeout_row, text="seconds").pack(side=tk.LEFT, padx=(4, 0))

        # Skip permissions toggle
        self._claude_code_skip_perms_var = tk.BooleanVar()
        ttk.Checkbutton(
            parent,
            text="Skip permission prompts (--dangerously-skip-permissions)",
            variable=self._claude_code_skip_perms_var,
        ).pack(fill=tk.X, pady=(4, 0))
        ttk.Label(
            parent,
            text="Skips the tool-use allowlist. Only enable on trusted systems.",
            foreground="#FF9966",
            font=("", 8),
        ).pack(fill=tk.X, pady=(0, 8))

        # Continue conversation toggle
        self._claude_code_continue_var = tk.BooleanVar()
        ttk.Checkbutton(
            parent,
            text="Continue conversation (--continue)",
            variable=self._claude_code_continue_var,
        ).pack(fill=tk.X, pady=(4, 0))
        ttk.Label(
            parent,
            text="Subsequent calls maintain context. Use tray menu to start fresh.",
            foreground="#888888",
            font=("", 8),
        ).pack(fill=tk.X, pady=(0, 8))

        # Hotkey display (read-only)
        hotkey_row = ttk.Frame(parent)
        hotkey_row.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(hotkey_row, text="Hotkey:", width=16, anchor=tk.W).pack(
            side=tk.LEFT
        )
        self._claude_code_hotkey_label = ttk.Label(
            hotkey_row, text=self._config.claude_code_hotkey,
            foreground="#AAAAAA",
        )
        self._claude_code_hotkey_label.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            hotkey_row, text="(change in config.toml)",
            foreground="#666666", font=("", 8),
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(8, 8))

        # Test button
        ttk.Button(
            parent, text="Test Connection", width=16,
            command=self._test_claude_code,
        ).pack(anchor=tk.W)

    def _browse_claude_code_workdir(self) -> None:
        """Open a directory chooser for Claude Code working directory."""
        from tkinter import filedialog
        directory = filedialog.askdirectory(
            title="Select Claude Code Working Directory",
            initialdir=self._claude_code_workdir_var.get() or None,
        )
        if directory:
            self._claude_code_workdir_var.set(directory)

    def _test_claude_code(self) -> None:
        """Test Claude Code CLI availability and show result."""
        try:
            from claude_code import ClaudeCodeBackend
            if ClaudeCodeBackend.is_available():
                version = ClaudeCodeBackend.get_version() or "unknown version"
                self._claude_code_status_var.set(f"Found: {version}")
                self._claude_code_status_label.configure(foreground="#66BB6A")
            else:
                self._claude_code_status_var.set(
                    "Not found. Install: npm i -g @anthropic-ai/claude-code"
                )
                self._claude_code_status_label.configure(foreground="#FF6B6B")
        except Exception as e:
            self._claude_code_status_var.set(f"Error: {e}")
            self._claude_code_status_label.configure(foreground="#FF6B6B")

    def _populate_from_config(self) -> None:
        """Fill widget values from current config and keyring."""
        config = self._config

        # v0.4: Backend selector
        if config.stt_backend == "local":
            self._backend_var.set("Local (faster-whisper, offline)")
        else:
            self._backend_var.set("Cloud (OpenAI Whisper API)")

        # Transcription language
        from constants import SUPPORTED_LANGUAGES
        lang_code = config.transcription_language
        if lang_code in SUPPORTED_LANGUAGES:
            self._lang_var.set(SUPPORTED_LANGUAGES[lang_code])
        else:
            # Unknown language code -- show it directly
            self._lang_var.set(lang_code)

        # Audio input device
        device_idx = config.audio_device_index
        if device_idx is not None:
            # Find the matching entry in the device map
            for idx, label in self._audio_device_map:
                if idx == device_idx:
                    self._audio_device_var.set(label)
                    break
            else:
                # Device index not found — might be unplugged; show as default
                self._audio_device_var.set("System Default")
        else:
            self._audio_device_var.set("System Default")

        # OpenAI API Key
        if config.openai_api_key:
            self._openai_key_var.set(config.masked_api_key())
            self._openai_key_entry.config(show="", state="readonly")
            self._openai_key_btn.config(text="Edit")
            self._openai_key_editing = False
        else:
            self._openai_key_var.set("")
            self._openai_key_entry.config(show="*", state="normal")
            self._openai_key_btn.pack_forget()
            self._openai_key_editing = True

        # v0.4: Local STT fields
        from constants import LOCAL_MODEL_DISPLAY
        model_key = config.local_model_size
        if model_key in LOCAL_MODEL_DISPLAY:
            self._local_model_var.set(LOCAL_MODEL_DISPLAY[model_key]["label"])
        else:
            # Fall back to first option
            first_key = next(iter(LOCAL_MODEL_DISPLAY))
            self._local_model_var.set(LOCAL_MODEL_DISPLAY[first_key]["label"])

        self._local_device_var.set(config.local_device)

        # Update model status
        self._update_model_status()

        # Summarization enabled
        self._summarization_enabled_var.set(config.summarization_enabled)

        # Provider
        _provider_display_map = {"openai": "OpenAI", "openrouter": "OpenRouter", "ollama": "Ollama"}
        provider_display = _provider_display_map.get(config.summarization_provider, "OpenAI")
        self._provider_var.set(provider_display)

        # OpenRouter API Key
        if config.openrouter_api_key:
            self._openrouter_key_var.set(config.masked_api_key(config.openrouter_api_key))
            self._openrouter_key_entry.config(show="", state="readonly")
            self._openrouter_key_btn.config(text="Edit")
            self._openrouter_key_editing = False
        else:
            self._openrouter_key_var.set("")
            self._openrouter_key_entry.config(show="*", state="normal")
            self._openrouter_key_btn.pack_forget()
            self._openrouter_key_editing = True

        # Model
        self._model_var.set(config.summarization_model)

        # Base URL (show provider default if empty)
        if config.summarization_base_url:
            self._base_url_var.set(config.summarization_base_url)
        else:
            provider_key = config.summarization_provider
            self._base_url_var.set(
                _PROVIDER_DEFAULTS.get(provider_key, {}).get("base_url", "")
            )

        # Custom prompt
        if config.summarization_custom_prompt:
            self._prompt_text.insert("1.0", config.summarization_custom_prompt)
        else:
            # Show the default prompt as placeholder
            from constants import SUMMARIZE_SYSTEM_PROMPT
            self._prompt_text.insert("1.0", SUMMARIZE_SYSTEM_PROMPT)

        # v0.6/v0.7: TTS fields
        self._tts_enabled_var.set(config.tts_enabled)

        # v0.7: TTS backend selector
        if config.tts_provider == "piper":
            self._tts_backend_var.set("Local (Piper, offline)")
        else:
            self._tts_backend_var.set("Cloud (ElevenLabs API)")

        # ElevenLabs API key
        if config.elevenlabs_api_key:
            self._elevenlabs_key_var.set(config.masked_api_key(config.elevenlabs_api_key))
            self._elevenlabs_key_entry.config(show="", state="readonly")
            self._elevenlabs_key_btn.config(text="Edit")
            self._elevenlabs_key_editing = False
        else:
            self._elevenlabs_key_var.set("")
            self._elevenlabs_key_entry.config(show="*", state="normal")
            self._elevenlabs_key_btn.pack_forget()
            self._elevenlabs_key_editing = True

        # Voice selection
        from constants import ELEVENLABS_VOICE_PRESETS
        voice_found = False
        for vid, info in ELEVENLABS_VOICE_PRESETS.items():
            if vid == config.tts_voice_id:
                self._tts_voice_var.set(f"{info['name']} ({info['description']})")
                voice_found = True
                break
        if not voice_found:
            self._tts_voice_var.set("Custom (enter Voice ID below)")
        self._tts_voice_id_var.set(config.tts_voice_id)

        # Model selection
        if "multilingual" in config.tts_model_id:
            self._tts_model_var.set("eleven_multilingual_v2 (higher quality)")
        else:
            self._tts_model_var.set("eleven_flash_v2_5 (fast, low latency)")

        # v0.7: Piper voice selection
        from constants import PIPER_VOICE_MODELS
        piper_voice_key = config.tts_local_voice
        if piper_voice_key in PIPER_VOICE_MODELS:
            self._tts_piper_voice_var.set(
                PIPER_VOICE_MODELS[piper_voice_key]["label"]
            )
        else:
            # Fall back to first voice
            first_key = next(iter(PIPER_VOICE_MODELS))
            self._tts_piper_voice_var.set(
                PIPER_VOICE_MODELS[first_key]["label"]
            )

        # TTS speed
        self._tts_speed_var.set(str(config.tts_speed))

        # Update Piper model download status
        self._update_tts_model_status()

        # Show/hide TTS cloud vs local sub-frames
        self._update_tts_backend_ui()

        # TTS Cache fields
        self._tts_cache_enabled_var.set(config.tts_cache_enabled)
        self._tts_cache_max_size_var.set(str(config.tts_cache_max_size_mb))
        self._tts_cache_max_age_var.set(str(config.tts_cache_max_age_days))
        self._tts_cache_max_entries_var.set(str(config.tts_cache_max_entries))
        self._refresh_tts_cache_stats()

        # TTS Export fields
        self._tts_export_enabled_var.set(config.tts_export_enabled)
        self._tts_export_path_var.set(config.tts_export_path)

        # Enable/disable TTS widgets
        self._on_tts_toggled()

        # Hotkeys
        self._hotkey_label.config(text=config.hotkey)
        self._prompt_hotkey_label.config(text=config.prompt_hotkey)
        self._tts_hotkey_label.config(text=config.tts_hotkey)
        self._tts_ask_hotkey_label.config(text=config.tts_ask_hotkey)

        # Audio cues
        self._audio_cues_var.set(config.audio_cues_enabled)

        # v0.9: Paste confirmation/delay
        self._paste_confirm_var.set(config.paste_require_confirmation)
        self._paste_delay_var.set(str(config.paste_delay_seconds))
        self._paste_timeout_var.set(str(config.paste_confirmation_timeout))
        self._paste_auto_enter_var.set(config.paste_auto_enter)
        self._on_paste_confirm_toggled()

        # v0.9: API
        self._api_enabled_var.set(config.api_enabled)
        self._api_port_var.set(str(config.api_port))

        # v0.9: Hands-Free
        self._handsfree_enabled_var.set(config.handsfree_enabled)
        self._wake_phrase_var.set(config.wake_phrase)
        _match_display = {
            "contains": "contains (forgiving)",
            "startswith": "startswith (strict)",
            "fuzzy": "fuzzy (token overlap)",
        }
        self._match_mode_var.set(
            _match_display.get(config.wake_phrase_match_mode, "contains (forgiving)")
        )
        _pipeline_display = {
            "ask_tts": "Ask AI + TTS (ask_tts)",
            "summary": "Transcribe + Paste (summary)",
            "prompt": "Ask AI + Paste (prompt)",
            "claude_code": "Claude Code (claude_code)",
        }
        self._handsfree_pipeline_var.set(
            _pipeline_display.get(config.handsfree_pipeline, "Ask AI + TTS (ask_tts)")
        )
        self._silence_timeout_var.set(str(config.silence_timeout_seconds))
        self._hf_max_recording_var.set(str(config.handsfree_max_recording_seconds))
        self._hf_cooldown_var.set(str(config.handsfree_cooldown_seconds))

        # v1.2: Claude Code tab
        self._claude_code_enabled_var.set(config.claude_code_enabled)
        self._claude_code_workdir_var.set(config.claude_code_working_dir)
        self._claude_code_prompt_text.delete("1.0", self._tk.END)
        if config.claude_code_system_prompt:
            self._claude_code_prompt_text.insert("1.0", config.claude_code_system_prompt)
        _mode_display = {"paste": "Paste", "speak": "Speak", "both": "Both"}
        self._claude_code_mode_var.set(
            _mode_display.get(config.claude_code_response_mode, "Speak")
        )
        self._claude_code_timeout_var.set(str(config.claude_code_timeout))
        self._claude_code_skip_perms_var.set(config.claude_code_skip_permissions)
        self._claude_code_continue_var.set(config.claude_code_continue_conversation)

        # Show/hide backend sub-frames
        self._update_backend_ui()
        # Show/hide OpenRouter key based on provider
        self._update_provider_ui()
        # Enable/disable summarization widgets
        self._on_summarization_toggled()

    def _on_backend_changed(self, event=None) -> None:
        """Handle backend dropdown change. Show/hide cloud vs local controls."""
        self._update_backend_ui()
        self._update_model_status()

    def _update_backend_ui(self) -> None:
        """Show/hide cloud and local sub-frames based on backend selection."""
        backend = self._backend_var.get()
        tk = self._tk

        if "Local" in backend:
            self._cloud_frame.pack_forget()
            self._local_frame.pack(fill=tk.X, pady=(4, 0))
        else:
            self._local_frame.pack_forget()
            self._cloud_frame.pack(fill=tk.X, pady=(4, 0))

    def _update_model_status(self) -> None:
        """Update the model status label and download button based on current selection."""
        backend = self._backend_var.get()
        if "Local" not in backend:
            return

        # Don't update while download is in progress
        if self._download_thread and self._download_thread.is_alive():
            return

        model_key = self._get_selected_model_key()

        try:
            import model_manager
            if model_manager.is_model_available(model_key):
                self._model_status_label.config(
                    text="Model downloaded and ready.",
                    foreground="#66CC66",
                )
                self._download_btn.pack_forget()
                self._progress_frame.pack_forget()
                self._delete_btn.config(state="normal")
                self._delete_btn.pack(side=self._tk.LEFT, padx=(8, 0))
            else:
                info = model_manager.get_model_info(model_key)
                size_mb = info.get("download_mb", "?")
                self._model_status_label.config(
                    text=f"Not downloaded (~{size_mb} MB).",
                    foreground="#FFB347",
                )
                self._download_btn.config(text="Download Model", state="normal")
                self._download_btn.pack(side=self._tk.LEFT, padx=(8, 0))
                self._delete_btn.pack_forget()
                self._progress_frame.pack_forget()
        except Exception as e:
            logger.debug("Could not check model status: %s", e)
            self._model_status_label.config(
                text="Could not check model status.",
                foreground="#FF6B6B",
            )
            self._download_btn.pack_forget()
            self._delete_btn.pack_forget()

    def _on_model_size_changed(self, event=None) -> None:
        """Handle model size dropdown change. Refresh download status."""
        self._update_model_status()

    def _on_delete_clicked(self) -> None:
        """Handle Delete button click. Remove the downloaded model."""
        model_key = self._get_selected_model_key()

        # Confirm deletion
        from tkinter import messagebox
        confirmed = messagebox.askyesno(
            "Delete Model",
            f"Delete the '{model_key}' model?\n\n"
            f"You will need to download it again to use local transcription.",
            parent=self._dialog,
        )
        if not confirmed:
            return

        self._delete_btn.config(state="disabled")

        try:
            import model_manager
            success = model_manager.delete_model(model_key)
            if success:
                logger.info("Model '%s' deleted via Settings dialog.", model_key)
            else:
                logger.error("Failed to delete model '%s'.", model_key)
        except Exception as e:
            logger.error("Error deleting model '%s': %s", model_key, e)

        self._update_model_status()

    def _on_download_clicked(self) -> None:
        """Handle Download Model / Cancel button click."""
        if self._download_thread and self._download_thread.is_alive():
            # Cancel in progress
            self._download_cancel.set()
            self._download_btn.config(text="Cancelling...", state="disabled")
            return

        model_key = self._get_selected_model_key()

        # Reset events and progress counters
        self._download_cancel.clear()
        self._download_done.clear()
        self._download_success = False
        self._download_error_msg = ""
        self._download_bytes = 0
        self._download_total = 0
        self._download_poll_count = 0

        # Update UI to show progress
        self._download_btn.config(text="Cancel", state="normal")
        self._model_status_label.config(
            text="Downloading...", foreground="#66B3FF",
        )
        self._progress_frame.pack(
            fill=self._tk.X, pady=(0, 4),
            after=self._model_status_label.master,
        )
        self._progress_bar.config(value=0)
        self._progress_label.config(text=f"Connecting to Hugging Face...")

        # Disable model/device combos and hide delete button during download
        self._local_model_combo.config(state="disabled")
        self._local_device_combo.config(state="disabled")
        self._backend_combo.config(state="disabled")
        self._delete_btn.pack_forget()

        # Launch download in background thread
        self._download_thread = threading.Thread(
            target=self._download_model_thread,
            args=(model_key,),
            daemon=True,
            name="model-download",
        )
        self._download_thread.start()

        # Start polling for completion
        self._dialog.after(250, self._poll_download)

    def _on_download_progress(
        self, bytes_downloaded: int, total_bytes: int
    ) -> None:
        """Progress callback invoked from the download thread.

        Updates shared counters that the UI poll reads on the tkinter
        thread. This method is called from a background thread so it
        must not touch any tkinter widgets directly.

        Args:
            bytes_downloaded: Bytes downloaded so far for the current file.
            total_bytes: Total bytes for the current file being downloaded.
        """
        self._download_phase = "downloading"
        self._download_bytes = bytes_downloaded
        self._download_total = total_bytes

    def _download_model_thread(self, model_key: str) -> None:
        """Background thread: download the model.

        Passes our progress callback to model_manager.download_model()
        so the UI can show real download progress.

        Args:
            model_key: The model size key (e.g., "base").
        """
        try:
            import model_manager
            logger.info(
                "Starting model download: model=%s, cache_dir=%s",
                model_key,
                model_manager.get_cache_dir(),
            )
            self._download_success = model_manager.download_model(
                model_key,
                on_progress=self._on_download_progress,
                cancel_event=self._download_cancel,
            )
            if not self._download_success and not self._download_cancel.is_set():
                self._download_error_msg = (
                    "Download failed. Check your internet connection "
                    "and try again. Run with --verbose for details."
                )
        except Exception as e:
            logger.error(
                "Model download failed: %s: %s", type(e).__name__, e,
                exc_info=True,
            )
            self._download_success = False
            self._download_error_msg = f"{type(e).__name__}: {e}"
        finally:
            self._download_done.set()

    def _reset_download_ui(self) -> None:
        """Reset download UI controls to idle state."""
        self._progress_bar.config(mode="determinate", value=0)
        self._progress_frame.pack_forget()
        self._local_model_combo.config(state="readonly")
        self._local_device_combo.config(state="readonly")
        self._backend_combo.config(state="readonly")

    def _poll_download(self) -> None:
        """Poll the download thread from the tkinter thread.

        Called via ``after()`` every 250ms. Updates the progress bar and
        label with real download progress, then checks if the download
        is complete.
        """
        # Respond to cancel immediately — don't wait for download thread
        if self._download_cancel.is_set() and not self._download_done.is_set():
            self._reset_download_ui()
            self._model_status_label.config(
                text="Download cancelled.", foreground="#FFB347",
            )
            self._download_btn.config(text="Download Model", state="normal")
            self._download_thread = None
            return

        if not self._download_done.is_set():
            self._download_poll_count += 1
            # Update progress bar from shared counters
            total = self._download_total
            downloaded = self._download_bytes
            if total > 0:
                pct = min(100.0, (downloaded / total) * 100.0)
                self._progress_bar.config(mode="determinate", value=pct)
                downloaded_mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                self._progress_label.config(
                    text=f"Downloading: {downloaded_mb:.1f} / {total_mb:.1f} MB "
                    f"({pct:.0f}%)"
                )
            else:
                # No data yet — show elapsed time so user knows it's working
                elapsed_s = self._download_poll_count * 250 // 1000
                self._progress_bar.config(mode="indeterminate")
                self._progress_bar.step(2)
                if elapsed_s >= 30:
                    self._progress_label.config(
                        text=f"Connection slow ({elapsed_s}s). "
                        f"Check internet or try again."
                    )
                else:
                    self._progress_label.config(
                        text=f"Connecting to Hugging Face... ({elapsed_s}s)"
                    )
            # Still downloading -- schedule next poll
            self._dialog.after(250, self._poll_download)
            return

        # Download finished -- update UI
        self._reset_download_ui()

        if self._download_cancel.is_set():
            self._model_status_label.config(
                text="Download cancelled.", foreground="#FFB347",
            )
            self._download_btn.config(text="Download Model", state="normal")
        elif self._download_success:
            self._model_status_label.config(
                text="Model downloaded and ready.",
                foreground="#66CC66",
            )
            self._download_btn.pack_forget()
        else:
            # Show error detail if available
            error_hint = self._download_error_msg or (
                "Check logs or run with --verbose and try again."
            )
            # Truncate for the label
            if len(error_hint) > 100:
                error_hint = error_hint[:97] + "..."
            self._model_status_label.config(
                text=f"Download failed. {error_hint}",
                foreground="#FF6B6B",
            )
            self._download_btn.config(text="Retry Download", state="normal")

        self._download_thread = None

    def _get_selected_model_key(self) -> str:
        """Return the model size key (e.g., 'base') from the selected display label."""
        from constants import LOCAL_MODEL_DISPLAY
        selected_label = self._local_model_var.get()
        for key, info in LOCAL_MODEL_DISPLAY.items():
            if info["label"] == selected_label:
                return key
        return "base"  # fallback

    def _update_provider_ui(self) -> None:
        """Show/hide the OpenRouter API key field based on provider selection."""
        provider = self._provider_var.get()
        if provider == "OpenRouter":
            self._openrouter_key_frame.pack(
                fill=self._tk.X, pady=(4, 4),
                after=self._provider_combo.master,
            )
        else:
            self._openrouter_key_frame.pack_forget()

    def _on_provider_changed(self, event=None) -> None:
        """Handle provider dropdown change."""
        provider = self._provider_var.get()
        _key_map = {"OpenAI": "openai", "OpenRouter": "openrouter", "Ollama": "ollama"}
        provider_key = _key_map.get(provider, "openai")

        defaults = _PROVIDER_DEFAULTS.get(provider_key, {})
        self._model_var.set(defaults.get("model", ""))
        self._base_url_var.set(defaults.get("base_url", ""))

        self._update_provider_ui()

    def _on_summarization_toggled(self) -> None:
        """Enable/disable summarization widgets based on checkbox."""
        enabled = self._summarization_enabled_var.get()

        for widget in self._summarization_widgets:
            try:
                if enabled:
                    if widget == self._provider_combo:
                        widget.config(state="readonly")
                    elif widget == self._prompt_text:
                        widget.config(state="normal",
                                      bg=self._text_bg, fg=self._text_fg)
                    elif widget in (self._openrouter_key_entry,):
                        # Respect editing state
                        if self._openrouter_key_editing:
                            widget.config(state="normal")
                        else:
                            widget.config(state="readonly")
                    else:
                        widget.config(state="normal")
                else:
                    if widget == self._prompt_text:
                        # tk.Text has no disabledbackground — set manually
                        widget.config(state="disabled",
                                      bg=_DARK_COLORS["disabled_bg"],
                                      fg=_DARK_COLORS["disabled_fg"])
                    else:
                        widget.config(state="disabled")
            except Exception:
                pass  # Some widgets may not support state changes

    def _toggle_openai_key_edit(self) -> None:
        """Toggle between showing masked key and editing."""
        if self._openai_key_editing:
            # Cancel editing - revert to masked display
            if self._openai_key_actual:
                self._openai_key_var.set(self._config.masked_api_key())
                self._openai_key_entry.config(show="", state="readonly")
                self._openai_key_btn.config(text="Edit")
                self._openai_key_editing = False
        else:
            # Start editing
            self._openai_key_var.set("")
            self._openai_key_entry.config(show="*", state="normal")
            self._openai_key_entry.focus_set()
            self._openai_key_btn.config(text="Cancel")
            self._openai_key_editing = True

    def _toggle_openrouter_key_edit(self) -> None:
        """Toggle between showing masked key and editing."""
        if self._openrouter_key_editing:
            # Cancel editing - revert to masked display
            if self._openrouter_key_actual:
                self._openrouter_key_var.set(
                    self._config.masked_api_key(self._config.openrouter_api_key)
                )
                self._openrouter_key_entry.config(show="", state="readonly")
                self._openrouter_key_btn.config(text="Edit")
                self._openrouter_key_editing = False
        else:
            # Start editing
            self._openrouter_key_var.set("")
            self._openrouter_key_entry.config(show="*", state="normal")
            self._openrouter_key_entry.focus_set()
            self._openrouter_key_btn.config(text="Cancel")
            self._openrouter_key_editing = True

    def _on_paste_confirm_toggled(self) -> None:
        """Enable/disable paste delay vs timeout based on confirmation checkbox."""
        confirm = self._paste_confirm_var.get()
        if confirm:
            # Confirmation mode: timeout is relevant, delay is not
            self._paste_delay_spin.config(state="disabled")
            self._paste_delay_label.config(foreground="#999999")
            self._paste_timeout_spin.config(state="normal")
            self._paste_timeout_label.config(foreground="")
        else:
            # Delay mode: delay is relevant, timeout is not
            self._paste_delay_spin.config(state="normal")
            self._paste_delay_label.config(foreground="")
            self._paste_timeout_spin.config(state="disabled")
            self._paste_timeout_label.config(foreground="#999999")

    def _on_tts_toggled(self) -> None:
        """Enable/disable TTS widgets based on checkbox.

        When TTS is disabled, the backend dropdown and all sub-frame widgets
        are disabled. When enabled, the backend dropdown is set to readonly
        and the visible sub-frame widgets are enabled according to their type.
        """
        enabled = self._tts_enabled_var.get()
        is_local = "Local" in self._tts_backend_var.get()

        for widget in self._tts_widgets:
            try:
                if enabled:
                    # Comboboxes -> readonly
                    if widget in (
                        self._tts_backend_combo,
                        self._tts_voice_combo,
                        self._tts_model_combo,
                        self._tts_piper_voice_combo,
                    ):
                        widget.config(state="readonly")
                    elif widget == self._elevenlabs_key_entry:
                        if self._elevenlabs_key_editing:
                            widget.config(state="normal")
                        else:
                            widget.config(state="readonly")
                    elif widget in (self._tts_download_btn, self._tts_delete_btn):
                        # Only enable download/delete if local backend is active
                        # and model status warrants it (handled by _update_tts_model_status)
                        if is_local:
                            widget.config(state="normal")
                        else:
                            widget.config(state="disabled")
                    else:
                        widget.config(state="normal")
                else:
                    widget.config(state="disabled")
            except Exception:
                pass

        # Refresh model status buttons when re-enabling with local backend
        if enabled and is_local:
            self._update_tts_model_status()

        # Refresh Voice ID entry state (readonly for presets, normal for custom)
        if enabled and not is_local:
            self._on_tts_voice_changed()

    def _on_tts_voice_changed(self, event=None) -> None:
        """Handle TTS voice dropdown change. Update voice ID field."""
        selected = self._tts_voice_var.get()
        if "Custom" in selected:
            # Let user type their own voice ID
            self._tts_voice_id_entry.config(state="normal")
            return

        from constants import ELEVENLABS_VOICE_PRESETS
        for vid, info in ELEVENLABS_VOICE_PRESETS.items():
            display = f"{info['name']} ({info['description']})"
            if display == selected:
                self._tts_voice_id_var.set(vid)
                # Make voice ID readonly when a preset is selected
                self._tts_voice_id_entry.config(state="readonly")
                return

    def _toggle_elevenlabs_key_edit(self) -> None:
        """Toggle between showing masked ElevenLabs key and editing."""
        if self._elevenlabs_key_editing:
            if self._elevenlabs_key_actual:
                self._elevenlabs_key_var.set(
                    self._config.masked_api_key(self._config.elevenlabs_api_key)
                )
                self._elevenlabs_key_entry.config(show="", state="readonly")
                self._elevenlabs_key_btn.config(text="Edit")
                self._elevenlabs_key_editing = False
        else:
            self._elevenlabs_key_var.set("")
            self._elevenlabs_key_entry.config(show="*", state="normal")
            self._elevenlabs_key_entry.focus_set()
            self._elevenlabs_key_btn.config(text="Cancel")
            self._elevenlabs_key_editing = True

    def _on_tts_backend_changed(self, event=None) -> None:
        """Handle TTS backend dropdown change. Show/hide cloud vs local controls."""
        self._update_tts_backend_ui()
        self._on_tts_toggled()
        self._update_tts_model_status()

    def _update_tts_backend_ui(self) -> None:
        """Show/hide TTS cloud and local sub-frames based on backend selection."""
        backend = self._tts_backend_var.get()
        tk = self._tk

        if "Local" in backend:
            self._tts_cloud_frame.pack_forget()
            self._tts_local_frame.pack(fill=tk.X, pady=(4, 0))
        else:
            self._tts_local_frame.pack_forget()
            self._tts_cloud_frame.pack(fill=tk.X, pady=(4, 0))

    def _get_selected_tts_voice_key(self) -> str:
        """Return the Piper voice key from the selected display label.

        Returns:
            Voice name key (e.g., 'de_DE-thorsten-medium'), or empty string
            if no match is found.
        """
        from constants import PIPER_VOICE_MODELS
        selected_label = self._tts_piper_voice_var.get()
        for key, info in PIPER_VOICE_MODELS.items():
            if info["label"] == selected_label:
                return key
        return ""

    def _on_tts_piper_voice_changed(self, event=None) -> None:
        """Handle Piper voice dropdown change. Refresh download status."""
        self._update_tts_model_status()

    def _get_tts_cache_dir(self) -> "Path":
        """Return the TTS cache directory path.

        Uses platform_impl.get_cache_dir() for cross-platform support.

        Returns:
            Path to the TTS cache directory.
        """
        from platform_impl import get_cache_dir as _platform_cache_dir
        return _platform_cache_dir() / "cache" / "tts"

    def _refresh_tts_cache_stats(self) -> None:
        """Read TTS cache stats from disk and update the usage label.

        Reads the index.json file directly to avoid importing the full
        TTSAudioCache module (which would duplicate the lock / instance).
        Falls back gracefully if the cache directory or index does not exist.
        """
        try:
            import json
            cache_dir = self._get_tts_cache_dir()
            index_path = cache_dir / "index.json"

            if not index_path.exists():
                self._tts_cache_usage_label.config(
                    text="Empty (no cached files)", foreground="#999999",
                )
                return

            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)

            entries = index.get("entries", {})
            total_bytes = sum(
                e.get("file_size_bytes", 0) for e in entries.values()
            )
            total_mb = total_bytes / (1024 * 1024)
            count = len(entries)

            self._tts_cache_usage_label.config(
                text=f"{count} entries, {total_mb:.1f} MB",
                foreground="#e0e0e0",
            )
        except Exception as e:
            logger.debug("Could not read TTS cache stats: %s", e)
            self._tts_cache_usage_label.config(
                text="Could not read cache stats", foreground="#FF6B6B",
            )

    def _on_tts_cache_clear_clicked(self) -> None:
        """Handle Clear Cache button click. Remove all cached TTS audio files.

        Prompts for confirmation, then deletes all files in the TTS cache
        directory and removes the index.json. Refreshes the usage label after.
        """
        from tkinter import messagebox
        confirmed = messagebox.askyesno(
            "Clear TTS Cache",
            "Delete all cached TTS audio files?\n\n"
            "This cannot be undone. New audio will be re-generated on demand.",
            parent=self._dialog,
        )
        if not confirmed:
            return

        try:
            cache_dir = self._get_tts_cache_dir()
            removed = 0
            if cache_dir.exists():
                for f in cache_dir.iterdir():
                    if f.is_file():
                        try:
                            f.unlink()
                            removed += 1
                        except OSError:
                            pass

            logger.info("TTS cache cleared: %d files removed.", removed)
            self._refresh_tts_cache_stats()
        except Exception as e:
            logger.warning("Failed to clear TTS cache: %s", e)
            from tkinter import messagebox as mb
            mb.showerror(
                "Error", f"Failed to clear cache:\n{e}", parent=self._dialog,
            )

    def _on_tts_cache_open_folder(self) -> None:
        """Open the TTS cache folder in the system file manager."""
        try:
            cache_dir = self._get_tts_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            import os
            import sys
            if sys.platform == "win32":
                os.startfile(str(cache_dir))
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(cache_dir)])
        except Exception as e:
            logger.warning("Failed to open TTS cache folder: %s", e)

    def _on_tts_export_browse(self) -> None:
        """Open a folder picker dialog for the TTS export path.

        Uses tkinter's askdirectory dialog. Sets the selected path into
        the export path entry field.
        """
        from tkinter import filedialog
        current = self._tts_export_path_var.get().strip()
        initial_dir = current if current else None
        folder = filedialog.askdirectory(
            title="Select TTS Export Folder",
            initialdir=initial_dir,
            parent=self._dialog,
        )
        if folder:
            self._tts_export_path_var.set(folder)

    def _update_tts_model_status(self) -> None:
        """Update the Piper model status label and buttons based on selection.

        Checks whether the currently selected Piper voice model is downloaded
        and updates the status label, download button, and delete button
        accordingly. Skipped when a download is in progress.
        """
        backend = self._tts_backend_var.get()
        if "Local" not in backend:
            return

        # Don't update while download is in progress
        if self._tts_download_thread and self._tts_download_thread.is_alive():
            return

        voice_key = self._get_selected_tts_voice_key()
        if not voice_key:
            self._tts_model_status_label.config(
                text="No voice selected.", foreground="#FFB347",
            )
            self._tts_download_btn.pack_forget()
            self._tts_delete_btn.pack_forget()
            return

        try:
            import tts_model_manager
            if tts_model_manager.is_tts_model_available(voice_key):
                self._tts_model_status_label.config(
                    text="Downloaded and ready.",
                    foreground="#66CC66",
                )
                self._tts_download_btn.pack_forget()
                self._tts_progress_frame.pack_forget()
                self._tts_delete_btn.config(state="normal")
                self._tts_delete_btn.pack(side=self._tk.LEFT, padx=(8, 0))
            else:
                from constants import PIPER_VOICE_MODELS
                info = PIPER_VOICE_MODELS.get(voice_key, {})
                size_mb = info.get("download_mb", "?")
                self._tts_model_status_label.config(
                    text=f"Not downloaded (~{size_mb} MB).",
                    foreground="#FFB347",
                )
                self._tts_download_btn.config(
                    text="Download Model", state="normal"
                )
                self._tts_download_btn.pack(
                    side=self._tk.LEFT, padx=(8, 0)
                )
                self._tts_delete_btn.pack_forget()
                self._tts_progress_frame.pack_forget()
        except Exception as e:
            logger.debug("Could not check TTS model status: %s", e)
            self._tts_model_status_label.config(
                text="Could not check model status.",
                foreground="#FF6B6B",
            )
            self._tts_download_btn.pack_forget()
            self._tts_delete_btn.pack_forget()

    def _on_tts_download_clicked(self) -> None:
        """Handle TTS Download Model / Cancel button click.

        If a download is in progress, signals cancellation. Otherwise starts
        a background download thread and begins UI polling.
        """
        if self._tts_download_thread and self._tts_download_thread.is_alive():
            # Cancel in progress
            self._tts_download_cancel.set()
            self._tts_download_btn.config(
                text="Cancelling...", state="disabled"
            )
            return

        voice_key = self._get_selected_tts_voice_key()
        if not voice_key:
            return

        # Reset events and progress counters
        self._tts_download_cancel.clear()
        self._tts_download_done.clear()
        self._tts_download_success = False
        self._tts_download_error_msg = ""
        self._tts_download_bytes = 0
        self._tts_download_total = 0
        self._tts_download_poll_count = 0

        # Update UI to show progress
        self._tts_download_btn.config(text="Cancel", state="normal")
        self._tts_model_status_label.config(
            text="Downloading...", foreground="#66B3FF",
        )
        self._tts_progress_frame.pack(
            fill=self._tk.X, pady=(0, 4),
            after=self._tts_model_status_label.master,
        )
        self._tts_progress_bar.config(value=0)
        self._tts_progress_label.config(text="Connecting to Hugging Face...")

        # Disable voice combo and backend combo during download
        self._tts_piper_voice_combo.config(state="disabled")
        self._tts_backend_combo.config(state="disabled")
        self._tts_delete_btn.pack_forget()

        # Launch download in background thread
        self._tts_download_thread = threading.Thread(
            target=self._tts_download_model_thread,
            args=(voice_key,),
            daemon=True,
            name="tts-model-download",
        )
        self._tts_download_thread.start()

        # Start polling for completion
        self._dialog.after(250, self._tts_poll_download)

    def _on_tts_download_progress(
        self, bytes_downloaded: int, total_bytes: int
    ) -> None:
        """Progress callback invoked from the TTS download thread.

        Updates shared counters that the UI poll reads on the tkinter
        thread. Called from a background thread -- must not touch widgets.

        Args:
            bytes_downloaded: Bytes downloaded so far for the current file.
            total_bytes: Total bytes for the current file being downloaded.
        """
        self._tts_download_bytes = bytes_downloaded
        self._tts_download_total = total_bytes

    def _tts_download_model_thread(self, voice_key: str) -> None:
        """Background thread: download the Piper TTS model.

        Args:
            voice_key: The Piper voice name (e.g., "de_DE-thorsten-medium").
        """
        try:
            import tts_model_manager
            logger.info(
                "Starting TTS model download: voice=%s, cache_dir=%s",
                voice_key,
                tts_model_manager.get_tts_cache_dir(),
            )
            self._tts_download_success = tts_model_manager.download_tts_model(
                voice_key,
                on_progress=self._on_tts_download_progress,
                cancel_event=self._tts_download_cancel,
            )
            if (
                not self._tts_download_success
                and not self._tts_download_cancel.is_set()
            ):
                self._tts_download_error_msg = (
                    "Download failed. Check your internet connection "
                    "and try again. Run with --verbose for details."
                )
        except Exception as e:
            logger.error(
                "TTS model download failed: %s: %s",
                type(e).__name__,
                e,
                exc_info=True,
            )
            self._tts_download_success = False
            self._tts_download_error_msg = f"{type(e).__name__}: {e}"
        finally:
            self._tts_download_done.set()

    def _reset_tts_download_ui(self) -> None:
        """Reset TTS download UI controls to idle state."""
        self._tts_progress_bar.config(mode="determinate", value=0)
        self._tts_progress_frame.pack_forget()
        self._tts_piper_voice_combo.config(state="readonly")
        self._tts_backend_combo.config(state="readonly")

    def _tts_poll_download(self) -> None:
        """Poll the TTS download thread from the tkinter thread.

        Called via ``after()`` every 250ms. Updates the progress bar and
        label, then checks if the download is complete.
        """
        # Respond to cancel immediately
        if (
            self._tts_download_cancel.is_set()
            and not self._tts_download_done.is_set()
        ):
            self._reset_tts_download_ui()
            self._tts_model_status_label.config(
                text="Download cancelled.", foreground="#FFB347",
            )
            self._tts_download_btn.config(
                text="Download Model", state="normal"
            )
            self._tts_download_thread = None
            return

        if not self._tts_download_done.is_set():
            self._tts_download_poll_count += 1
            total = self._tts_download_total
            downloaded = self._tts_download_bytes
            if total > 0:
                pct = min(100.0, (downloaded / total) * 100.0)
                self._tts_progress_bar.config(
                    mode="determinate", value=pct
                )
                downloaded_mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                self._tts_progress_label.config(
                    text=f"Downloading: {downloaded_mb:.1f} / {total_mb:.1f} MB "
                    f"({pct:.0f}%)"
                )
            else:
                elapsed_s = self._tts_download_poll_count * 250 // 1000
                self._tts_progress_bar.config(mode="indeterminate")
                self._tts_progress_bar.step(2)
                if elapsed_s >= 30:
                    self._tts_progress_label.config(
                        text=f"Connection slow ({elapsed_s}s). "
                        f"Check internet or try again."
                    )
                else:
                    self._tts_progress_label.config(
                        text=f"Connecting to Hugging Face... ({elapsed_s}s)"
                    )
            self._dialog.after(250, self._tts_poll_download)
            return

        # Download finished -- update UI
        self._reset_tts_download_ui()

        if self._tts_download_cancel.is_set():
            self._tts_model_status_label.config(
                text="Download cancelled.", foreground="#FFB347",
            )
            self._tts_download_btn.config(
                text="Download Model", state="normal"
            )
        elif self._tts_download_success:
            self._tts_model_status_label.config(
                text="Downloaded and ready.",
                foreground="#66CC66",
            )
            self._tts_download_btn.pack_forget()
        else:
            error_hint = self._tts_download_error_msg or (
                "Check logs or run with --verbose and try again."
            )
            if len(error_hint) > 100:
                error_hint = error_hint[:97] + "..."
            self._tts_model_status_label.config(
                text=f"Download failed. {error_hint}",
                foreground="#FF6B6B",
            )
            self._tts_download_btn.config(
                text="Retry Download", state="normal"
            )

        self._tts_download_thread = None
        # Refresh status to show correct buttons (download vs delete)
        self._update_tts_model_status()

    def _on_tts_delete_clicked(self) -> None:
        """Handle TTS Delete button click. Remove the downloaded Piper model."""
        voice_key = self._get_selected_tts_voice_key()
        if not voice_key:
            return

        from tkinter import messagebox
        confirmed = messagebox.askyesno(
            "Delete TTS Voice",
            f"Delete the '{voice_key}' voice model?\n\n"
            f"You will need to download it again to use local TTS.",
            parent=self._dialog,
        )
        if not confirmed:
            return

        self._tts_delete_btn.config(state="disabled")

        try:
            import tts_model_manager
            success = tts_model_manager.delete_tts_model(voice_key)
            if success:
                logger.info(
                    "TTS model '%s' deleted via Settings dialog.", voice_key
                )
            else:
                logger.error(
                    "Failed to delete TTS model '%s'.", voice_key
                )
        except Exception as e:
            logger.error(
                "Error deleting TTS model '%s': %s", voice_key, e
            )

        self._update_tts_model_status()

    def _reset_prompt(self) -> None:
        """Reset the custom prompt to the default."""
        from constants import SUMMARIZE_SYSTEM_PROMPT
        self._prompt_text.delete("1.0", self._tk.END)
        self._prompt_text.insert("1.0", SUMMARIZE_SYSTEM_PROMPT)

    def _validate(self) -> Optional[str]:
        """Validate all fields.

        Returns:
            Error message string if validation fails, None if OK.
        """
        backend = self._backend_var.get()
        is_cloud = "Cloud" in backend

        # Transcription API key (only required for cloud backend)
        if is_cloud:
            if self._openai_key_editing:
                key = self._openai_key_var.get().strip()
                if not key:
                    self._openai_key_entry.focus_set()
                    return "Transcription API key is required for cloud mode."
                if not key.startswith("sk-"):
                    self._openai_key_entry.focus_set()
                    return 'API key should start with "sk-". Check your key.'
            elif not self._openai_key_actual:
                return "Transcription API key is required for cloud mode. Click Edit to enter one."

        # Summarization
        if self._summarization_enabled_var.get():
            provider = self._provider_var.get()

            # OpenRouter key required
            if provider == "OpenRouter":
                if self._openrouter_key_editing:
                    or_key = self._openrouter_key_var.get().strip()
                    if not or_key:
                        self._openrouter_key_entry.focus_set()
                        return "OpenRouter API key is required when OpenRouter is selected."
                elif not self._openrouter_key_actual:
                    return "OpenRouter API key is required. Click Edit to enter one."

            # Model required
            if not self._model_var.get().strip():
                self._model_entry.focus_set()
                return "Model name is required."

            # Base URL required and must be https/http
            base_url = self._base_url_var.get().strip()
            if not base_url:
                self._base_url_entry.focus_set()
                return "Base URL is required."
            # SEC-014: Enforce HTTPS only (REQ-S06), except for local services
            is_local = ("localhost" in base_url or "127.0.0.1" in base_url)
            if not base_url.startswith("https://") and not is_local:
                self._base_url_entry.focus_set()
                return "Base URL must start with https:// for security (except localhost)."

        # TTS validation (v0.6, v0.7 local Piper)
        if self._tts_enabled_var.get():
            tts_backend = self._tts_backend_var.get()
            is_tts_cloud = "Cloud" in tts_backend

            if is_tts_cloud:
                # Cloud (ElevenLabs) -- require API key and voice ID
                if self._elevenlabs_key_editing:
                    el_key = self._elevenlabs_key_var.get().strip()
                    if not el_key:
                        self._elevenlabs_key_entry.focus_set()
                        return "ElevenLabs API key is required for cloud TTS."
                elif not self._elevenlabs_key_actual:
                    return "ElevenLabs API key is required for cloud TTS. Click Edit to enter one."

                voice_id = self._tts_voice_id_var.get().strip()
                if not voice_id:
                    self._tts_voice_id_entry.focus_set()
                    return "Voice ID is required for cloud TTS."
            else:
                # Local (Piper) -- just require a voice selection
                piper_voice = self._get_selected_tts_voice_key()
                if not piper_voice:
                    return "Please select a Piper voice for local TTS."

        return None

    def _on_save_clicked(self) -> None:
        """Handle Save button click."""
        # Hide any previous error
        self._error_label.pack_forget()

        # Validate
        error = self._validate()
        if error:
            self._error_label.config(text=error)
            self._error_label.pack(
                fill=self._tk.X, pady=(8, 0), before=self._button_frame
            )
            return

        changed_fields: dict[str, Any] = {}
        config = self._config

        # --- Collect new values ---

        # v0.4: Backend
        new_backend = "local" if "Local" in self._backend_var.get() else "cloud"
        if new_backend != config.stt_backend:
            changed_fields["stt_backend"] = new_backend
            config.stt_backend = new_backend

        # Transcription language
        lang_display = self._lang_var.get()
        try:
            lang_idx = self._lang_display_values.index(lang_display)
            new_language = self._lang_code_keys[lang_idx]
        except (ValueError, IndexError):
            new_language = lang_display  # Direct code if not in display list
        if new_language != config.transcription_language:
            changed_fields["transcription_language"] = new_language
            config.transcription_language = new_language

        # Audio input device
        device_label = self._audio_device_var.get()
        new_device_index = None  # default
        for idx, label in self._audio_device_map:
            if label == device_label:
                new_device_index = idx
                break
        if new_device_index != config.audio_device_index:
            changed_fields["audio_device_index"] = new_device_index
            config.audio_device_index = new_device_index

        # v0.4: Local STT fields
        new_model_size = self._get_selected_model_key()
        if new_model_size != config.local_model_size:
            changed_fields["local_model_size"] = new_model_size
            config.local_model_size = new_model_size

        new_device = self._local_device_var.get()
        if new_device != config.local_device:
            changed_fields["local_device"] = new_device
            config.local_device = new_device

        # Compute type derived from device selection
        new_compute_type = "float16" if new_device == "cuda" else "int8"
        if new_compute_type != config.local_compute_type:
            changed_fields["local_compute_type"] = new_compute_type
            config.local_compute_type = new_compute_type

        # OpenAI API key
        if self._openai_key_editing:
            new_openai_key = self._openai_key_var.get().strip()
            if new_openai_key != config.openai_api_key:
                changed_fields["openai_api_key"] = new_openai_key
                config.openai_api_key = new_openai_key
                # Store in keyring
                try:
                    import keyring_store
                    from constants import KEYRING_OPENAI_KEY
                    keyring_store.set_credential(KEYRING_OPENAI_KEY, new_openai_key)
                except Exception as e:
                    logger.warning("Failed to store OpenAI key in keyring: %s", e)

        # Summarization enabled
        new_enabled = self._summarization_enabled_var.get()
        if new_enabled != config.summarization_enabled:
            changed_fields["summarization_enabled"] = new_enabled
            config.summarization_enabled = new_enabled

        # Provider
        provider_display = self._provider_var.get()
        _save_key_map = {"OpenAI": "openai", "OpenRouter": "openrouter", "Ollama": "ollama"}
        new_provider = _save_key_map.get(provider_display, "openai")
        if new_provider != config.summarization_provider:
            changed_fields["summarization_provider"] = new_provider
            config.summarization_provider = new_provider

        # OpenRouter API key
        if self._openrouter_key_editing:
            new_or_key = self._openrouter_key_var.get().strip()
            if new_or_key != config.openrouter_api_key:
                changed_fields["openrouter_api_key"] = new_or_key
                config.openrouter_api_key = new_or_key
                try:
                    import keyring_store
                    from constants import KEYRING_OPENROUTER_KEY
                    keyring_store.set_credential(KEYRING_OPENROUTER_KEY, new_or_key)
                except Exception as e:
                    logger.warning("Failed to store OpenRouter key in keyring: %s", e)

        # Model
        new_model = self._model_var.get().strip()
        if new_model != config.summarization_model:
            changed_fields["summarization_model"] = new_model
            config.summarization_model = new_model

        # Base URL (store empty if it matches provider default)
        new_base_url = self._base_url_var.get().strip()
        provider_key = new_provider
        default_url = _PROVIDER_DEFAULTS.get(provider_key, {}).get("base_url", "")
        if new_base_url == default_url:
            new_base_url = ""  # Store empty = use provider default
        if new_base_url != config.summarization_base_url:
            changed_fields["summarization_base_url"] = new_base_url
            config.summarization_base_url = new_base_url

        # Custom prompt
        new_prompt = self._prompt_text.get("1.0", self._tk.END).strip()
        # If the prompt is the default, store empty
        from constants import SUMMARIZE_SYSTEM_PROMPT
        if new_prompt == SUMMARIZE_SYSTEM_PROMPT.strip():
            new_prompt = ""
        if new_prompt != config.summarization_custom_prompt:
            changed_fields["summarization_custom_prompt"] = new_prompt
            config.summarization_custom_prompt = new_prompt

        # v0.6/v0.7: TTS fields
        new_tts_enabled = self._tts_enabled_var.get()
        if new_tts_enabled != config.tts_enabled:
            changed_fields["tts_enabled"] = new_tts_enabled
            config.tts_enabled = new_tts_enabled

        # v0.7: TTS provider (cloud = elevenlabs, local = piper)
        new_tts_provider = (
            "piper" if "Local" in self._tts_backend_var.get() else "elevenlabs"
        )
        if new_tts_provider != config.tts_provider:
            changed_fields["tts_provider"] = new_tts_provider
            config.tts_provider = new_tts_provider

        # ElevenLabs API key
        if self._elevenlabs_key_editing:
            new_el_key = self._elevenlabs_key_var.get().strip()
            if new_el_key != config.elevenlabs_api_key:
                changed_fields["elevenlabs_api_key"] = new_el_key
                config.elevenlabs_api_key = new_el_key
                try:
                    import keyring_store
                    from constants import KEYRING_ELEVENLABS_KEY
                    keyring_store.set_credential(KEYRING_ELEVENLABS_KEY, new_el_key)
                except Exception as e:
                    logger.warning("Failed to store ElevenLabs key in keyring: %s", e)

        # Voice ID (ElevenLabs cloud)
        new_voice_id = self._tts_voice_id_var.get().strip()
        if new_voice_id and new_voice_id != config.tts_voice_id:
            changed_fields["tts_voice_id"] = new_voice_id
            config.tts_voice_id = new_voice_id

        # Model ID (ElevenLabs cloud)
        new_model_display = self._tts_model_var.get()
        if "multilingual" in new_model_display:
            new_model_id = "eleven_multilingual_v2"
        else:
            new_model_id = "eleven_flash_v2_5"
        if new_model_id != config.tts_model_id:
            changed_fields["tts_model_id"] = new_model_id
            config.tts_model_id = new_model_id

        # v0.7: Local TTS voice (Piper)
        new_tts_local_voice = self._get_selected_tts_voice_key()
        if new_tts_local_voice and new_tts_local_voice != config.tts_local_voice:
            changed_fields["tts_local_voice"] = new_tts_local_voice
            config.tts_local_voice = new_tts_local_voice

        # TTS speed
        try:
            new_tts_speed = float(self._tts_speed_var.get())
            new_tts_speed = max(0.5, min(new_tts_speed, 2.0))
        except (ValueError, TypeError):
            new_tts_speed = config.tts_speed
        if new_tts_speed != config.tts_speed:
            changed_fields["tts_speed"] = new_tts_speed
            config.tts_speed = new_tts_speed

        # TTS Cache settings
        new_cache_enabled = self._tts_cache_enabled_var.get()
        if new_cache_enabled != config.tts_cache_enabled:
            changed_fields["tts_cache_enabled"] = new_cache_enabled
            config.tts_cache_enabled = new_cache_enabled

        try:
            new_cache_max_size = int(self._tts_cache_max_size_var.get())
            new_cache_max_size = max(10, min(new_cache_max_size, 2000))
        except (ValueError, TypeError):
            new_cache_max_size = config.tts_cache_max_size_mb
        if new_cache_max_size != config.tts_cache_max_size_mb:
            changed_fields["tts_cache_max_size_mb"] = new_cache_max_size
            config.tts_cache_max_size_mb = new_cache_max_size

        try:
            new_cache_max_age = int(self._tts_cache_max_age_var.get())
            new_cache_max_age = max(0, min(new_cache_max_age, 365))
        except (ValueError, TypeError):
            new_cache_max_age = config.tts_cache_max_age_days
        if new_cache_max_age != config.tts_cache_max_age_days:
            changed_fields["tts_cache_max_age_days"] = new_cache_max_age
            config.tts_cache_max_age_days = new_cache_max_age

        try:
            new_cache_max_entries = int(self._tts_cache_max_entries_var.get())
            new_cache_max_entries = max(0, min(new_cache_max_entries, 5000))
        except (ValueError, TypeError):
            new_cache_max_entries = config.tts_cache_max_entries
        if new_cache_max_entries != config.tts_cache_max_entries:
            changed_fields["tts_cache_max_entries"] = new_cache_max_entries
            config.tts_cache_max_entries = new_cache_max_entries

        # TTS Export settings
        new_export_enabled = self._tts_export_enabled_var.get()
        if new_export_enabled != config.tts_export_enabled:
            changed_fields["tts_export_enabled"] = new_export_enabled
            config.tts_export_enabled = new_export_enabled

        new_export_path = self._tts_export_path_var.get().strip()
        if new_export_path != config.tts_export_path:
            changed_fields["tts_export_path"] = new_export_path
            config.tts_export_path = new_export_path

        # Audio cues
        new_audio_cues = self._audio_cues_var.get()
        if new_audio_cues != config.audio_cues_enabled:
            changed_fields["audio_cues_enabled"] = new_audio_cues
            config.audio_cues_enabled = new_audio_cues

        # v0.9: Paste confirmation/delay
        new_paste_confirm = self._paste_confirm_var.get()
        if new_paste_confirm != config.paste_require_confirmation:
            changed_fields["paste_require_confirmation"] = new_paste_confirm
            config.paste_require_confirmation = new_paste_confirm

        try:
            new_paste_delay = float(self._paste_delay_var.get())
            new_paste_delay = max(0.0, min(new_paste_delay, 36000.0))
        except (ValueError, TypeError):
            new_paste_delay = config.paste_delay_seconds
        if new_paste_delay != config.paste_delay_seconds:
            changed_fields["paste_delay_seconds"] = new_paste_delay
            config.paste_delay_seconds = new_paste_delay

        try:
            new_paste_timeout = float(self._paste_timeout_var.get())
            new_paste_timeout = max(5.0, min(new_paste_timeout, 120.0))
        except (ValueError, TypeError):
            new_paste_timeout = config.paste_confirmation_timeout
        if new_paste_timeout != config.paste_confirmation_timeout:
            changed_fields["paste_confirmation_timeout"] = new_paste_timeout
            config.paste_confirmation_timeout = new_paste_timeout

        new_paste_auto_enter = self._paste_auto_enter_var.get()
        if new_paste_auto_enter != config.paste_auto_enter:
            changed_fields["paste_auto_enter"] = new_paste_auto_enter
            config.paste_auto_enter = new_paste_auto_enter

        # v0.9: API
        new_api_enabled = self._api_enabled_var.get()
        if new_api_enabled != config.api_enabled:
            changed_fields["api_enabled"] = new_api_enabled
            config.api_enabled = new_api_enabled

        try:
            new_api_port = int(self._api_port_var.get())
            if not (1024 <= new_api_port <= 65535):
                new_api_port = config.api_port
        except (ValueError, TypeError):
            new_api_port = config.api_port
        if new_api_port != config.api_port:
            changed_fields["api_port"] = new_api_port
            config.api_port = new_api_port

        # v0.9: Hands-Free
        new_hf_enabled = self._handsfree_enabled_var.get()
        if new_hf_enabled != config.handsfree_enabled:
            changed_fields["handsfree_enabled"] = new_hf_enabled
            config.handsfree_enabled = new_hf_enabled

        new_wake_phrase = self._wake_phrase_var.get().strip()
        if not new_wake_phrase:
            from constants import DEFAULT_WAKE_PHRASE
            new_wake_phrase = DEFAULT_WAKE_PHRASE
        if new_wake_phrase != config.wake_phrase:
            changed_fields["wake_phrase"] = new_wake_phrase
            config.wake_phrase = new_wake_phrase

        _match_save_map = {
            "contains (forgiving)": "contains",
            "startswith (strict)": "startswith",
            "fuzzy (token overlap)": "fuzzy",
        }
        new_match_mode = _match_save_map.get(self._match_mode_var.get(), "contains")
        if new_match_mode != config.wake_phrase_match_mode:
            changed_fields["wake_phrase_match_mode"] = new_match_mode
            config.wake_phrase_match_mode = new_match_mode

        _pipeline_save_map = {
            "Ask AI + TTS (ask_tts)": "ask_tts",
            "Transcribe + Paste (summary)": "summary",
            "Ask AI + Paste (prompt)": "prompt",
            "Claude Code (claude_code)": "claude_code",
        }
        new_pipeline = _pipeline_save_map.get(self._handsfree_pipeline_var.get(), "ask_tts")
        if new_pipeline != config.handsfree_pipeline:
            changed_fields["handsfree_pipeline"] = new_pipeline
            config.handsfree_pipeline = new_pipeline

        try:
            new_silence_timeout = float(self._silence_timeout_var.get())
            new_silence_timeout = max(1.0, min(new_silence_timeout, 10.0))
        except (ValueError, TypeError):
            new_silence_timeout = config.silence_timeout_seconds
        if new_silence_timeout != config.silence_timeout_seconds:
            changed_fields["silence_timeout_seconds"] = new_silence_timeout
            config.silence_timeout_seconds = new_silence_timeout

        try:
            new_hf_max = int(float(self._hf_max_recording_var.get()))
            new_hf_max = max(10, min(new_hf_max, 300))
        except (ValueError, TypeError):
            new_hf_max = config.handsfree_max_recording_seconds
        if new_hf_max != config.handsfree_max_recording_seconds:
            changed_fields["handsfree_max_recording_seconds"] = new_hf_max
            config.handsfree_max_recording_seconds = new_hf_max

        try:
            new_cooldown = float(self._hf_cooldown_var.get())
            new_cooldown = max(1.0, min(new_cooldown, 10.0))
        except (ValueError, TypeError):
            new_cooldown = config.handsfree_cooldown_seconds
        if new_cooldown != config.handsfree_cooldown_seconds:
            changed_fields["handsfree_cooldown_seconds"] = new_cooldown
            config.handsfree_cooldown_seconds = new_cooldown

        # v1.2: Claude Code
        new_cc_enabled = self._claude_code_enabled_var.get()
        if new_cc_enabled != config.claude_code_enabled:
            changed_fields["claude_code_enabled"] = new_cc_enabled
            config.claude_code_enabled = new_cc_enabled

        new_cc_workdir = self._claude_code_workdir_var.get().strip()
        if new_cc_workdir != config.claude_code_working_dir:
            changed_fields["claude_code_working_dir"] = new_cc_workdir
            config.claude_code_working_dir = new_cc_workdir

        new_cc_prompt = self._claude_code_prompt_text.get("1.0", self._tk.END).strip()
        if new_cc_prompt != config.claude_code_system_prompt:
            changed_fields["claude_code_system_prompt"] = new_cc_prompt
            config.claude_code_system_prompt = new_cc_prompt

        _mode_save_map = {"Paste": "paste", "Speak": "speak", "Both": "both"}
        new_cc_mode = _mode_save_map.get(self._claude_code_mode_var.get(), "speak")
        if new_cc_mode != config.claude_code_response_mode:
            changed_fields["claude_code_response_mode"] = new_cc_mode
            config.claude_code_response_mode = new_cc_mode

        try:
            new_cc_timeout = int(self._claude_code_timeout_var.get())
            new_cc_timeout = max(10, min(new_cc_timeout, 600))
        except (ValueError, TypeError):
            new_cc_timeout = config.claude_code_timeout
        if new_cc_timeout != config.claude_code_timeout:
            changed_fields["claude_code_timeout"] = new_cc_timeout
            config.claude_code_timeout = new_cc_timeout

        new_cc_skip_perms = self._claude_code_skip_perms_var.get()
        if new_cc_skip_perms != config.claude_code_skip_permissions:
            changed_fields["claude_code_skip_permissions"] = new_cc_skip_perms
            config.claude_code_skip_permissions = new_cc_skip_perms

        new_cc_continue = self._claude_code_continue_var.get()
        if new_cc_continue != config.claude_code_continue_conversation:
            changed_fields["claude_code_continue_conversation"] = new_cc_continue
            config.claude_code_continue_conversation = new_cc_continue

        # Save non-secret fields to config.toml
        config.save_to_toml()

        # Notify the app about changes
        if changed_fields:
            logger.info(
                "Settings saved. Changed fields: %s",
                list(changed_fields.keys()),
            )
            try:
                self._on_save(changed_fields)
            except Exception:
                logger.exception("Error in on_save callback (settings still saved).")
        else:
            logger.info("Settings saved (no changes detected).")

        # Close dialog
        self._parent.quit()

    def _on_cancel_clicked(self) -> None:
        """Handle Cancel button click. Close without saving."""
        self._parent.quit()

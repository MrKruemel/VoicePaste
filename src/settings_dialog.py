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

    Must be called AFTER the window has been created and update_idletasks()
    has run (so that a valid HWND exists).

    Args:
        widget: A tkinter Tk or Toplevel instance.
    """
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
) -> bool:
    """Open the settings dialog on a dedicated tkinter thread.

    Args:
        config: Current application configuration.
        on_save: Callback invoked with dict of changed fields after save.

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
        self._dialog.minsize(540, 680)

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
        """Build all UI widgets."""
        tk = self._tk
        ttk = self._ttk
        dialog = self._dialog

        # Main frame with padding
        main_frame = ttk.Frame(dialog, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Error label at top (hidden by default)
        self._error_label = ttk.Label(
            main_frame, text="", foreground="#FF6B6B", wraplength=480
        )
        self._error_label.pack(fill=tk.X, pady=(0, 4))
        self._error_label.pack_forget()  # Hidden initially

        # === Section 1: Transcription ===
        transcription_frame = ttk.LabelFrame(
            main_frame, text="Transcription", padding=(10, 8)
        )
        transcription_frame.pack(fill=tk.X, pady=(0, 8))

        # v0.4: Backend selector row
        backend_row = ttk.Frame(transcription_frame)
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

        # --- Cloud sub-frame (shown when backend = cloud) ---
        self._cloud_frame = ttk.Frame(transcription_frame)

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
        self._local_frame = ttk.Frame(transcription_frame)

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
        # Don't pack yet — _update_model_status will show/hide it

        self._delete_btn = ttk.Button(
            status_row, text="Delete", width=8,
            command=self._on_delete_clicked,
        )
        # Don't pack yet — _update_model_status will show/hide it

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
            transcription_frame, text="", foreground="#FF6B6B", font=("", 8)
        )

        # === Section 2: Summarization ===
        summarization_frame = ttk.LabelFrame(
            main_frame, text="Summarization", padding=(10, 8)
        )
        summarization_frame.pack(fill=tk.X, pady=(0, 8))

        # Enable checkbox
        self._summarization_enabled_var = tk.BooleanVar()
        self._summarization_checkbox = ttk.Checkbutton(
            summarization_frame,
            text="Enable summarization",
            variable=self._summarization_enabled_var,
            command=self._on_summarization_toggled,
        )
        self._summarization_checkbox.pack(fill=tk.X, pady=(0, 6))

        # Provider row
        provider_row = ttk.Frame(summarization_frame)
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
        self._openrouter_key_frame = ttk.Frame(summarization_frame)

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
        model_row = ttk.Frame(summarization_frame)
        model_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(model_row, text="Model:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._model_var = tk.StringVar()
        self._model_entry = ttk.Entry(
            model_row, textvariable=self._model_var, width=40
        )
        self._model_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # Base URL row
        url_row = ttk.Frame(summarization_frame)
        url_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(url_row, text="Base URL:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._base_url_var = tk.StringVar()
        self._base_url_entry = ttk.Entry(
            url_row, textvariable=self._base_url_var, width=40
        )
        self._base_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        url_hint = ttk.Label(
            summarization_frame,
            text="Advanced. Change only if using a custom endpoint.",
            foreground="#999999",
            font=("", 8),
        )
        url_hint.pack(fill=tk.X, pady=(0, 6))

        # Custom Prompt section
        prompt_label_row = ttk.Frame(summarization_frame)
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
            summarization_frame,
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
            summarization_frame,
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

        # === Section 3: Text-to-Speech (v0.6) ===
        tts_frame = ttk.LabelFrame(
            main_frame, text="Text-to-Speech", padding=(10, 8)
        )
        tts_frame.pack(fill=tk.X, pady=(0, 8))

        # Enable checkbox
        self._tts_enabled_var = tk.BooleanVar()
        self._tts_checkbox = ttk.Checkbutton(
            tts_frame,
            text="Enable Text-to-Speech",
            variable=self._tts_enabled_var,
            command=self._on_tts_toggled,
        )
        self._tts_checkbox.pack(fill=tk.X, pady=(0, 6))

        # ElevenLabs API Key row
        tts_key_row = ttk.Frame(tts_frame)
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
        self._elevenlabs_key_actual = config.elevenlabs_api_key

        tts_key_hint = ttk.Label(
            tts_frame,
            text="Get a key at elevenlabs.io. Stored in Windows Credential Manager.",
            foreground="#999999",
            font=("", 8),
        )
        tts_key_hint.pack(fill=tk.X, pady=(0, 4))

        # Voice row
        tts_voice_row = ttk.Frame(tts_frame)
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
        tts_custom_voice_row = ttk.Frame(tts_frame)
        tts_custom_voice_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(tts_custom_voice_row, text="Voice ID:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._tts_voice_id_var = tk.StringVar()
        self._tts_voice_id_entry = ttk.Entry(
            tts_custom_voice_row, textvariable=self._tts_voice_id_var, width=30
        )
        self._tts_voice_id_entry.pack(side=tk.LEFT, padx=(4, 0))

        # Model row
        tts_model_row = ttk.Frame(tts_frame)
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

        # Store references for enable/disable
        self._tts_widgets = [
            self._elevenlabs_key_entry,
            self._elevenlabs_key_btn,
            self._tts_voice_combo,
            self._tts_voice_id_entry,
            self._tts_model_combo,
        ]

        # === Section 4: General ===
        general_frame = ttk.LabelFrame(
            main_frame, text="General", padding=(10, 8)
        )
        general_frame.pack(fill=tk.X, pady=(0, 8))

        # Hotkey display (read-only)
        hotkey_row = ttk.Frame(general_frame)
        hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(hotkey_row, text="Summarize:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._hotkey_label = ttk.Label(hotkey_row, text="", font=("", 9, "bold"))
        self._hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        prompt_hotkey_row = ttk.Frame(general_frame)
        prompt_hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(prompt_hotkey_row, text="Ask LLM:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._prompt_hotkey_label = ttk.Label(prompt_hotkey_row, text="", font=("", 9, "bold"))
        self._prompt_hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        # v0.6: TTS hotkeys display
        tts_hotkey_row = ttk.Frame(general_frame)
        tts_hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(tts_hotkey_row, text="Read TTS:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_hotkey_label = ttk.Label(tts_hotkey_row, text="", font=("", 9, "bold"))
        self._tts_hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        tts_ask_hotkey_row = ttk.Frame(general_frame)
        tts_ask_hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(tts_ask_hotkey_row, text="Ask+TTS:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._tts_ask_hotkey_label = ttk.Label(tts_ask_hotkey_row, text="", font=("", 9, "bold"))
        self._tts_ask_hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        hotkey_hint = ttk.Label(
            general_frame,
            text="Change in config.toml (requires restart)",
            foreground="#999999",
            font=("", 8),
        )
        hotkey_hint.pack(fill=tk.X, pady=(0, 4))

        # Audio cues checkbox
        self._audio_cues_var = tk.BooleanVar()
        ttk.Checkbutton(
            general_frame,
            text="Play audio cues",
            variable=self._audio_cues_var,
        ).pack(fill=tk.X)

        # === Button Bar ===
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(12, 0))

        ttk.Button(
            button_frame,
            text="Save",
            width=10,
            command=self._on_save_clicked,
        ).pack(side=tk.RIGHT)

        ttk.Button(
            button_frame,
            text="Cancel",
            width=10,
            command=self._on_cancel_clicked,
        ).pack(side=tk.RIGHT, padx=(0, 8))

    def _populate_from_config(self) -> None:
        """Fill widget values from current config and keyring."""
        config = self._config

        # v0.4: Backend selector
        if config.stt_backend == "local":
            self._backend_var.set("Local (faster-whisper, offline)")
        else:
            self._backend_var.set("Cloud (OpenAI Whisper API)")

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

        # v0.6: TTS fields
        self._tts_enabled_var.set(config.tts_enabled)

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

        # Enable/disable TTS widgets
        self._on_tts_toggled()

        # Hotkeys
        self._hotkey_label.config(text=config.hotkey)
        self._prompt_hotkey_label.config(text=config.prompt_hotkey)
        self._tts_hotkey_label.config(text=config.tts_hotkey)
        self._tts_ask_hotkey_label.config(text=config.tts_ask_hotkey)

        # Audio cues
        self._audio_cues_var.set(config.audio_cues_enabled)

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

    def _on_tts_toggled(self) -> None:
        """Enable/disable TTS widgets based on checkbox."""
        enabled = self._tts_enabled_var.get()
        for widget in self._tts_widgets:
            try:
                if enabled:
                    if widget == self._tts_voice_combo or widget == self._tts_model_combo:
                        widget.config(state="readonly")
                    elif widget == self._elevenlabs_key_entry:
                        if self._elevenlabs_key_editing:
                            widget.config(state="normal")
                        else:
                            widget.config(state="readonly")
                    else:
                        widget.config(state="normal")
                else:
                    widget.config(state="disabled")
            except Exception:
                pass

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

        # TTS validation (v0.6)
        if self._tts_enabled_var.get():
            if self._elevenlabs_key_editing:
                el_key = self._elevenlabs_key_var.get().strip()
                if not el_key:
                    self._elevenlabs_key_entry.focus_set()
                    return "ElevenLabs API key is required when TTS is enabled."
            elif not self._elevenlabs_key_actual:
                return "ElevenLabs API key is required for TTS. Click Edit to enter one."

            voice_id = self._tts_voice_id_var.get().strip()
            if not voice_id:
                self._tts_voice_id_entry.focus_set()
                return "Voice ID is required when TTS is enabled."

        return None

    def _on_save_clicked(self) -> None:
        """Handle Save button click."""
        # Hide any previous error
        self._error_label.pack_forget()

        # Validate
        error = self._validate()
        if error:
            self._error_label.config(text=error)
            self._error_label.pack(fill=self._tk.X, pady=(0, 4), before=self._error_label.master.winfo_children()[1])
            return

        changed_fields: dict[str, Any] = {}
        config = self._config

        # --- Collect new values ---

        # v0.4: Backend
        new_backend = "local" if "Local" in self._backend_var.get() else "cloud"
        if new_backend != config.stt_backend:
            changed_fields["stt_backend"] = new_backend
            config.stt_backend = new_backend

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

        # v0.6: TTS fields
        new_tts_enabled = self._tts_enabled_var.get()
        if new_tts_enabled != config.tts_enabled:
            changed_fields["tts_enabled"] = new_tts_enabled
            config.tts_enabled = new_tts_enabled

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

        # Voice ID
        new_voice_id = self._tts_voice_id_var.get().strip()
        if new_voice_id and new_voice_id != config.tts_voice_id:
            changed_fields["tts_voice_id"] = new_voice_id
            config.tts_voice_id = new_voice_id

        # Model ID
        new_model_display = self._tts_model_var.get()
        if "multilingual" in new_model_display:
            new_model_id = "eleven_multilingual_v2"
        else:
            new_model_id = "eleven_flash_v2_5"
        if new_model_id != config.tts_model_id:
            changed_fields["tts_model_id"] = new_model_id
            config.tts_model_id = new_model_id

        # Audio cues
        new_audio_cues = self._audio_cues_var.get()
        if new_audio_cues != config.audio_cues_enabled:
            changed_fields["audio_cues_enabled"] = new_audio_cues
            config.audio_cues_enabled = new_audio_cues

        # Save non-secret fields to config.toml
        config.save_to_toml()

        # Notify the app about changes
        if changed_fields:
            logger.info(
                "Settings saved. Changed fields: %s",
                list(changed_fields.keys()),
            )
            self._on_save(changed_fields)
        else:
            logger.info("Settings saved (no changes detected).")

        # Close dialog
        self._parent.quit()

    def _on_cancel_clicked(self) -> None:
        """Handle Cancel button click. Close without saving."""
        self._parent.quit()

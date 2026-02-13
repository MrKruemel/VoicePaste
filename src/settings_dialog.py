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
}


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

            dialog = SettingsDialog(root, config, on_save)

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

        # Create the dialog window
        self._dialog = tk.Toplevel(parent)
        self._dialog.title("Voice Paste - Settings")
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
            main_frame, text="", foreground="#CC0000", wraplength=480
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
            foreground="#666666",
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
            foreground="#666666",
            font=("", 8),
        )
        device_hint.pack(side=tk.LEFT, padx=(8, 0))

        # Model status row
        status_row = ttk.Frame(self._local_frame)
        status_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(status_row, text="Status:", width=10, anchor=tk.W).pack(side=tk.LEFT)

        self._model_status_label = ttk.Label(
            status_row, text="Checking...", foreground="#666666"
        )
        self._model_status_label.pack(side=tk.LEFT, padx=(4, 0))

        self._download_btn = ttk.Button(
            status_row, text="Download Model", width=16,
            command=self._on_download_clicked,
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
            self._progress_frame, text="Downloading...", foreground="#666666",
            font=("", 8),
        )
        self._progress_label.pack(side=tk.LEFT)

        # Local privacy note
        local_hint = ttk.Label(
            self._local_frame,
            text="Local mode: audio is never sent to any server. Requires faster-whisper.",
            foreground="#006600",
            font=("", 8),
        )
        local_hint.pack(fill=tk.X, pady=(2, 4))

        # Transcription error label
        self._transcription_error = ttk.Label(
            transcription_frame, text="", foreground="#CC0000", font=("", 8)
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
            values=["OpenAI", "OpenRouter"],
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
            foreground="#666666",
            font=("", 8),
        )
        or_hint.pack(fill=tk.X, pady=(0, 4))

        # OpenRouter error label
        self._openrouter_error = ttk.Label(
            self._openrouter_key_frame, text="", foreground="#CC0000", font=("", 8)
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
            foreground="#666666",
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
        )
        self._prompt_text.pack(fill=tk.X, pady=(0, 2))

        prompt_hint = ttk.Label(
            summarization_frame,
            text="Instructs the LLM how to clean up the transcription. Leave empty for default.",
            foreground="#666666",
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

        # === Section 3: General ===
        general_frame = ttk.LabelFrame(
            main_frame, text="General", padding=(10, 8)
        )
        general_frame.pack(fill=tk.X, pady=(0, 8))

        # Hotkey display (read-only)
        hotkey_row = ttk.Frame(general_frame)
        hotkey_row.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(hotkey_row, text="Hotkey:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self._hotkey_label = ttk.Label(hotkey_row, text="", font=("", 9, "bold"))
        self._hotkey_label.pack(side=tk.LEFT, padx=(4, 0))

        hotkey_hint = ttk.Label(
            general_frame,
            text="Change in config.toml (requires restart)",
            foreground="#666666",
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
        provider_display = "OpenAI" if config.summarization_provider == "openai" else "OpenRouter"
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

        # Hotkey
        self._hotkey_label.config(text=config.hotkey)

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
                    foreground="#006600",
                )
                self._download_btn.pack_forget()
                self._progress_frame.pack_forget()
            else:
                info = model_manager.get_model_info(model_key)
                size_mb = info.get("download_mb", "?")
                self._model_status_label.config(
                    text=f"Not downloaded (~{size_mb} MB).",
                    foreground="#CC6600",
                )
                self._download_btn.config(text="Download Model", state="normal")
                self._download_btn.pack(side=self._tk.LEFT, padx=(8, 0))
                self._progress_frame.pack_forget()
        except Exception as e:
            logger.debug("Could not check model status: %s", e)
            self._model_status_label.config(
                text="Could not check model status.",
                foreground="#CC0000",
            )
            self._download_btn.pack_forget()

    def _on_model_size_changed(self, event=None) -> None:
        """Handle model size dropdown change. Refresh download status."""
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

        # Update UI to show progress
        self._download_btn.config(text="Cancel", state="normal")
        self._model_status_label.config(
            text="Downloading...", foreground="#0066CC",
        )
        self._progress_frame.pack(
            fill=self._tk.X, pady=(0, 4),
            after=self._model_status_label.master,
        )
        self._progress_bar.config(value=0)
        self._progress_label.config(text=f"Connecting to Hugging Face...")

        # Disable model/device combos during download
        self._local_model_combo.config(state="disabled")
        self._local_device_combo.config(state="disabled")
        self._backend_combo.config(state="disabled")

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
            self._download_success = model_manager.download_model(
                model_key,
                on_progress=self._on_download_progress,
                cancel_event=self._download_cancel,
            )
            if not self._download_success and not self._download_cancel.is_set():
                self._download_error_msg = (
                    "Download failed. Check your internet connection "
                    "and try again."
                )
        except Exception as e:
            logger.error("Model download failed: %s", e)
            self._download_success = False
            self._download_error_msg = str(e)
        finally:
            self._download_done.set()

    def _poll_download(self) -> None:
        """Poll the download thread from the tkinter thread.

        Called via ``after()`` every 250ms. Updates the progress bar and
        label with real download progress, then checks if the download
        is complete.
        """
        if not self._download_done.is_set():
            # Update progress bar from shared counters
            total = self._download_total
            downloaded = self._download_bytes
            if total > 0:
                pct = min(100.0, (downloaded / total) * 100.0)
                self._progress_bar.config(value=pct)
                downloaded_mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                self._progress_label.config(
                    text=f"Downloading: {downloaded_mb:.1f} / {total_mb:.1f} MB "
                    f"({pct:.0f}%)"
                )
            # Still downloading -- schedule next poll
            self._dialog.after(250, self._poll_download)
            return

        # Download finished -- update UI
        self._progress_bar.config(value=0)
        self._progress_frame.pack_forget()

        # Re-enable combos
        self._local_model_combo.config(state="readonly")
        self._local_device_combo.config(state="readonly")
        self._backend_combo.config(state="readonly")

        if self._download_cancel.is_set():
            self._model_status_label.config(
                text="Download cancelled.", foreground="#CC6600",
            )
            self._download_btn.config(text="Download Model", state="normal")
        elif self._download_success:
            self._model_status_label.config(
                text="Model downloaded and ready.",
                foreground="#006600",
            )
            self._download_btn.pack_forget()
        else:
            # Show error detail if available
            error_hint = self._download_error_msg or "Check logs or try again."
            # Truncate for the label
            if len(error_hint) > 80:
                error_hint = error_hint[:77] + "..."
            self._model_status_label.config(
                text=f"Download failed. {error_hint}",
                foreground="#CC0000",
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
        provider_key = "openai" if provider == "OpenAI" else "openrouter"

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
                        widget.config(state="normal")
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
                        widget.config(state="disabled")
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
            # SEC-014: Enforce HTTPS only (REQ-S06)
            if not base_url.startswith("https://"):
                self._base_url_entry.focus_set()
                return "Base URL must start with https:// for security."

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
        new_provider = "openai" if provider_display == "OpenAI" else "openrouter"
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

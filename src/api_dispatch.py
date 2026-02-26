"""API command dispatch for VoicePaste.

Extracted from VoicePasteApp to reduce the god-object pattern.
Maps incoming API commands to application actions and returns
JSON-serializable response dicts.

v1.1: Extracted from main.py.
"""

import logging
import threading
from typing import Optional, Protocol, runtime_checkable

from constants import APP_VERSION, AppState, TTS_MAX_TEXT_LENGTH, TTS_MAX_TEXT_LENGTH_LOCAL

logger = logging.getLogger(__name__)


@runtime_checkable
class AppContext(Protocol):
    """Interface that the API dispatch layer needs from the main app.

    This protocol defines the minimal surface area required by the
    APIController, decoupling it from the full VoicePasteApp class.
    """

    @property
    def state(self) -> AppState: ...
    config: object  # AppConfig (avoid circular import)

    def _set_state(self, new_state: AppState) -> None: ...
    def _start_recording(self) -> None: ...
    def _stop_recording_and_process(self) -> None: ...
    def _on_cancel(self) -> None: ...
    def _run_tts_pipeline(self, text: str) -> None: ...
    def _run_tts_export_pipeline(self, text: str, filename_hint: str = "") -> None: ...
    def replay_tts_entry(self, entry_id: str) -> bool: ...


class APIController:
    """Handles API command dispatch, mapping actions to app operations.

    This class owns the routing logic previously in
    ``VoicePasteApp._api_dispatch``.  It receives a reference to the
    application context (``AppContext``) and delegates to it.

    Attributes:
        app: Reference to the application context.
    """

    def __init__(
        self,
        app: AppContext,
        tts_backend,
        audio_player,
        tts_cache,
        tts_exporter,
        paste_cancel_event: threading.Event,
    ) -> None:
        self._app = app
        self._tts = tts_backend
        self._audio_player = audio_player
        self._tts_cache = tts_cache
        self._tts_exporter = tts_exporter
        self._paste_cancel_event = paste_cancel_event

    # Allow main.py to update references after hot-reload
    def update_tts(self, tts_backend) -> None:
        """Update the TTS backend reference after settings change."""
        self._tts = tts_backend

    def update_cache(self, tts_cache) -> None:
        """Update the TTS cache reference after settings change."""
        self._tts_cache = tts_cache

    def update_exporter(self, tts_exporter) -> None:
        """Update the TTS exporter reference after settings change."""
        self._tts_exporter = tts_exporter

    def dispatch(self, command: dict) -> dict:
        """Handle an API command and return a JSON-serializable response.

        Args:
            command: Dict with "action" key and optional parameters.

        Returns:
            Response dict with "status" key.
        """
        action = command.get("action", "")

        if action == "status":
            return self._handle_status()

        if action == "tts":
            return self._handle_tts(command)

        if action == "stop_tts":
            return self._handle_stop_tts()

        if action == "record_start":
            return self._handle_record_start(command)

        if action == "record_stop":
            return self._handle_record_stop()

        if action == "cancel":
            return self._handle_cancel()

        if action == "tts_history_list":
            return self._handle_tts_history_list()

        if action == "tts_history_get":
            return self._handle_tts_history_get(command)

        if action == "tts_replay":
            return self._handle_tts_replay(command)

        if action == "tts_history_delete":
            return self._handle_tts_history_delete(command)

        if action == "tts_history_clear":
            return self._handle_tts_history_clear()

        if action == "tts_export_list":
            return self._handle_tts_export_list()

        if action == "tts_export":
            return self._handle_tts_export(command)

        return {
            "status": "error",
            "error_code": "INVALID_PARAMS",
            "message": f"Unknown action: {action}",
        }

    # -- Individual action handlers --

    def _handle_status(self) -> dict:
        return {
            "status": "ok",
            "data": {
                "state": self._app.state.value,
                "tts_enabled": self._app.config.tts_enabled,
                "api_version": "1",
                "app_version": APP_VERSION,
            },
        }

    def _handle_tts(self, command: dict) -> dict:
        text = command.get("text", "")
        if not text or not text.strip():
            return {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "text is required and must be non-empty",
            }
        text = text.strip()
        max_len = (TTS_MAX_TEXT_LENGTH_LOCAL
                   if self._app.config.tts_provider == "piper"
                   else TTS_MAX_TEXT_LENGTH)
        if len(text) > max_len:
            return {
                "status": "error",
                "error_code": "TEXT_TOO_LONG",
                "message": f"Text exceeds {max_len} character limit",
            }
        if not self._tts:
            return {
                "status": "error",
                "error_code": "TTS_NOT_CONFIGURED",
                "message": "TTS is not enabled or configured",
            }
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        # Fire-and-forget: start TTS in worker thread
        self._app._set_state(AppState.PROCESSING)
        thread = threading.Thread(
            target=self._app._run_tts_pipeline,
            args=(text,),
            daemon=True,
            name="api-tts-worker",
        )
        thread.start()
        return {"status": "ok"}

    def _handle_stop_tts(self) -> dict:
        if self._app.state == AppState.SPEAKING:
            self._audio_player.stop()
        return {"status": "ok"}

    def _handle_record_start(self, command: dict) -> dict:
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        mode = command.get("mode", "summary")
        if mode not in ("summary", "prompt"):
            mode = "summary"
        self._app._active_mode = mode
        self._app._start_recording()
        return {"status": "ok"}

    def _handle_record_stop(self) -> dict:
        if self._app.state != AppState.RECORDING:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Not currently recording",
            }
        self._app._stop_recording_and_process()
        return {"status": "ok"}

    def _handle_cancel(self) -> dict:
        current = self._app.state
        if current == AppState.RECORDING:
            self._app._on_cancel()
        elif current == AppState.SPEAKING:
            self._audio_player.stop()
        elif current == AppState.AWAITING_PASTE:
            self._paste_cancel_event.set()
        return {"status": "ok"}

    def _handle_tts_history_list(self) -> dict:
        entries = self._tts_cache.list_entries(limit=50)
        stats = self._tts_cache.stats()
        return {
            "status": "ok",
            "data": {
                "entries": entries,
                "total_entries": stats["total_entries"],
                "total_size_mb": stats["total_size_mb"],
                "cache_enabled": stats["cache_enabled"],
            },
        }

    def _handle_tts_history_get(self, command: dict) -> dict:
        entry_id = command.get("id", "")
        entry = self._tts_cache.get_entry(entry_id)
        if entry is None:
            return {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": f"Cache entry '{entry_id}' not found",
            }
        return {"status": "ok", "data": entry}

    def _handle_tts_replay(self, command: dict) -> dict:
        entry_id = command.get("id", "")
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        success = self._app.replay_tts_entry(entry_id)
        if not success:
            return {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": f"Cache entry '{entry_id}' not found",
            }
        return {"status": "ok"}

    def _handle_tts_history_delete(self, command: dict) -> dict:
        entry_id = command.get("id", "")
        deleted = self._tts_cache.delete(entry_id)
        if not deleted:
            return {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": f"Cache entry '{entry_id}' not found",
            }
        return {"status": "ok", "deleted": True}

    def _handle_tts_history_clear(self) -> dict:
        count = self._tts_cache.clear()
        return {"status": "ok", "deleted_count": count}

    def _handle_tts_export_list(self) -> dict:
        exports = self._tts_exporter.list_exports()
        stats = self._tts_exporter.stats()
        return {
            "status": "ok",
            "data": {
                "exports": exports,
                "total_files": stats["total_files"],
                "total_size_mb": stats["total_size_mb"],
                "export_enabled": stats["enabled"],
                "export_dir": stats["export_dir"],
            },
        }

    def _handle_tts_export(self, command: dict) -> dict:
        text = command.get("text", "")
        if not text or not text.strip():
            return {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "text is required and must be non-empty",
            }
        text = text.strip()
        max_len = (TTS_MAX_TEXT_LENGTH_LOCAL
                   if self._app.config.tts_provider == "piper"
                   else TTS_MAX_TEXT_LENGTH)
        if len(text) > max_len:
            return {
                "status": "error",
                "error_code": "TEXT_TOO_LONG",
                "message": f"Text exceeds {max_len} character limit",
            }
        if not self._tts:
            return {
                "status": "error",
                "error_code": "TTS_NOT_CONFIGURED",
                "message": "TTS is not enabled or configured",
            }
        if not self._tts_exporter.enabled:
            return {
                "status": "error",
                "error_code": "EXPORT_DISABLED",
                "message": "TTS export is not enabled. Enable in Settings.",
            }
        if self._app.state != AppState.IDLE:
            return {
                "status": "busy",
                "state": self._app.state.value,
                "message": "Another operation is in progress",
            }
        # Synthesize + export in worker thread (fire-and-forget)
        self._app._set_state(AppState.PROCESSING)
        thread = threading.Thread(
            target=self._app._run_tts_export_pipeline,
            args=(text, command.get("filename_hint", "")),
            daemon=True,
            name="api-tts-export-worker",
        )
        thread.start()
        return {"status": "ok", "message": "Export started"}

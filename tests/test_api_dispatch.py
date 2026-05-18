"""Tests for the APIController dispatcher (api_dispatch.py).

Focus: the per-call ``voice`` override on POST /tts so PURR-style
callers can pick a Piper voice profile per request without touching
global settings.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api_dispatch import APIController
from constants import AppState


def _make_app(state=AppState.IDLE, provider="piper", local_voice="de_DE-thorsten-medium"):
    """Build a minimal AppContext stub for APIController tests."""
    config = SimpleNamespace(
        tts_provider=provider,
        tts_local_voice=local_voice,
        tts_enabled=True,
    )
    app = SimpleNamespace(
        state=state,
        config=config,
        _set_state=MagicMock(),
        _run_tts_pipeline=MagicMock(),
        _run_tts_export_pipeline=MagicMock(),
        _start_recording=MagicMock(),
        _stop_recording_and_process=MagicMock(),
        _on_cancel=MagicMock(),
        replay_tts_entry=MagicMock(return_value=True),
    )
    return app


def _make_controller(app=None):
    """Build an APIController wired to mocks for non-app collaborators."""
    if app is None:
        app = _make_app()
    return APIController(
        app=app,
        tts_backend=MagicMock(),       # truthy -> passes "TTS configured" guard
        audio_player=MagicMock(),
        tts_cache=MagicMock(),
        tts_exporter=MagicMock(),
        paste_cancel_event=threading.Event(),
    )


class TestTTSVoiceOverride:
    """Verify the new ``voice`` body parameter on the tts action."""

    def test_valid_voice_is_forwarded_to_pipeline(self):
        """A registered Piper voice name is forwarded as a kwarg."""
        app = _make_app()
        ctrl = _make_controller(app)
        result = ctrl.dispatch({
            "action": "tts",
            "text": "Hallo Welt",
            "voice": "de_DE-thorsten_emotional-medium",
        })
        assert result == {"status": "ok"}
        # Wait briefly for the worker thread to call _run_tts_pipeline.
        # The dispatcher fires-and-forgets; assert with a small retry loop.
        for _ in range(50):
            if app._run_tts_pipeline.called:
                break
            threading.Event().wait(0.01)
        app._run_tts_pipeline.assert_called_once()
        args, kwargs = app._run_tts_pipeline.call_args
        assert args == ("Hallo Welt",)
        assert kwargs == {"voice": "de_DE-thorsten_emotional-medium"}

    def test_missing_voice_uses_default(self):
        """Without the voice key, pipeline is called with voice=None."""
        app = _make_app()
        ctrl = _make_controller(app)
        result = ctrl.dispatch({"action": "tts", "text": "Hallo"})
        assert result == {"status": "ok"}
        for _ in range(50):
            if app._run_tts_pipeline.called:
                break
            threading.Event().wait(0.01)
        app._run_tts_pipeline.assert_called_once()
        args, kwargs = app._run_tts_pipeline.call_args
        assert args == ("Hallo",)
        assert kwargs == {"voice": None}

    def test_unknown_voice_returns_400(self):
        """An unrecognized voice name yields INVALID_PARAMS, no pipeline call."""
        app = _make_app()
        ctrl = _make_controller(app)
        result = ctrl.dispatch({
            "action": "tts",
            "text": "Hallo",
            "voice": "totally-not-a-real-voice",
        })
        assert result["status"] == "error"
        assert result["error_code"] == "INVALID_PARAMS"
        assert "totally-not-a-real-voice" in result["message"]
        app._run_tts_pipeline.assert_not_called()
        app._set_state.assert_not_called()

    def test_empty_voice_string_returns_400(self):
        """A blank voice string is rejected."""
        app = _make_app()
        ctrl = _make_controller(app)
        result = ctrl.dispatch({
            "action": "tts",
            "text": "Hallo",
            "voice": "   ",
        })
        assert result["status"] == "error"
        assert result["error_code"] == "INVALID_PARAMS"
        app._run_tts_pipeline.assert_not_called()

    def test_voice_with_non_piper_provider_returns_400(self):
        """Voice override is rejected if the provider is not Piper."""
        app = _make_app(provider="elevenlabs")
        ctrl = _make_controller(app)
        result = ctrl.dispatch({
            "action": "tts",
            "text": "Hallo",
            "voice": "de_DE-thorsten-medium",
        })
        assert result["status"] == "error"
        assert result["error_code"] == "INVALID_PARAMS"
        assert "Piper" in result["message"]
        app._run_tts_pipeline.assert_not_called()

    def test_null_voice_treated_as_missing(self):
        """voice: null is equivalent to omitting the key."""
        app = _make_app()
        ctrl = _make_controller(app)
        result = ctrl.dispatch({
            "action": "tts",
            "text": "Hallo",
            "voice": None,
        })
        assert result == {"status": "ok"}
        for _ in range(50):
            if app._run_tts_pipeline.called:
                break
            threading.Event().wait(0.01)
        args, kwargs = app._run_tts_pipeline.call_args
        assert kwargs == {"voice": None}

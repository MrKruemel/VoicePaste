"""Tests for the v1.4 binary API surface.

Covers:
* POST /stt -- raw audio body -> JSON transcript.
* POST /tts streaming mode (Accept: audio/* / ?stream=ogg / body stream:true)
  -- raw audio bytes response, no speaker playback.
* Regression: POST /tts WITHOUT the streaming hint stays JSON.

All collaborators (TTS backend, STT backend, orchestrator) are stubbed so
the tests stay fast and do not touch the model files.
"""

import io
import json
import threading
import wave
from http.server import HTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib import request as urlrequest
from urllib.error import HTTPError

import pytest

from api_dispatch import APIController, _looks_like_wav
from api_server import VoicePasteAPIServer, start_api_server, stop_api_server
from constants import AppState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(state=AppState.IDLE, provider="piper"):
    config = SimpleNamespace(
        tts_provider=provider,
        tts_local_voice="de_DE-thorsten-medium",
        tts_enabled=True,
        transcription_language="de",
    )
    return SimpleNamespace(
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


def _tiny_wav_bytes(duration_s: float = 0.2, sample_rate: int = 16000) -> bytes:
    """Build a valid silent 16-bit PCM WAV blob."""
    n_samples = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)
    return buf.getvalue()


def _ogg_like_bytes() -> bytes:
    """Tiny non-WAV blob that LOOKS ogg-ish.

    We never actually decode it -- the STT stub short-circuits before the
    PyAV decode would run. The handler only needs to see a non-WAV prefix
    so the WAV fast-path is skipped.
    """
    return b"OggS" + b"\x00" * 28 + b"PURRSTUB" * 16


def _make_controller(
    *,
    app=None,
    stt_backend=None,
    tts_orchestrator=None,
    tts_backend=None,
):
    if app is None:
        app = _make_app()
    return APIController(
        app=app,
        tts_backend=tts_backend if tts_backend is not None else MagicMock(),
        audio_player=MagicMock(),
        tts_cache=MagicMock(),
        tts_exporter=MagicMock(),
        paste_cancel_event=threading.Event(),
        stt_backend=stt_backend,
        tts_orchestrator=tts_orchestrator,
    )


# ---------------------------------------------------------------------------
# Unit tests against the APIController (no HTTP layer)
# ---------------------------------------------------------------------------

class TestDispatchSTT:
    """Direct unit tests for APIController.dispatch_stt."""

    def test_empty_body_returns_400(self):
        ctrl = _make_controller(stt_backend=MagicMock())
        status, payload = ctrl.dispatch_stt(b"", content_type="audio/ogg")
        assert status == 400
        assert payload["error_code"] == "INVALID_PARAMS"

    def test_no_backend_returns_503(self):
        ctrl = _make_controller(stt_backend=None)
        status, payload = ctrl.dispatch_stt(b"raw", content_type="audio/ogg")
        assert status == 503
        assert payload["error_code"] == "STT_NOT_CONFIGURED"

    def test_wav_input_routes_through_transcribe(self):
        stt = MagicMock()
        stt.transcribe.return_value = "katzenstuhl"
        stt.detected_language = "de"
        ctrl = _make_controller(stt_backend=stt)
        wav = _tiny_wav_bytes()
        status, payload = ctrl.dispatch_stt(
            wav, content_type="audio/wav", language="de",
        )
        assert status == 200
        assert payload == {
            "status": "ok",
            "transcript": "katzenstuhl",
            "language": "de",
        }
        stt.transcribe.assert_called_once()
        args, kwargs = stt.transcribe.call_args
        assert args[0] == wav
        assert kwargs.get("language") == "de"

    def test_non_wav_routes_to_local_stt_decoder(self, monkeypatch):
        """A non-WAV blob with LocalWhisperSTT goes through PyAV decoder."""
        # Build a fake LocalWhisperSTT class instance.
        from local_stt import LocalWhisperSTT

        # Avoid instantiating the real thing (it touches faster-whisper).
        fake_stt = LocalWhisperSTT.__new__(LocalWhisperSTT)
        fake_stt._model_loaded = True
        fake_stt._model = MagicMock()
        fake_stt._beam_size = 5
        fake_stt._vad_filter = False
        fake_stt._initial_prompt = ""
        # mimic the post-transcribe attribute setter.
        fake_stt.detected_language = None

        # Stub the model.transcribe -> (segments_iter, info_obj).
        seg = SimpleNamespace(text="ogg-transkript")
        info = SimpleNamespace(
            language="de", language_probability=0.99,
        )
        fake_stt._model.transcribe.return_value = (iter([seg]), info)

        # Inject a stub decode_audio that pretends PyAV decoded our blob.
        import numpy as np
        import faster_whisper.audio as fw_audio
        monkeypatch.setattr(
            fw_audio,
            "decode_audio",
            lambda buf, sampling_rate=16000: np.zeros(
                sampling_rate // 4, dtype=np.float32,
            ),
        )

        ctrl = _make_controller(stt_backend=fake_stt)
        status, payload = ctrl.dispatch_stt(
            _ogg_like_bytes(), content_type="audio/ogg", language="de",
        )
        assert status == 200
        assert payload["status"] == "ok"
        assert payload["transcript"] == "ogg-transkript"
        assert payload["language"] == "de"

    def test_busy_state_returns_409(self):
        stt = MagicMock()
        ctrl = _make_controller(
            app=_make_app(state=AppState.PROCESSING),
            stt_backend=stt,
        )
        status, payload = ctrl.dispatch_stt(_tiny_wav_bytes())
        assert status == 409
        assert payload["status"] == "busy"
        stt.transcribe.assert_not_called()


class TestDispatchTTSStream:
    """Direct unit tests for APIController.dispatch_tts_stream."""

    def test_empty_text_returns_400(self):
        orch = MagicMock()
        ctrl = _make_controller(tts_orchestrator=orch)
        audio, mime, status = ctrl.dispatch_tts_stream({"text": "   "})
        assert audio is None
        assert status["error_code"] == "INVALID_PARAMS"

    def test_no_orchestrator_returns_error(self):
        ctrl = _make_controller(tts_orchestrator=None)
        audio, mime, status = ctrl.dispatch_tts_stream({"text": "Hallo"})
        assert audio is None
        assert status["error_code"] == "TTS_NOT_CONFIGURED"

    def test_happy_path_returns_bytes(self):
        orch = MagicMock()
        orch.synthesize_to_bytes.return_value = (b"WAVDATA", "audio/wav")
        ctrl = _make_controller(tts_orchestrator=orch)
        audio, mime, status = ctrl.dispatch_tts_stream(
            {"text": "Hallo Tim"}
        )
        assert audio == b"WAVDATA"
        assert mime == "audio/wav"
        assert status == {"status": "ok"}
        orch.synthesize_to_bytes.assert_called_once_with(
            "Hallo Tim", voice=None,
        )

    def test_voice_override_is_forwarded(self):
        orch = MagicMock()
        orch.synthesize_to_bytes.return_value = (b"abc", "audio/wav")
        ctrl = _make_controller(tts_orchestrator=orch)
        ctrl.dispatch_tts_stream({
            "text": "x", "voice": "de_DE-thorsten_emotional-medium",
        })
        orch.synthesize_to_bytes.assert_called_once_with(
            "x", voice="de_DE-thorsten_emotional-medium",
        )

    def test_unknown_voice_returns_error(self):
        orch = MagicMock()
        ctrl = _make_controller(tts_orchestrator=orch)
        audio, mime, status = ctrl.dispatch_tts_stream({
            "text": "x", "voice": "katzen-quatsch-medium",
        })
        assert audio is None
        assert status["error_code"] == "INVALID_PARAMS"
        orch.synthesize_to_bytes.assert_not_called()


def test_looks_like_wav():
    assert _looks_like_wav(_tiny_wav_bytes()) is True
    assert _looks_like_wav(_ogg_like_bytes()) is False
    assert _looks_like_wav(b"") is False
    assert _looks_like_wav(b"RIFF") is False  # too short


# ---------------------------------------------------------------------------
# HTTP integration tests (real http.server, localhost)
# ---------------------------------------------------------------------------

@pytest.fixture
def http_server():
    """Spin up a VoicePasteAPIServer with stubbed controller for testing."""

    app = _make_app()

    orch = MagicMock()
    orch.synthesize_to_bytes.return_value = (b"STREAM-BYTES", "audio/wav")

    stt = MagicMock()
    stt.transcribe.return_value = "guten morgen"
    stt.detected_language = "de"

    ctrl = APIController(
        app=app,
        tts_backend=MagicMock(),
        audio_player=MagicMock(),
        tts_cache=MagicMock(),
        tts_exporter=MagicMock(),
        paste_cancel_event=threading.Event(),
        stt_backend=stt,
        tts_orchestrator=orch,
    )

    def _dispatch(cmd):
        return ctrl.dispatch(cmd)

    server, thread = start_api_server(
        port=0,  # let the kernel pick a port
        dispatch=_dispatch,
        controller=ctrl,
    )
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        yield base, ctrl, orch, stt
    finally:
        stop_api_server(server)


def _post(url, data=None, headers=None):
    headers = headers or {}
    req = urlrequest.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urlrequest.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


class TestHTTPStreamAndSTT:

    def test_tts_default_path_still_returns_json(self, http_server):
        base, ctrl, orch, _stt = http_server
        body = json.dumps({"text": "Hallo Welt"}).encode("utf-8")
        status, ctype, payload = _post(
            f"{base}/tts",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert "application/json" in ctype
        assert json.loads(payload) == {"status": "ok"}
        # Streaming path must NOT have been taken.
        orch.synthesize_to_bytes.assert_not_called()

    def test_tts_with_accept_audio_returns_bytes(self, http_server):
        base, ctrl, orch, _stt = http_server
        body = json.dumps({"text": "Stream me", "voice": "de_DE-thorsten-medium"})
        status, ctype, payload = _post(
            f"{base}/tts",
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/ogg",
            },
        )
        assert status == 200
        assert ctype.startswith("audio/")
        assert payload == b"STREAM-BYTES"
        orch.synthesize_to_bytes.assert_called_once()

    def test_tts_stream_via_body_field(self, http_server):
        base, ctrl, orch, _stt = http_server
        body = json.dumps({"text": "Hi", "stream": True}).encode("utf-8")
        status, ctype, payload = _post(
            f"{base}/tts",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert ctype.startswith("audio/")
        assert payload == b"STREAM-BYTES"

    def test_tts_stream_via_query_param(self, http_server):
        base, ctrl, orch, _stt = http_server
        body = json.dumps({"text": "Hi"}).encode("utf-8")
        status, ctype, payload = _post(
            f"{base}/tts?stream=ogg",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert ctype.startswith("audio/")
        assert payload == b"STREAM-BYTES"

    def test_stt_empty_body_returns_400(self, http_server):
        base, *_ = http_server
        status, ctype, payload = _post(
            f"{base}/stt",
            data=b"",
            headers={"Content-Type": "audio/ogg"},
        )
        assert status == 400
        body = json.loads(payload)
        assert body["error_code"] == "INVALID_PARAMS"

    def test_stt_wav_returns_transcript(self, http_server):
        base, ctrl, _orch, stt = http_server
        wav = _tiny_wav_bytes()
        status, ctype, payload = _post(
            f"{base}/stt",
            data=wav,
            headers={"Content-Type": "audio/wav"},
        )
        assert status == 200
        body = json.loads(payload)
        assert body["status"] == "ok"
        assert body["transcript"] == "guten morgen"
        assert body["language"] == "de"
        stt.transcribe.assert_called_once()

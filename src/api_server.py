"""Local HTTP API server for Voice Paste.

Provides a localhost-only REST API that allows external programs to
control Voice Paste (TTS, recording, status queries).

Uses http.server from the Python standard library (zero dependencies).

Endpoints:
    GET  /health         - Health check (always 200)
    GET  /status         - App state + info
    GET  /tts/exports    - List exported TTS audio files
    POST /tts            - Speak text via TTS
    POST /tts/export     - Synthesize text and save to export directory
    POST /stop           - Stop TTS playback
    POST /record/start   - Start recording
    POST /record/stop    - Stop recording, trigger pipeline
    POST /cancel         - Cancel current operation

Security:
    - Binds to 127.0.0.1 ONLY (hardcoded, not configurable).
    - CORS restricted to http://localhost origins.
    - Rate limited to 5 requests/second.
    - API disabled by default.

v0.9: Initial implementation.
"""

import json
import logging
import re
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Optional
from urllib.parse import urlparse, parse_qs

# Strict CORS origin pattern: only http://localhost or http://localhost:PORT
_ALLOWED_ORIGIN_RE = re.compile(r"^http://localhost(:\d+)?$")

# Valid cache entry ID: exactly 16 lowercase hex characters (SHA256[:16])
_VALID_ENTRY_ID_RE = re.compile(r"^[0-9a-f]{16}$")

logger = logging.getLogger(__name__)

# Rate limiting
RATE_LIMIT_PER_SECOND = 5
MAX_CONTENT_LENGTH = 65536  # 64 KB max JSON request body
# STT audio bodies are much larger -- allow up to 25 MB (matches OpenAI
# Whisper's upload limit). Telegram voice messages cap at ~1 minute @ 16 kbps,
# i.e. well under 200 KB, so 25 MB is comfortable headroom.
MAX_AUDIO_CONTENT_LENGTH = 25 * 1024 * 1024


class _RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, max_per_second: int = RATE_LIMIT_PER_SECOND) -> None:
        self._max = max_per_second
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            # Remove timestamps older than 1 second
            self._timestamps = [t for t in self._timestamps if now - t < 1.0]
            if len(self._timestamps) >= self._max:
                return False
            self._timestamps.append(now)
            return True


class VoicePasteAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Voice Paste API."""

    server: "VoicePasteAPIServer"

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP server logs to our logger instead of stderr."""
        logger.debug("HTTP: %s", format % args)

    def _send_json(self, status_code: int, data: dict) -> None:
        """Send a JSON response with CORS headers."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # CORS: only allow localhost origins
        origin = self.headers.get("Origin", "")
        if _ALLOWED_ORIGIN_RE.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Optional[dict]:
        """Read and parse JSON request body. Returns None on error."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_CONTENT_LENGTH:
            return None
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        """Handle GET requests."""
        # Rate limit
        if not self.server.rate_limiter.allow():
            self._send_json(429, {
                "status": "error",
                "error_code": "RATE_LIMITED",
                "message": "Too many requests",
            })
            return

        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/status":
            result = self.server.dispatch({"action": "status"})
            self._send_json(200, result)
        elif self.path == "/tts/history":
            result = self.server.dispatch({"action": "tts_history_list"})
            self._send_json(200, result)
        elif self.path.startswith("/tts/history/"):
            entry_id = self.path.split("/")[-1]
            if not _VALID_ENTRY_ID_RE.match(entry_id):
                self._send_json(400, {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": "Invalid entry ID format",
                })
                return
            result = self.server.dispatch({
                "action": "tts_history_get", "id": entry_id,
            })
            code = 200 if result.get("status") == "ok" else 404
            self._send_json(code, result)
        elif self.path == "/tts/exports":
            result = self.server.dispatch({"action": "tts_export_list"})
            self._send_json(200, result)
        else:
            self._send_json(404, {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": "Unknown endpoint",
            })

    def do_POST(self) -> None:
        """Handle POST requests."""
        # Rate limit
        if not self.server.rate_limiter.allow():
            self._send_json(429, {
                "status": "error",
                "error_code": "RATE_LIMITED",
                "message": "Too many requests",
            })
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # v1.4: Speech-to-Text (raw audio body in, JSON out).
        if path == "/stt":
            self._handle_stt_request(query)
            return

        # v1.4: TTS streaming mode (raw audio bytes out).
        # Triggered by Accept: audio/* OR ?stream=ogg OR body field stream:true.
        if path == "/tts" and self._is_tts_stream_request(query):
            self._handle_tts_stream_request(query)
            return

        # Parse body for the JSON-only routes
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "Invalid JSON body",
            })
            return

        if body is None:
            self._send_json(413, {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "Request body too large",
            })
            return

        # If stream was requested via body field, route to stream handler.
        if path == "/tts" and isinstance(body, dict) and body.get("stream"):
            self._handle_tts_stream_request(query, prefetched_body=body)
            return

        # Route to action
        route_map = {
            "/tts": "tts",
            "/tts/export": "tts_export",
            "/stop": "stop_tts",
            "/record/start": "record_start",
            "/record/stop": "record_stop",
            "/cancel": "cancel",
        }

        action = route_map.get(path)

        # v1.0: TTS cache replay route (POST /tts/replay/{id})
        if action is None and path.startswith("/tts/replay/"):
            entry_id = path.split("/")[-1]
            if not _VALID_ENTRY_ID_RE.match(entry_id):
                self._send_json(400, {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": "Invalid entry ID format",
                })
                return
            body["action"] = "tts_replay"
            body["id"] = entry_id
            action = "tts_replay"
        if action is None:
            self._send_json(404, {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": "Unknown endpoint",
            })
            return

        body["action"] = action
        logger.info("API request: %s %s", self.command, path)

        result = self.server.dispatch(body)

        # Determine HTTP status code from result
        status_code = 200
        result_status = result.get("status", "")
        if result_status == "busy":
            status_code = 409
        elif result_status == "error":
            error_code = result.get("error_code", "")
            status_code = {
                "INVALID_PARAMS": 400,
                "TEXT_TOO_LONG": 413,
                "TTS_NOT_CONFIGURED": 503,
                "EXPORT_DISABLED": 403,
                "RATE_LIMITED": 429,
            }.get(error_code, 500)

        self._send_json(status_code, result)

    # ------------------------------------------------------------------
    # v1.4: New binary endpoints
    # ------------------------------------------------------------------

    def _is_tts_stream_request(self, query: dict) -> bool:
        """Return True if the caller wants raw audio bytes instead of JSON.

        Triggers:
          * ``Accept: audio/*`` header (handles audio/ogg, audio/wav, ...).
          * ``?stream=ogg`` (or any non-empty value) query string.
          * Body field ``stream: true`` (checked separately after JSON parse).
        """
        accept = self.headers.get("Accept", "").lower()
        if accept.startswith("audio/") or "audio/*" in accept:
            return True
        if "stream" in query and query["stream"] and query["stream"][0]:
            return True
        return False

    def _send_binary(
        self, status_code: int, content_type: str, payload: bytes,
    ) -> None:
        """Send a raw binary response with CORS headers."""
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        origin = self.headers.get("Origin", "")
        if _ALLOWED_ORIGIN_RE.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _http_status_for(self, result: dict, fallback: int = 500) -> int:
        result_status = result.get("status", "")
        if result_status == "busy":
            return 409
        if result_status == "ok":
            return 200
        if result_status == "error":
            return {
                "INVALID_PARAMS": 400,
                "TEXT_TOO_LONG": 413,
                "TTS_NOT_CONFIGURED": 503,
                "STT_NOT_CONFIGURED": 503,
                "AUDIO_DECODE_FAILED": 400,
                "TTS_FAILED": 500,
                "STT_FAILED": 500,
                "EXPORT_DISABLED": 403,
                "RATE_LIMITED": 429,
            }.get(result.get("error_code", ""), fallback)
        return fallback

    def _handle_tts_stream_request(
        self, query: dict, prefetched_body: Optional[dict] = None,
    ) -> None:
        """POST /tts with Accept: audio/* -- return raw audio bytes."""
        if prefetched_body is not None:
            body = prefetched_body
        else:
            try:
                body = self._read_json_body()
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": "Invalid JSON body",
                })
                return
            if body is None:
                self._send_json(413, {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": "Request body too large",
                })
                return

        logger.info("API request: %s /tts (stream)", self.command)
        ctrl = self.server.controller
        if ctrl is None:
            self._send_json(503, {
                "status": "error",
                "error_code": "TTS_NOT_CONFIGURED",
                "message": "API controller not wired for streaming",
            })
            return

        audio_bytes, mime, status = ctrl.dispatch_tts_stream(body)
        if audio_bytes is None:
            self._send_json(self._http_status_for(status), status)
            return
        self._send_binary(200, mime or "application/octet-stream", audio_bytes)

    def _handle_stt_request(self, query: dict) -> None:
        """POST /stt -- raw audio body in, JSON transcript out."""
        ctrl = self.server.controller
        if ctrl is None:
            self._send_json(503, {
                "status": "error",
                "error_code": "STT_NOT_CONFIGURED",
                "message": "API controller not wired for STT",
            })
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._send_json(400, {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "Audio body is empty",
            })
            return
        if content_length > MAX_AUDIO_CONTENT_LENGTH:
            self._send_json(413, {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": (
                    f"Audio body too large "
                    f"({content_length} > {MAX_AUDIO_CONTENT_LENGTH} bytes)"
                ),
            })
            return

        audio_data = self.rfile.read(content_length)
        if not audio_data:
            self._send_json(400, {
                "status": "error",
                "error_code": "INVALID_PARAMS",
                "message": "Audio body is empty",
            })
            return

        content_type = self.headers.get("Content-Type", "")
        # language: query string first, fall back to default.
        language: Optional[str] = None
        if "language" in query and query["language"]:
            language = query["language"][0]

        # REQ-S11: never log audio data, only its length.
        logger.info(
            "API request: %s /stt (%d bytes, %s)",
            self.command, len(audio_data), content_type or "no-type",
        )

        status_code, payload = ctrl.dispatch_stt(
            audio_data, content_type=content_type, language=language,
        )
        self._send_json(status_code, payload)

    def do_DELETE(self) -> None:
        """Handle DELETE requests (v1.0: TTS cache)."""
        if not self.server.rate_limiter.allow():
            self._send_json(429, {
                "status": "error",
                "error_code": "RATE_LIMITED",
                "message": "Too many requests",
            })
            return

        if self.path == "/tts/history":
            result = self.server.dispatch({"action": "tts_history_clear"})
            self._send_json(200, result)
        elif self.path.startswith("/tts/history/"):
            entry_id = self.path.split("/")[-1]
            if not _VALID_ENTRY_ID_RE.match(entry_id):
                self._send_json(400, {
                    "status": "error",
                    "error_code": "INVALID_PARAMS",
                    "message": "Invalid entry ID format",
                })
                return
            result = self.server.dispatch({
                "action": "tts_history_delete", "id": entry_id,
            })
            code = 200 if result.get("status") == "ok" else 404
            self._send_json(code, result)
        else:
            self._send_json(404, {
                "status": "error",
                "error_code": "NOT_FOUND",
                "message": "Unknown endpoint",
            })

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        origin = self.headers.get("Origin", "")
        if _ALLOWED_ORIGIN_RE.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()


class VoicePasteAPIServer(HTTPServer):
    """Threaded HTTP server for the Voice Paste API.

    Each request is handled in a new daemon thread.
    Binds to 127.0.0.1 only (hardcoded for security).
    """

    def __init__(
        self,
        port: int,
        dispatch: Callable[[dict], dict],
        controller: Any = None,
    ) -> None:
        self.dispatch = dispatch
        # v1.4: Optional direct controller reference for the binary
        # endpoints (POST /stt, POST /tts stream). The legacy ``dispatch``
        # callback only returns JSON-serializable dicts, which cannot
        # express raw audio responses.
        self.controller = controller
        self.rate_limiter = _RateLimiter()
        super().__init__(("127.0.0.1", port), VoicePasteAPIHandler)
        logger.info("API server initialized on http://127.0.0.1:%d", port)

    def process_request(self, request, client_address):
        """Handle each request in a new daemon thread."""
        t = threading.Thread(
            target=self._process_request_thread,
            args=(request, client_address),
            daemon=True,
            name="api-handler",
        )
        t.start()

    def _process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def start_api_server(
    port: int,
    dispatch: Callable[[dict], dict],
    controller: Any = None,
) -> tuple[VoicePasteAPIServer, threading.Thread]:
    """Create and start the API server on a daemon thread.

    Args:
        port: TCP port to bind to (on 127.0.0.1).
        dispatch: Callback to handle API commands. Receives a dict
            with an "action" key and returns a dict response.
        controller: Optional APIController reference (for v1.4 binary
            endpoints /stt and /tts streaming).

    Returns:
        Tuple of (server, thread). Call server.shutdown() to stop.

    Raises:
        OSError: If the port is already in use.
    """
    server = VoicePasteAPIServer(port, dispatch, controller=controller)
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="api-server",
    )
    thread.start()
    logger.info("API server started on http://127.0.0.1:%d", port)
    return server, thread


def stop_api_server(server: VoicePasteAPIServer) -> None:
    """Stop the API server gracefully."""
    try:
        server.shutdown()
        server.server_close()
        logger.info("API server stopped.")
    except Exception:
        logger.debug("Error stopping API server.", exc_info=True)

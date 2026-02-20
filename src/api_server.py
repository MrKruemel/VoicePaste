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

# Strict CORS origin pattern: only http://localhost or http://localhost:PORT
_ALLOWED_ORIGIN_RE = re.compile(r"^http://localhost(:\d+)?$")

logger = logging.getLogger(__name__)

# Rate limiting
RATE_LIMIT_PER_SECOND = 5
MAX_CONTENT_LENGTH = 65536  # 64 KB max request body


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

        # Parse body
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

        # Route to action
        route_map = {
            "/tts": "tts",
            "/tts/export": "tts_export",
            "/stop": "stop_tts",
            "/record/start": "record_start",
            "/record/stop": "record_stop",
            "/cancel": "cancel",
        }

        action = route_map.get(self.path)

        # v1.0: TTS cache replay route (POST /tts/replay/{id})
        if action is None and self.path.startswith("/tts/replay/"):
            entry_id = self.path.split("/")[-1]
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
        logger.info("API request: %s %s", self.command, self.path)

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
    ) -> None:
        self.dispatch = dispatch
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
) -> tuple[VoicePasteAPIServer, threading.Thread]:
    """Create and start the API server on a daemon thread.

    Args:
        port: TCP port to bind to (on 127.0.0.1).
        dispatch: Callback to handle API commands. Receives a dict
            with an "action" key and returns a dict response.

    Returns:
        Tuple of (server, thread). Call server.shutdown() to stop.

    Raises:
        OSError: If the port is already in use.
    """
    server = VoicePasteAPIServer(port, dispatch)
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

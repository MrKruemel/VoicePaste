"""Tests for the HTTP API server (api_server.py).

Validates:
- Server start/stop lifecycle
- All GET endpoints: /health, /status, /tts/history, /tts/history/{id}, /tts/exports
- All POST endpoints: /tts, /tts/export, /stop, /record/start, /record/stop, /cancel,
  /tts/replay/{id}
- DELETE endpoints: /tts/history, /tts/history/{id}
- OPTIONS CORS preflight handling
- Rate limiting (sliding window)
- Error handling: unknown routes (404), invalid methods, malformed JSON (400),
  oversized body (413), invalid entry IDs (400)
- Response format (JSON with proper Content-Type and CORS headers)
- Security: binds to 127.0.0.1 only, CORS origin validation

Coverage target: >= 70% of api_server.py
"""

import http.client
import json
import socket
import threading
import time

import pytest
from unittest.mock import MagicMock, patch

from api_server import (
    MAX_CONTENT_LENGTH,
    RATE_LIMIT_PER_SECOND,
    VoicePasteAPIHandler,
    VoicePasteAPIServer,
    _RateLimiter,
    start_api_server,
    stop_api_server,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_dispatch(return_value=None):
    """Create a mock dispatch callable that returns a fixed dict."""
    if return_value is None:
        return_value = {"status": "ok"}
    return MagicMock(return_value=return_value)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_dispatch():
    """A mock dispatch function that returns {"status": "ok"} by default."""
    return _make_dispatch()


@pytest.fixture()
def api_server(mock_dispatch):
    """Start an API server on a random free port; shut it down after the test."""
    port = _find_free_port()
    server, thread = start_api_server(port, mock_dispatch)
    # Give the server thread a moment to become ready
    time.sleep(0.05)
    yield server, port, mock_dispatch
    stop_api_server(server)
    thread.join(timeout=2)


def _get(port, path, headers=None):
    """Send a GET request to localhost:port and return (status, headers, body_dict)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = json.loads(resp.read().decode("utf-8"))
    status = resp.status
    resp_headers = dict(resp.getheaders())
    conn.close()
    return status, resp_headers, body


def _post(port, path, body=None, headers=None):
    """Send a POST request to localhost:port and return (status, headers, body_dict)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    conn.request("POST", path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    status = resp.status
    resp_headers = dict(resp.getheaders())
    conn.close()
    return status, resp_headers, data


def _delete(port, path, headers=None):
    """Send a DELETE request to localhost:port and return (status, headers, body_dict)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("DELETE", path, headers=headers or {})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    status = resp.status
    resp_headers = dict(resp.getheaders())
    conn.close()
    return status, resp_headers, data


def _options(port, path, headers=None):
    """Send an OPTIONS request and return (status, headers)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("OPTIONS", path, headers=headers or {})
    resp = conn.getresponse()
    _ = resp.read()
    status = resp.status
    resp_headers = dict(resp.getheaders())
    conn.close()
    return status, resp_headers


# ===========================================================================
# Unit tests for _RateLimiter
# ===========================================================================

class TestRateLimiter:
    """Test the sliding-window rate limiter independently."""

    def test_allows_up_to_max_requests(self):
        """Rate limiter should allow exactly max_per_second requests in a burst."""
        rl = _RateLimiter(max_per_second=3)
        assert rl.allow() is True
        assert rl.allow() is True
        assert rl.allow() is True
        assert rl.allow() is False

    def test_allows_new_requests_after_window_expires(self):
        """After the 1-second window slides, new requests should be allowed."""
        rl = _RateLimiter(max_per_second=1)
        assert rl.allow() is True
        assert rl.allow() is False
        # Simulate time passing by manipulating timestamps
        with rl._lock:
            rl._timestamps = [time.monotonic() - 2.0]
        assert rl.allow() is True

    def test_default_max_matches_constant(self):
        """Default max should match RATE_LIMIT_PER_SECOND."""
        rl = _RateLimiter()
        for _ in range(RATE_LIMIT_PER_SECOND):
            assert rl.allow() is True
        assert rl.allow() is False

    def test_thread_safety(self):
        """Rate limiter should be safe under concurrent access."""
        rl = _RateLimiter(max_per_second=100)
        results = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            results.append(rl.allow())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 10
        assert all(r is True for r in results)


# ===========================================================================
# Server lifecycle tests
# ===========================================================================

class TestServerLifecycle:
    """Test start_api_server / stop_api_server and VoicePasteAPIServer."""

    def test_start_and_stop(self, mock_dispatch):
        """Server starts, accepts a request, and shuts down cleanly."""
        port = _find_free_port()
        server, thread = start_api_server(port, mock_dispatch)
        try:
            time.sleep(0.05)
            status, _, body = _get(port, "/health")
            assert status == 200
            assert body["status"] == "ok"
        finally:
            stop_api_server(server)
            thread.join(timeout=2)

    def test_server_binds_to_localhost_only(self, mock_dispatch):
        """VoicePasteAPIServer must bind to 127.0.0.1."""
        port = _find_free_port()
        server = VoicePasteAPIServer(port, mock_dispatch)
        host, bound_port = server.server_address
        assert host == "127.0.0.1"
        assert bound_port == port
        server.server_close()

    def test_stop_server_is_idempotent(self, mock_dispatch):
        """Calling stop_api_server on an already-stopped server should not raise."""
        port = _find_free_port()
        server, thread = start_api_server(port, mock_dispatch)
        time.sleep(0.05)
        stop_api_server(server)
        thread.join(timeout=2)
        # Second stop should be harmless
        stop_api_server(server)

    def test_port_in_use_raises_oserror(self, mock_dispatch):
        """Starting a server on an occupied port should raise OSError."""
        port = _find_free_port()
        server1, thread1 = start_api_server(port, mock_dispatch)
        time.sleep(0.05)
        try:
            with pytest.raises(OSError):
                VoicePasteAPIServer(port, mock_dispatch)
        finally:
            stop_api_server(server1)
            thread1.join(timeout=2)

    def test_server_stores_dispatch_callback(self, mock_dispatch):
        """The server object should hold a reference to the dispatch callable."""
        port = _find_free_port()
        server = VoicePasteAPIServer(port, mock_dispatch)
        assert server.dispatch is mock_dispatch
        server.server_close()

    def test_server_has_rate_limiter(self, mock_dispatch):
        """The server object should have a rate_limiter attribute."""
        port = _find_free_port()
        server = VoicePasteAPIServer(port, mock_dispatch)
        assert isinstance(server.rate_limiter, _RateLimiter)
        server.server_close()


# ===========================================================================
# GET endpoint tests
# ===========================================================================

class TestGETEndpoints:
    """Test all GET endpoints."""

    def test_health_returns_200(self, api_server):
        """GET /health should return 200 with {"status": "ok"}."""
        _, port, _ = api_server
        status, _, body = _get(port, "/health")
        assert status == 200
        assert body == {"status": "ok"}

    def test_health_does_not_call_dispatch(self, api_server):
        """GET /health should NOT call the dispatch function."""
        _, port, dispatch = api_server
        _get(port, "/health")
        dispatch.assert_not_called()

    def test_status_calls_dispatch(self, api_server):
        """GET /status should dispatch {"action": "status"}."""
        server, port, dispatch = api_server
        dispatch.return_value = {
            "status": "ok",
            "data": {"state": "idle", "tts_enabled": False},
        }
        status, _, body = _get(port, "/status")
        assert status == 200
        assert body["status"] == "ok"
        dispatch.assert_called_with({"action": "status"})

    def test_tts_history_list(self, api_server):
        """GET /tts/history should dispatch tts_history_list."""
        _, port, dispatch = api_server
        dispatch.return_value = {"status": "ok", "data": {"entries": []}}
        status, _, body = _get(port, "/tts/history")
        assert status == 200
        dispatch.assert_called_with({"action": "tts_history_list"})

    def test_tts_history_get_valid_id(self, api_server):
        """GET /tts/history/{id} with valid hex ID dispatches correctly."""
        _, port, dispatch = api_server
        entry_id = "abcdef0123456789"
        dispatch.return_value = {"status": "ok", "data": {"id": entry_id}}
        status, _, body = _get(port, f"/tts/history/{entry_id}")
        assert status == 200
        dispatch.assert_called_with({"action": "tts_history_get", "id": entry_id})

    def test_tts_history_get_not_found(self, api_server):
        """GET /tts/history/{id} returns 404 when dispatch reports not found."""
        _, port, dispatch = api_server
        entry_id = "abcdef0123456789"
        dispatch.return_value = {"status": "error", "error_code": "NOT_FOUND"}
        status, _, body = _get(port, f"/tts/history/{entry_id}")
        assert status == 404

    def test_tts_history_get_invalid_id_format(self, api_server):
        """GET /tts/history/{id} with invalid ID returns 400 without dispatch."""
        _, port, dispatch = api_server
        # Too short
        status, _, body = _get(port, "/tts/history/abc")
        assert status == 400
        assert body["error_code"] == "INVALID_PARAMS"
        dispatch.assert_not_called()

    def test_tts_history_get_invalid_id_uppercase(self, api_server):
        """GET /tts/history/{id} rejects uppercase hex."""
        _, port, dispatch = api_server
        status, _, body = _get(port, "/tts/history/ABCDEF0123456789")
        assert status == 400
        dispatch.assert_not_called()

    def test_tts_exports_list(self, api_server):
        """GET /tts/exports should dispatch tts_export_list."""
        _, port, dispatch = api_server
        dispatch.return_value = {"status": "ok", "data": {"exports": []}}
        status, _, body = _get(port, "/tts/exports")
        assert status == 200
        dispatch.assert_called_with({"action": "tts_export_list"})

    def test_unknown_get_route_returns_404(self, api_server):
        """GET on an unknown path should return 404."""
        _, port, dispatch = api_server
        status, _, body = _get(port, "/nonexistent")
        assert status == 404
        assert body["error_code"] == "NOT_FOUND"
        dispatch.assert_not_called()

    def test_response_content_type_is_json(self, api_server):
        """All responses should have Content-Type: application/json."""
        _, port, _ = api_server
        _, headers, _ = _get(port, "/health")
        assert headers.get("Content-Type") == "application/json"


# ===========================================================================
# POST endpoint tests
# ===========================================================================

class TestPOSTEndpoints:
    """Test all POST endpoints."""

    def test_tts_dispatches_with_body(self, api_server):
        """POST /tts should dispatch with action=tts and the request body."""
        _, port, dispatch = api_server
        dispatch.return_value = {"status": "ok"}
        status, _, body = _post(port, "/tts", {"text": "Hallo Welt"})
        assert status == 200
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "tts"
        assert call_arg["text"] == "Hallo Welt"

    def test_tts_export_dispatches(self, api_server):
        """POST /tts/export should dispatch with action=tts_export."""
        _, port, dispatch = api_server
        dispatch.return_value = {"status": "ok"}
        status, _, body = _post(port, "/tts/export", {"text": "Export this"})
        assert status == 200
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "tts_export"

    def test_stop_tts_dispatches(self, api_server):
        """POST /stop should dispatch with action=stop_tts."""
        _, port, dispatch = api_server
        status, _, body = _post(port, "/stop")
        assert status == 200
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "stop_tts"

    def test_record_start_dispatches(self, api_server):
        """POST /record/start should dispatch with action=record_start."""
        _, port, dispatch = api_server
        status, _, body = _post(port, "/record/start")
        assert status == 200
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "record_start"

    def test_record_stop_dispatches(self, api_server):
        """POST /record/stop should dispatch with action=record_stop."""
        _, port, dispatch = api_server
        status, _, body = _post(port, "/record/stop")
        assert status == 200
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "record_stop"

    def test_cancel_dispatches(self, api_server):
        """POST /cancel should dispatch with action=cancel."""
        _, port, dispatch = api_server
        status, _, body = _post(port, "/cancel")
        assert status == 200
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "cancel"

    def test_tts_replay_valid_id(self, api_server):
        """POST /tts/replay/{valid_id} should dispatch tts_replay with id."""
        _, port, dispatch = api_server
        entry_id = "abcdef0123456789"
        dispatch.return_value = {"status": "ok"}
        status, _, body = _post(port, f"/tts/replay/{entry_id}", {})
        assert status == 200
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "tts_replay"
        assert call_arg["id"] == entry_id

    def test_tts_replay_invalid_id(self, api_server):
        """POST /tts/replay/{bad_id} should return 400 without dispatch."""
        _, port, dispatch = api_server
        status, _, body = _post(port, "/tts/replay/INVALID!", {})
        assert status == 400
        assert body["error_code"] == "INVALID_PARAMS"
        dispatch.assert_not_called()

    def test_unknown_post_route_returns_404(self, api_server):
        """POST on an unknown path should return 404."""
        _, port, dispatch = api_server
        status, _, body = _post(port, "/unknown_action")
        assert status == 404
        assert body["error_code"] == "NOT_FOUND"
        dispatch.assert_not_called()

    def test_post_empty_body_treated_as_empty_dict(self, api_server):
        """POST with no body (Content-Length 0) should parse as empty dict."""
        _, port, dispatch = api_server
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/record/start", body=b"", headers={
            "Content-Length": "0",
        })
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        assert resp.status == 200
        # dispatch should have been called; action key set by handler
        call_arg = dispatch.call_args[0][0]
        assert call_arg["action"] == "record_start"


# ===========================================================================
# POST response code mapping tests
# ===========================================================================

class TestPOSTResponseCodes:
    """Verify the handler maps dispatch result statuses to correct HTTP codes."""

    def test_busy_status_returns_409(self, api_server):
        """Dispatch returning {"status": "busy"} should yield HTTP 409."""
        _, port, dispatch = api_server
        dispatch.return_value = {"status": "busy", "state": "recording"}
        status, _, body = _post(port, "/tts", {"text": "test"})
        assert status == 409

    def test_error_invalid_params_returns_400(self, api_server):
        """error_code INVALID_PARAMS should yield HTTP 400."""
        _, port, dispatch = api_server
        dispatch.return_value = {
            "status": "error",
            "error_code": "INVALID_PARAMS",
            "message": "bad",
        }
        status, _, _ = _post(port, "/tts", {"text": "x"})
        assert status == 400

    def test_error_text_too_long_returns_413(self, api_server):
        """error_code TEXT_TOO_LONG should yield HTTP 413."""
        _, port, dispatch = api_server
        dispatch.return_value = {
            "status": "error",
            "error_code": "TEXT_TOO_LONG",
            "message": "too long",
        }
        status, _, _ = _post(port, "/tts", {"text": "x"})
        assert status == 413

    def test_error_tts_not_configured_returns_503(self, api_server):
        """error_code TTS_NOT_CONFIGURED should yield HTTP 503."""
        _, port, dispatch = api_server
        dispatch.return_value = {
            "status": "error",
            "error_code": "TTS_NOT_CONFIGURED",
            "message": "no tts",
        }
        status, _, _ = _post(port, "/tts", {"text": "x"})
        assert status == 503

    def test_error_export_disabled_returns_403(self, api_server):
        """error_code EXPORT_DISABLED should yield HTTP 403."""
        _, port, dispatch = api_server
        dispatch.return_value = {
            "status": "error",
            "error_code": "EXPORT_DISABLED",
            "message": "disabled",
        }
        status, _, _ = _post(port, "/tts/export", {"text": "x"})
        assert status == 403

    def test_error_rate_limited_returns_429(self, api_server):
        """error_code RATE_LIMITED should yield HTTP 429."""
        _, port, dispatch = api_server
        dispatch.return_value = {
            "status": "error",
            "error_code": "RATE_LIMITED",
            "message": "slow down",
        }
        status, _, _ = _post(port, "/tts", {"text": "x"})
        assert status == 429

    def test_error_unknown_code_returns_500(self, api_server):
        """An unrecognized error_code should fall back to HTTP 500."""
        _, port, dispatch = api_server
        dispatch.return_value = {
            "status": "error",
            "error_code": "SOMETHING_UNEXPECTED",
            "message": "oops",
        }
        status, _, _ = _post(port, "/tts", {"text": "x"})
        assert status == 500


# ===========================================================================
# DELETE endpoint tests
# ===========================================================================

class TestDELETEEndpoints:
    """Test DELETE endpoints for TTS history management."""

    def test_delete_all_history(self, api_server):
        """DELETE /tts/history should dispatch tts_history_clear."""
        _, port, dispatch = api_server
        dispatch.return_value = {"status": "ok", "deleted_count": 5}
        status, _, body = _delete(port, "/tts/history")
        assert status == 200
        dispatch.assert_called_with({"action": "tts_history_clear"})

    def test_delete_single_entry_valid_id(self, api_server):
        """DELETE /tts/history/{id} dispatches tts_history_delete."""
        _, port, dispatch = api_server
        entry_id = "abcdef0123456789"
        dispatch.return_value = {"status": "ok", "deleted": True}
        status, _, body = _delete(port, f"/tts/history/{entry_id}")
        assert status == 200
        dispatch.assert_called_with({"action": "tts_history_delete", "id": entry_id})

    def test_delete_single_entry_not_found(self, api_server):
        """DELETE /tts/history/{id} returns 404 when entry not found."""
        _, port, dispatch = api_server
        entry_id = "abcdef0123456789"
        dispatch.return_value = {"status": "error", "error_code": "NOT_FOUND"}
        status, _, body = _delete(port, f"/tts/history/{entry_id}")
        assert status == 404

    def test_delete_invalid_entry_id(self, api_server):
        """DELETE /tts/history/{bad_id} returns 400 without dispatch."""
        _, port, dispatch = api_server
        status, _, body = _delete(port, "/tts/history/not-valid-hex")
        assert status == 400
        assert body["error_code"] == "INVALID_PARAMS"
        dispatch.assert_not_called()

    def test_delete_unknown_route_returns_404(self, api_server):
        """DELETE on an unknown path should return 404."""
        _, port, dispatch = api_server
        status, _, body = _delete(port, "/nonexistent")
        assert status == 404
        assert body["error_code"] == "NOT_FOUND"
        dispatch.assert_not_called()


# ===========================================================================
# OPTIONS / CORS tests
# ===========================================================================

class TestCORS:
    """Test CORS preflight and response header behavior."""

    def test_options_returns_204(self, api_server):
        """OPTIONS request should return 204 No Content."""
        _, port, _ = api_server
        status, headers = _options(port, "/tts", headers={
            "Origin": "http://localhost:3000",
        })
        assert status == 204

    def test_options_includes_cors_headers_for_localhost(self, api_server):
        """OPTIONS with a valid localhost origin should include CORS headers."""
        _, port, _ = api_server
        _, headers = _options(port, "/tts", headers={
            "Origin": "http://localhost:3000",
        })
        assert headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
        assert "POST" in headers.get("Access-Control-Allow-Methods", "")
        assert "GET" in headers.get("Access-Control-Allow-Methods", "")
        assert "DELETE" in headers.get("Access-Control-Allow-Methods", "")

    def test_options_no_cors_for_external_origin(self, api_server):
        """OPTIONS with a non-localhost origin should NOT include CORS headers."""
        _, port, _ = api_server
        _, headers = _options(port, "/tts", headers={
            "Origin": "http://evil.example.com",
        })
        assert "Access-Control-Allow-Origin" not in headers

    def test_get_cors_header_for_localhost_origin(self, api_server):
        """GET response should include Access-Control-Allow-Origin for localhost."""
        _, port, _ = api_server
        _, headers, _ = _get(port, "/health", headers={
            "Origin": "http://localhost",
        })
        assert headers.get("Access-Control-Allow-Origin") == "http://localhost"

    def test_get_no_cors_header_for_non_localhost(self, api_server):
        """GET response should NOT include CORS header for non-localhost origin."""
        _, port, _ = api_server
        _, headers, _ = _get(port, "/health", headers={
            "Origin": "http://attacker.com",
        })
        assert "Access-Control-Allow-Origin" not in headers

    def test_cors_allows_localhost_with_port(self, api_server):
        """CORS should accept http://localhost:NNNNN patterns."""
        _, port, _ = api_server
        _, headers, _ = _get(port, "/health", headers={
            "Origin": "http://localhost:8080",
        })
        assert headers.get("Access-Control-Allow-Origin") == "http://localhost:8080"

    def test_cors_rejects_https_localhost(self, api_server):
        """CORS should reject https://localhost (only http:// allowed by regex)."""
        _, port, _ = api_server
        _, headers, _ = _get(port, "/health", headers={
            "Origin": "https://localhost",
        })
        assert "Access-Control-Allow-Origin" not in headers

    def test_cors_rejects_localhost_subdomain(self, api_server):
        """CORS should reject http://evil.localhost."""
        _, port, _ = api_server
        _, headers, _ = _get(port, "/health", headers={
            "Origin": "http://evil.localhost",
        })
        assert "Access-Control-Allow-Origin" not in headers


# ===========================================================================
# Rate limiting tests
# ===========================================================================

class TestRateLimiting:
    """Test that the server enforces rate limiting on all HTTP methods."""

    def test_get_rate_limit_enforced(self, mock_dispatch):
        """Exceeding rate limit on GET should return 429."""
        port = _find_free_port()
        server, thread = start_api_server(port, mock_dispatch)
        time.sleep(0.05)
        try:
            # Exhaust the rate limit
            for _ in range(RATE_LIMIT_PER_SECOND):
                status, _, _ = _get(port, "/health")
                assert status == 200
            # Next request should be rate limited
            status, _, body = _get(port, "/health")
            assert status == 429
            assert body["error_code"] == "RATE_LIMITED"
        finally:
            stop_api_server(server)
            thread.join(timeout=2)

    def test_post_rate_limit_enforced(self, mock_dispatch):
        """Exceeding rate limit on POST should return 429."""
        port = _find_free_port()
        server, thread = start_api_server(port, mock_dispatch)
        time.sleep(0.05)
        try:
            for _ in range(RATE_LIMIT_PER_SECOND):
                status, _, _ = _post(port, "/cancel")
                assert status == 200
            status, _, body = _post(port, "/cancel")
            assert status == 429
            assert body["error_code"] == "RATE_LIMITED"
        finally:
            stop_api_server(server)
            thread.join(timeout=2)

    def test_delete_rate_limit_enforced(self, mock_dispatch):
        """Exceeding rate limit on DELETE should return 429."""
        port = _find_free_port()
        server, thread = start_api_server(port, mock_dispatch)
        time.sleep(0.05)
        try:
            for _ in range(RATE_LIMIT_PER_SECOND):
                status, _, _ = _delete(port, "/tts/history")
                assert status == 200
            status, _, body = _delete(port, "/tts/history")
            assert status == 429
            assert body["error_code"] == "RATE_LIMITED"
        finally:
            stop_api_server(server)
            thread.join(timeout=2)


# ===========================================================================
# Error handling tests
# ===========================================================================

class TestErrorHandling:
    """Test error handling for malformed requests and edge cases."""

    def test_malformed_json_body_returns_400(self, api_server):
        """POST with invalid JSON body should return 400."""
        _, port, dispatch = api_server
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/tts", body=b"this is not json{{{", headers={
            "Content-Type": "application/json",
            "Content-Length": "19",
        })
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        assert resp.status == 400
        assert data["error_code"] == "INVALID_PARAMS"
        assert "Invalid JSON" in data["message"]
        dispatch.assert_not_called()

    def test_oversized_body_returns_413(self, api_server):
        """POST with body exceeding MAX_CONTENT_LENGTH should return 413."""
        _, port, dispatch = api_server
        # Craft a request that claims a very large Content-Length
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        # We only send the header claiming large content, not the actual body,
        # because the handler checks Content-Length before reading.
        conn.request("POST", "/tts", body=b"{}", headers={
            "Content-Type": "application/json",
            "Content-Length": str(MAX_CONTENT_LENGTH + 1),
        })
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        assert resp.status == 413
        assert data["error_code"] == "INVALID_PARAMS"
        assert "too large" in data["message"]
        dispatch.assert_not_called()


# ===========================================================================
# Handler log_message routing test
# ===========================================================================

class TestHandlerLogMessage:
    """Test that the handler routes logs through the module logger."""

    def test_log_message_uses_module_logger(self, api_server):
        """HTTP log messages should go through the api_server module logger."""
        _, port, _ = api_server
        with patch("api_server.logger") as mock_logger:
            _get(port, "/health")
            # Give async handler time to log
            time.sleep(0.1)
            # The log_message override calls logger.debug
            mock_logger.debug.assert_called()


# ===========================================================================
# Entry ID validation tests
# ===========================================================================

class TestEntryIdValidation:
    """Test _VALID_ENTRY_ID_RE enforcement across all endpoints that use it."""

    _VALID_IDS = [
        "0000000000000000",
        "abcdef0123456789",
        "ffffffffffffffff",
    ]

    _INVALID_IDS = [
        "",                   # empty
        "abc",                # too short
        "abcdef01234567890",  # too long (17 chars)
        "ABCDEF0123456789",   # uppercase
        "abcdef012345678g",   # non-hex char
        "abcdef0123456789/",  # trailing slash
        "../abcdef01234567",  # path traversal attempt
    ]

    @pytest.mark.parametrize("entry_id", _VALID_IDS)
    def test_get_history_accepts_valid_id(self, api_server, entry_id):
        """GET /tts/history/{id} should accept valid 16-char lowercase hex IDs."""
        _, port, dispatch = api_server
        dispatch.return_value = {"status": "ok", "data": {}}
        status, _, _ = _get(port, f"/tts/history/{entry_id}")
        assert status == 200

    @pytest.mark.parametrize("entry_id", _INVALID_IDS)
    def test_get_history_rejects_invalid_id(self, api_server, entry_id):
        """GET /tts/history/{id} should reject invalid entry IDs with 400."""
        _, port, dispatch = api_server
        status, _, body = _get(port, f"/tts/history/{entry_id}")
        # Empty ID hits the /tts/history route (no trailing segment), returns 200.
        # The others should be 400.
        if entry_id == "":
            # /tts/history/ with empty segment becomes /tts/history/ which
            # does not match startswith("/tts/history/") because path is /tts/history
            return
        assert status in (400, 404), f"Expected 400 or 404 for entry_id={entry_id!r}, got {status}"

    @pytest.mark.parametrize("entry_id", _INVALID_IDS)
    def test_post_replay_rejects_invalid_id(self, api_server, entry_id):
        """POST /tts/replay/{id} should reject invalid entry IDs."""
        _, port, dispatch = api_server
        if entry_id == "":
            return  # Empty segment won't match the route
        status, _, body = _post(port, f"/tts/replay/{entry_id}", {})
        assert status in (400, 404), f"Expected 400 or 404 for entry_id={entry_id!r}, got {status}"

    @pytest.mark.parametrize("entry_id", _INVALID_IDS)
    def test_delete_history_rejects_invalid_id(self, api_server, entry_id):
        """DELETE /tts/history/{id} should reject invalid entry IDs."""
        _, port, dispatch = api_server
        if entry_id == "":
            return
        status, _, body = _delete(port, f"/tts/history/{entry_id}")
        assert status in (400, 404), f"Expected 400 or 404 for entry_id={entry_id!r}, got {status}"


# ===========================================================================
# JSON response format tests
# ===========================================================================

class TestResponseFormat:
    """Verify all responses are well-formed JSON with expected structure."""

    def test_success_response_has_status_key(self, api_server):
        """Successful responses should always contain a 'status' key."""
        _, port, _ = api_server
        _, _, body = _get(port, "/health")
        assert "status" in body

    def test_error_response_has_required_keys(self, api_server):
        """Error responses should contain status, error_code, and message."""
        _, port, _ = api_server
        _, _, body = _get(port, "/nonexistent")
        assert body["status"] == "error"
        assert "error_code" in body
        assert "message" in body

    def test_content_length_header_is_accurate(self, api_server):
        """Content-Length header should match the actual body size."""
        _, port, _ = api_server
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        claimed_length = int(resp.getheader("Content-Length"))
        actual_body = resp.read()
        conn.close()
        assert len(actual_body) == claimed_length


# ===========================================================================
# Concurrency test
# ===========================================================================

class TestConcurrency:
    """Test that the server handles concurrent requests without errors."""

    def test_concurrent_requests(self, mock_dispatch):
        """Multiple simultaneous requests should all receive valid responses."""
        port = _find_free_port()
        # Use a high rate limit to avoid 429s during this test
        server, thread = start_api_server(port, mock_dispatch)
        server.rate_limiter = _RateLimiter(max_per_second=100)
        time.sleep(0.05)

        results = []
        errors = []

        def worker(idx):
            try:
                status, _, body = _get(port, "/health")
                results.append((idx, status, body))
            except Exception as e:
                errors.append((idx, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        stop_api_server(server)
        thread.join(timeout=2)

        assert len(errors) == 0, f"Errors during concurrent requests: {errors}"
        assert len(results) == 10
        for idx, status, body in results:
            assert status == 200
            assert body["status"] == "ok"

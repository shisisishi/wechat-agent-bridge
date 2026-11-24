"""Localhost-only token-authenticated HTTP IPC server for ShareTarget, SendTo, and CLI ingests."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


class LocalIPCServer:
    """Binds strictly to 127.0.0.1 and dispatches incoming ingest/focus events safely."""

    def __init__(
        self,
        *,
        port: int,
        token: str,
        on_ingest: Callable[[dict[str, Any]], dict[str, Any]],
        on_show: Callable[[], None],
    ) -> None:
        self.port = port
        self.token = token
        self.on_ingest = on_ingest
        self.on_show = on_show
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def _send_json(self, code: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _is_authorized(self) -> bool:
                return self.headers.get("X-Bridge-Token", "") == parent.token

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/api/health":
                    self._send_json(200, {"ok": True, "app": "WeChatAgentBridge", "pid": os.getpid()})
                else:
                    self._send_json(404, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._is_authorized():
                    self._send_json(403, {"ok": False, "error": "forbidden"})
                    return

                length = int(self.headers.get("Content-Length", "0") or 0)
                if length > 20 * 1024 * 1024:
                    self._send_json(413, {"ok": False, "error": "payload_too_large"})
                    return

                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    self._send_json(400, {"ok": False, "error": "invalid_json"})
                    return

                if self.path == "/api/show":
                    parent.on_show()
                    self._send_json(200, {"ok": True})
                    return

                if self.path == "/api/ingest":
                    try:
                        result = parent.on_ingest(data)
                        self._send_json(200, {"ok": True, **(result or {})})
                    except Exception as exc:
                        self._send_json(500, {"ok": False, "error": str(exc)})
                    return

                self._send_json(404, {"ok": False, "error": "not_found"})

        # Try configured port first, fall back to ephemeral port if occupied
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None


def send_ipc_request(port: int, token: str, endpoint: str, payload: dict[str, Any] | None = None, timeout: float = 3.0) -> tuple[bool, dict[str, Any]]:
    """Client helper to push an ingest or focus request to an already-running bridge instance (bypasses system proxy)."""
    url = f"http://127.0.0.1:{port}{endpoint}"
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Bridge-Token": token,
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(raw) if raw else {"ok": True}
    except Exception as exc:
        return False, {"ok": False, "error": str(exc)}

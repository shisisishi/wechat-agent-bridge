"""Isolated Mobile To-Do Sync Server (port 5201).

Security architecture:
- Completely isolated from WeChat chat databases (port 5200 never leaves 127.0.0.1).
- Reads/writes ONLY curated to-do items and sync logs in ~/.wechat-agent-bridge/inbox.db.
- Enforces secret access token authentication via HttpOnly cookie or Bearer header
  with timing-safe comparison and per-IP failed-attempt rate limiting.
"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import DATA_DIR, ensure_data_dirs
from .storage import InboxStore

MOBILE_SYNC_STATE_PATH = DATA_DIR / "mobile_sync.json"
MOBILE_UI_HTML_PATH = Path(__file__).resolve().parent / "mobile_ui.html"
COOKIE_NAME = "wcb_mobile_auth"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5201

APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#07c160"/>
      <stop offset="60%" stop-color="#10b981"/>
      <stop offset="100%" stop-color="#06b6d4"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="30" fill="url(#g)"/>
  <path d="M36 66 L56 86 L94 44" fill="none" stroke="#ffffff" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

MANIFEST_JSON = {
    "name": "微信实时待办清单",
    "short_name": "微信待办",
    "description": "每小时自动同步的微信待办清单（手机与电脑双向同步）",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#070b14",
    "theme_color": "#07c160",
    "icons": [
        {
            "src": "/icon.svg",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "any maskable",
        }
    ],
}


def load_or_create_mobile_state(
    state_path: Path = MOBILE_SYNC_STATE_PATH,
    *,
    rotate_token: bool = False,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    ensure_data_dirs()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    if rotate_token or not str(data.get("token") or "").strip():
        data["token"] = secrets.token_urlsafe(18)
        data["token_created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["port"] = int(port)
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def update_public_url(
    public_url: str,
    state_path: Path = MOBILE_SYNC_STATE_PATH,
) -> dict[str, Any]:
    data = load_or_create_mobile_state(state_path)
    data["public_url"] = public_url.rstrip("/")
    data["magic_url"] = f"{data['public_url']}/?k={data['token']}"
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


class _RateLimiter:
    """Simple per-IP sliding-window failed auth limiter."""

    def __init__(self, max_failures: int = 10, window_sec: float = 300.0):
        self.max_failures = max_failures
        self.window_sec = window_sec
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            arr = [t for t in self._hits.get(key, []) if now - t < self.window_sec]
            self._hits[key] = arr
            return len(arr) >= self.max_failures

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            arr = [t for t in self._hits.get(key, []) if now - t < self.window_sec]
            arr.append(now)
            self._hits[key] = arr

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def _tasks_payload(store: InboxStore) -> dict[str, Any]:
    tasks = [asdict(t) for t in store.list_tasks(include_done=True)]
    logs = [asdict(l) for l in store.list_sync_logs(limit=10)]
    todo_cnt = sum(1 for t in tasks if t["status"] == "todo")
    done_cnt = sum(1 for t in tasks if t["status"] == "done")
    high_cnt = sum(1 for t in tasks if t["status"] == "todo" and t["priority"] == "high")
    return {
        "ok": True,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tasks": tasks,
        "sync_logs": logs,
        "counts": {
            "todo": todo_cnt,
            "done": done_cnt,
            "high": high_cnt,
            "total": len(tasks),
        },
    }


def create_mobile_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str,
    store: InboxStore | None = None,
    ui_html_path: Path = MOBILE_UI_HTML_PATH,
) -> ThreadingHTTPServer:
    inbox_store = store or InboxStore()
    limiter = _RateLimiter()
    expected_token = token.strip()
    if not expected_token:
        raise ValueError("Mobile sync server requires a non-empty access token")

    class MobileTodoHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Suppress noisy access logs containing query strings
            return

        def _client_ip(self) -> str:
            forwarded = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
            return self.client_address[0] if self.client_address else "unknown"

        def _is_https(self) -> bool:
            proto = (self.headers.get("X-Forwarded-Proto") or "").lower()
            cf_visitor = (self.headers.get("CF-Visitor") or "").lower()
            return proto == "https" or '"https"' in cf_visitor

        def _valid_token(self, candidate: str) -> bool:
            if not candidate:
                return False
            return hmac.compare_digest(candidate.encode("utf-8"), expected_token.encode("utf-8"))

        def _extract_auth_token(self) -> str:
            auth_hdr = self.headers.get("Authorization") or ""
            if auth_hdr.startswith("Bearer "):
                return auth_hdr[7:].strip()
            cookie_hdr = self.headers.get("Cookie") or ""
            if cookie_hdr:
                jar = cookies.SimpleCookie()
                try:
                    jar.load(cookie_hdr)
                    if COOKIE_NAME in jar:
                        return jar[COOKIE_NAME].value.strip()
                except Exception:
                    pass
            return ""

        def _is_authenticated(self) -> bool:
            return self._valid_token(self._extract_auth_token())

        def _make_cookie_header(self, value: str, *, clear: bool = False) -> str:
            parts = [f"{COOKIE_NAME}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
            if clear:
                parts.append("Max-Age=0")
            else:
                parts.append("Max-Age=2592000")  # 30 days
            if self._is_https():
                parts.append("Secure")
            return "; ".join(parts)

        def _send_bytes(
            self,
            status: int,
            body: bytes,
            content_type: str,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(
            self,
            status: int,
            payload: dict[str, Any],
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status, raw, "application/json; charset=utf-8", extra_headers)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 65536:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}

        def _require_auth(self) -> bool:
            if self._is_authenticated():
                return True
            self._send_json(401, {"ok": False, "error": "Unauthorized"})
            return False

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/api/health":
                self._send_json(200, {"ok": True, "service": "mobile-todo-sync"})
                return

            if path == "/manifest.webmanifest":
                raw = json.dumps(MANIFEST_JSON, ensure_ascii=False).encode("utf-8")
                self._send_bytes(200, raw, "application/manifest+json; charset=utf-8")
                return

            if path == "/icon.svg":
                self._send_bytes(200, APP_ICON_SVG.encode("utf-8"), "image/svg+xml; charset=utf-8")
                return

            if path in ("/", "/index.html"):
                # Magic link login: /?k=<token> -> set HttpOnly cookie and redirect to clean /
                magic_k = (qs.get("k") or [""])[0].strip()
                if magic_k:
                    ip = self._client_ip()
                    if not limiter.is_blocked(ip) and self._valid_token(magic_k):
                        limiter.clear(ip)
                        self.send_response(302)
                        self.send_header("Location", "/")
                        self.send_header("Set-Cookie", self._make_cookie_header(expected_token))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Referrer-Policy", "no-referrer")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    limiter.record_failure(ip)

                html_text = ui_html_path.read_text(encoding="utf-8")
                authed_flag = "true" if self._is_authenticated() else "false"
                html_text = html_text.replace("__INITIAL_AUTH__", authed_flag)
                self._send_bytes(200, html_text.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/api/tasks":
                if not self._require_auth():
                    return
                self._send_json(200, _tasks_payload(inbox_store))
                return

            self._send_json(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/api/auth":
                ip = self._client_ip()
                if limiter.is_blocked(ip):
                    self._send_json(429, {"ok": False, "error": "尝试次数过多，请 5 分钟后再试"})
                    return
                body = self._read_json_body()
                candidate = str(body.get("token") or "").strip()
                if self._valid_token(candidate):
                    limiter.clear(ip)
                    self._send_json(
                        200,
                        {"ok": True},
                        extra_headers={"Set-Cookie": self._make_cookie_header(expected_token)},
                    )
                    return
                limiter.record_failure(ip)
                self._send_json(401, {"ok": False, "error": "访问口令不正确"})
                return

            if path == "/api/logout":
                self._send_json(
                    200,
                    {"ok": True},
                    extra_headers={"Set-Cookie": self._make_cookie_header("", clear=True)},
                )
                return

            if not self._require_auth():
                return

            if path == "/api/tasks":
                body = self._read_json_body()
                title = str(body.get("title") or "").strip()
                if not title:
                    self._send_json(400, {"ok": False, "error": "待办标题不能为空"})
                    return
                priority = str(body.get("priority") or "normal").strip().lower()
                if priority not in ("high", "normal", "low"):
                    priority = "normal"
                task, created = inbox_store.add_task(
                    title=title,
                    detail=str(body.get("detail") or "").strip(),
                    deadline=str(body.get("deadline") or "").strip(),
                    priority=priority,
                    source_title=str(body.get("source_title") or "手机端添加").strip(),
                    deduplicate=False,
                )
                self._send_json(200, {"ok": True, "created": created, "task": asdict(task)})
                return

            if path == "/api/tasks/clear-done":
                removed = inbox_store.clear_completed_tasks()
                self._send_json(200, {"ok": True, "removed": removed})
                return

            # /api/tasks/<id>/toggle
            parts = [p for p in path.split("/") if p]
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "toggle":
                try:
                    task_id = int(parts[2])
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "Invalid task id"})
                    return
                body = self._read_json_body()
                done = bool(body.get("done", True))
                inbox_store.toggle_task(task_id, done)
                self._send_json(200, {"ok": True, "id": task_id, "done": done})
                return

            self._send_json(404, {"ok": False, "error": "Not found"})

        def do_DELETE(self) -> None:
            if not self._require_auth():
                return
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "tasks":
                try:
                    task_id = int(parts[2])
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "Invalid task id"})
                    return
                inbox_store.delete_task(task_id)
                self._send_json(200, {"ok": True, "id": task_id})
                return
            self._send_json(404, {"ok": False, "error": "Not found"})

    return ThreadingHTTPServer((host, int(port)), MobileTodoHandler)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="mobile-todo-server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--rotate-token", action="store_true")
    parser.add_argument("--show-token", action="store_true")
    args = parser.parse_args(argv)

    state = load_or_create_mobile_state(rotate_token=args.rotate_token, port=args.port)
    token = str(state["token"])

    if args.show_token:
        print(json.dumps({"ok": True, "port": args.port, "token": token, "public_url": state.get("public_url", "")}, ensure_ascii=False))
        return 0

    server = create_mobile_server(host=args.host, port=args.port, token=token)
    print(f"Mobile To-Do Sync Server running at http://{args.host}:{args.port}", flush=True)
    print(f"Protected by secret token (saved in {MOBILE_SYNC_STATE_PATH})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

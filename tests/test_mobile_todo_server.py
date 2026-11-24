"""Tests for isolated mobile to-do sync server (auth lock, isolation, CRUD)."""

from __future__ import annotations

import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from bridge.mobile_todo_server import create_mobile_server, load_or_create_mobile_state
from bridge.storage import InboxStore


class MobileTodoServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp_dir = Path(self._tmp.name)
        self.db_path = tmp_dir / "inbox.db"
        self.store = InboxStore(db_path=self.db_path)
        self.token = "secret-mobile-token-12345"
        self.server = create_mobile_server(
            host="127.0.0.1",
            port=0,
            token=self.token,
            store=self.store,
        )
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

        self.no_proxy = urllib.request.ProxyHandler({})

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._tmp.cleanup()

    def _opener(self, jar: http.cookiejar.CookieJar | None = None):
        handlers = [self.no_proxy]
        if jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        return urllib.request.build_opener(*handlers)

    def test_health_public_and_tasks_require_auth(self) -> None:
        opener = self._opener()
        with opener.open(f"{self.base}/api/health", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(body["ok"])
            self.assertNotIn("tasks", body)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(f"{self.base}/api/tasks", timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        # Ensure WeChat chat routes are completely absent (air-gapped)
        for forbidden in ("/api/messages", "/api/sessions", "/api/contacts", "/api/stats"):
            with self.assertRaises(urllib.error.HTTPError) as ctx2:
                opener.open(f"{self.base}{forbidden}", timeout=5)
            self.assertEqual(ctx2.exception.code, 404)

    def test_magic_link_auth_and_full_crud_sync(self) -> None:
        jar = http.cookiejar.CookieJar()
        opener = self._opener(jar)

        # Open magic link -> sets HttpOnly cookie and redirects cleanly to /
        with opener.open(f"{self.base}/?k={self.token}", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.geturl(), f"{self.base}/")
            html = resp.read().decode("utf-8")
            self.assertIn("const INITIAL_AUTH = true;", html)

        # Create a task via mobile API
        req = urllib.request.Request(
            f"{self.base}/api/tasks",
            data=json.dumps({"title": "手机新增测试待办", "priority": "high", "deadline": "今晚"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=5) as resp:
            created = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(created["ok"])
            task_id = created["task"]["id"]

        # Verify shared SQLite InboxStore immediately sees it (PC sync)
        store_tasks = self.store.list_tasks(include_done=True)
        self.assertEqual(len(store_tasks), 1)
        self.assertEqual(store_tasks[0].title, "手机新增测试待办")

        # Toggle done via mobile API
        req_toggle = urllib.request.Request(
            f"{self.base}/api/tasks/{task_id}/toggle",
            data=json.dumps({"done": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req_toggle, timeout=5) as resp:
            self.assertTrue(json.loads(resp.read().decode("utf-8"))["ok"])

        with opener.open(f"{self.base}/api/tasks", timeout=5) as resp:
            listed = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(listed["counts"]["done"], 1)
            self.assertEqual(listed["counts"]["todo"], 0)

        # Clear completed tasks
        req_clear = urllib.request.Request(f"{self.base}/api/tasks/clear-done", data=b"{}", method="POST")
        with opener.open(req_clear, timeout=5) as resp:
            cleared = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(cleared["removed"], 1)

    def test_state_persistence_and_rotation(self) -> None:
        state_file = Path(self._tmp.name) / "mobile_sync.json"
        s1 = load_or_create_mobile_state(state_file)
        s2 = load_or_create_mobile_state(state_file)
        self.assertEqual(s1["token"], s2["token"])
        s3 = load_or_create_mobile_state(state_file, rotate_token=True)
        self.assertNotEqual(s1["token"], s3["token"])


if __name__ == "__main__":
    unittest.main()

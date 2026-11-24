"""Automated unit and integration tests for WeChat Agent Bridge."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.agents import create_adapter
from bridge.agents.detector import detect_local_targets, detect_wechat_environment
from bridge.config import BridgeConfig, TargetConfig
from bridge.ipc import LocalIPCServer, send_ipc_request
from bridge.parser import build_bundle, merge_bundles, parse_wechat_chat_text, strip_html
from bridge.storage import InboxStore
from bridge.sync_engine import extract_tasks_by_rules, run_progress_sync
from bridge.wechat_db_client import (
    WeChatCLIService,
    WeChatDBClient,
    _strip_wxid_suffix,
    detect_wechat_db_paths,
    get_mcp_clients_status,
    install_mcp_client,
    pull_incremental_wechat_bundles,
    uninstall_mcp_client,
)
from bridge.win_integration import parse_hotkey_string


class TestParserAndStorage(unittest.TestCase):
    def test_parse_timestamped_and_copy_formats(self) -> None:
        ts_chat = (
            "张三 2026-11-23 14:10:00\n"
            "明天下午三点开项目评审会，资料发我一份 https://example.com/spec\n\n"
            "李四 2026-11-23 14:12:30\n"
            "收到，我今晚把原型图整理好发群里。\n"
        )
        msgs = parse_wechat_chat_text(ts_chat)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].sender, "张三")
        self.assertEqual(msgs[0].timestamp, "2026-11-23 14:10:00")
        self.assertIn("https://example.com/spec", msgs[0].content)

        copy_chat = "王五:\n今天发现一个很有意思的商机\n\n赵六:\n展开说说？"
        msgs2 = parse_wechat_chat_text(copy_chat)
        self.assertEqual(len(msgs2), 2)
        self.assertEqual(msgs2[0].sender, "王五")
        self.assertEqual(msgs2[1].sender, "赵六")

    def test_strip_cf_html(self) -> None:
        cf_html = (
            "Version:0.9\r\n<!--StartFragment--><p>第一行<br/>第二行 &amp; 测试</p><!--EndFragment-->"
        )
        self.assertEqual(strip_html(cf_html), "第一行\n第二行 & 测试")

    def test_wechat_zip_ingest_and_batch_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundles_root = root / "bundles"
            zip1 = root / "产品群聊天记录_批1.zip"
            zip2 = root / "产品群聊天记录_批2.zip"

            with zipfile.ZipFile(zip1, "w") as zf:
                zf.writestr(
                    "聊天记录.txt",
                    (
                        "Alice 2026-11-23 10:00:00\n第一条消息\n\n"
                        "Bob 2026-11-23 10:01:00\n第二条消息 https://demo.local/a\n\n"
                        "Alice 2026-11-23 10:02:00\n重叠边界消息\n"
                    ).encode("utf-8"),
                )
                zf.writestr("images/diagram.png", b"\x89PNG\r\n\x1a\nMOCKPNG1")

            with zipfile.ZipFile(zip2, "w") as zf:
                zf.writestr(
                    "聊天记录.txt",
                    (
                        "Alice 2026-11-23 10:02:00\n重叠边界消息\n\n"
                        "Charlie 2026-11-23 10:05:00\n第四条新消息\n"
                    ).encode("utf-8"),
                )
                zf.writestr("images/diagram.png", b"\x89PNG\r\n\x1a\nMOCKPNG1")
                zf.writestr("docs/brief.pdf", b"%PDF-1.4 MOCK")

            b1 = build_bundle(bundles_root=bundles_root, source="windows_share_target", file_paths=[zip1])
            b2 = build_bundle(bundles_root=bundles_root, source="windows_share_target", file_paths=[zip2])
            self.assertEqual(len(b1.messages), 3)
            self.assertEqual(len(b1.attachments), 1)
            self.assertEqual(b1.attachments[0].kind, "image")
            self.assertEqual(len(b1.original_archives), 1)

            merged = merge_bundles(bundles_root, [b1, b2])
            # 3 + 2 - 1 overlapping message = 4 unique messages
            self.assertEqual(len(merged.messages), 4)
            # Duplicate PNG should be deduplicated, leaving 1 PNG + 1 PDF = 2 attachments
            self.assertEqual(len(merged.attachments), 2)
            self.assertEqual(merged.batch_count, 2)
            self.assertTrue(Path(merged.markdown_path).exists())

            # Test SQLite InboxStore
            store = InboxStore(db_path=root / "inbox.db")
            store.save_bundle(b1, queued_for_merge=True)
            store.save_bundle(b2, queued_for_merge=True)
            self.assertEqual(len(store.get_queued_bundles()), 2)
            found = store.list_bundles(search="Charlie")
            self.assertEqual(len(found), 1)
            did = store.record_delivery(
                bundle_id=merged.bundle_id,
                target_id="codex_cli",
                target_name="Codex CLI",
                template_name="提炼要点",
                status="running",
                mode="cli_exec",
            )
            store.update_delivery(did, status="success", summary="OK", output_text="Done")
            recs = store.list_deliveries(merged.bundle_id)
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0].status, "success")


class TestAdaptersAndIPC(unittest.TestCase):
    def test_obsidian_adapter_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / "shot.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\nIMGDATA")
            bundle = build_bundle(
                bundles_root=root / "bundles",
                source="clipboard",
                text="张三 2026-11-23 11:00:00\n沉淀一条知识卡片",
                file_paths=[img],
            )
            vault = root / "MyVault"
            vault.mkdir()
            target = TargetConfig(
                id="obs_test",
                name="Obsidian Test",
                kind="obsidian",
                mode="obsidian",
                vault_path=str(vault),
                subfolder="WeChatInbox",
                open_after_save=False,
            )
            res = create_adapter(target).deliver(bundle, "沉淀知识")
            self.assertTrue(res.ok, res.error)
            note = Path(res.output_path)
            self.assertTrue(note.exists())
            content = note.read_text(encoding="utf-8")
            self.assertIn("source: wechat", content)
            self.assertIn("![[WeChatInbox/assets/", content)

    def test_custom_cli_streaming_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = build_bundle(
                bundles_root=root / "bundles",
                source="manual",
                text="张三 2026-11-23 12:00:00\n测试 CLI 流式输出",
            )
            target = TargetConfig(
                id="py_echo",
                name="Python Echo Agent",
                kind="custom_cli",
                mode="cli_exec",
                command_template=f'"{sys.executable}" -c "import sys; data=sys.stdin.read(); print(\'ACK_LEN:\', len(data))"',
            )
            chunks: list[str] = []
            res = create_adapter(target, timeout_sec=15).deliver(
                bundle, "请汇总", on_chunk=lambda c: chunks.append(c)
            )
            self.assertTrue(res.ok, res.error)
            self.assertIn("ACK_LEN:", res.output_text)
            self.assertTrue(any("ACK_LEN:" in c for c in chunks))

    def test_webhook_openai_compatible_adapter(self) -> None:
        received: list[dict] = []

        class MockOpenAI(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                received.append(body)
                resp = json.dumps({"choices": [{"message": {"content": "## 提炼结果\n- 关键结论 A"}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        server = HTTPServer(("127.0.0.1", 0), MockOpenAI)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                bundle = build_bundle(
                    bundles_root=Path(tmp) / "bundles",
                    source="manual",
                    text="测试 Webhook 投递",
                )
                target = TargetConfig(
                    id="wh_test",
                    name="Mock OpenAI",
                    kind="webhook",
                    mode="webhook",
                    webhook_url=f"http://127.0.0.1:{port}/v1/chat/completions",
                    api_model="mock-model",
                )
                res = create_adapter(target, timeout_sec=10).deliver(bundle, "提炼要点")
                self.assertTrue(res.ok, res.error)
                self.assertIn("关键结论 A", res.output_text)
                self.assertEqual(received[0]["model"], "mock-model")
        finally:
            server.shutdown()
            server.server_close()

    def test_ipc_server_auth_and_ingest(self) -> None:
        events: list[dict] = []
        shown = []
        srv = LocalIPCServer(
            port=0,
            token="secret-token-123456",
            on_ingest=lambda p: (events.append(p) or {"received": True}),
            on_show=lambda: shown.append(True),
        )
        port = srv.start()
        try:
            ok_bad, _ = send_ipc_request(port, "wrong-token", "/api/ingest", {"text": "x"})
            self.assertFalse(ok_bad)

            ok_good, resp = send_ipc_request(port, "secret-token-123456", "/api/ingest", {"text": "hello"})
            self.assertTrue(ok_good)
            self.assertTrue(resp.get("received"))
            self.assertEqual(events[0]["text"], "hello")

            ok_show, _ = send_ipc_request(port, "secret-token-123456", "/api/show", {})
            self.assertTrue(ok_show)
            self.assertEqual(len(shown), 1)
        finally:
            srv.stop()

    def test_hotkey_parser_and_detection_and_msix_artifacts(self) -> None:
        self.assertIsNotNone(parse_hotkey_string("Ctrl+Alt+W"))
        self.assertIsNone(parse_hotkey_string("W"))

        w = detect_wechat_environment()
        self.assertIsInstance(w.installed, bool)
        targets = detect_local_targets()
        ids = {t.id for t in targets}
        for required in ("antigravity_gui", "codex_cli", "codex_gui", "claude_cli", "openclaw_cli", "obsidian_vault"):
            self.assertIn(required, ids)

    def test_hourly_sync_watch_folder_and_task_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            watch_dir = root / "watch"
            watch_dir.mkdir()
            (watch_dir / "项目同步.txt").write_text(
                "张三 2026-11-23 17:30:00\n"
                "1. 紧急：今晚 20:00 前需要修复登录超时报错\n"
                "2. 麻烦明天上午整理下客户访谈纪要\n",
                encoding="utf-8",
            )
            cfg = BridgeConfig(path=root / "config.json")
            cfg.data["sync_agent_id"] = "local_rules"
            cfg.data["wechat_db_enabled"] = False
            cfg.data["sync_watch_dirs"] = [str(watch_dir)]
            cfg.save()

            store = InboxStore(db_path=root / "inbox.db")
            res1 = run_progress_sync(
                config=cfg,
                store=store,
                trigger_type="hourly",
                bundles_root=root / "bundles",
                seen_path=root / "seen.json",
            )
            self.assertTrue(res1.ok)
            self.assertEqual(res1.new_bundles_ingested, 1)
            self.assertEqual(len(res1.new_tasks), 2)
            self.assertEqual(res1.new_tasks[0].priority, "high")

            # Running hourly sync again with no new files should be noop & zero duplicate tasks
            res2 = run_progress_sync(
                config=cfg,
                store=store,
                trigger_type="hourly",
                bundles_root=root / "bundles",
                seen_path=root / "seen.json",
            )
            self.assertEqual(res2.status, "noop")
            self.assertEqual(len(res2.new_tasks), 0)

            # Toggle first task done & verify persistence
            tasks = store.list_tasks()
            store.toggle_task(tasks[0].id, True)
            tasks_after = store.list_tasks()
            done_count = sum(1 for t in tasks_after if t.status == "done")
            self.assertEqual(done_count, 1)
            self.assertEqual(len(store.list_sync_logs()), 2)

class TestWeChatDirectExportStatsAndMCP(unittest.TestCase):
    def test_detect_paths_and_wxid_parsing(self) -> None:
        self.assertEqual(_strip_wxid_suffix("wxid_demo1234567890_52c6"), "wxid_demo1234567890")
        self.assertEqual(_strip_wxid_suffix("custom_alias"), "custom_alias")
        info = detect_wechat_db_paths()
        self.assertIsInstance(info.all_accounts, list)

    def test_direct_wechat_api_incremental_sync_and_stats_rendering(self) -> None:
        from urllib.parse import parse_qs, urlparse

        now_ts = int(time.time())
        state = {
            "refreshed": 0,
            "messages": [
                {
                    "seq": 101,
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now_ts - 120)),
                    "talker": "12345@chatroom",
                    "sender": "产品经理",
                    "isSelf": False,
                    "isChatRoom": True,
                    "type": 1,
                    "content": "紧急：今晚 21:00 前需要上线统计仪表板",
                },
                {
                    "seq": 102,
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now_ts - 60)),
                    "talker": "12345@chatroom",
                    "sender": "我",
                    "isSelf": True,
                    "isChatRoom": True,
                    "type": 1,
                    "content": "好的，明天上午麻烦同步一下测试报告",
                },
            ],
        }

        class MockWeChatCLI(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def _send_json(self, obj: object, code: int = 200) -> None:
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802
                if self.path.startswith("/api/refresh"):
                    state["refreshed"] += 1
                    self._send_json({"success": True})
                    return
                self._send_json({"error": "not found"}, 404)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                if parsed.path == "/api/health":
                    self._send_json({"status": "ok"})
                elif parsed.path == "/api/status":
                    self._send_json({"hasData": True, "dbCount": 5, "tables": ["message_0.db"]})
                elif parsed.path == "/api/sessions":
                    last_t = state["messages"][-1]["time"]
                    self._send_json([
                        {
                            "username": "12345@chatroom",
                            "nickname": "核心产品群",
                            "remark": "",
                            "alias": "",
                            "lastMessage": state["messages"][-1]["content"],
                            "lastTime": last_t,
                            "accountType": "normal",
                            "isHidden": False,
                        }
                    ])
                elif parsed.path == "/api/messages":
                    start_t = int(qs.get("start_time", ["0"])[0])
                    filtered = []
                    for m in state["messages"]:
                        ts = int(time.mktime(time.strptime(m["time"], "%Y-%m-%dT%H:%M:%S")))
                        if ts >= start_t:
                            filtered.append(m)
                    self._send_json(filtered)
                elif parsed.path == "/api/stats":
                    self._send_json({
                        "totalMessages": 2480,
                        "totalContacts": 120,
                        "totalSessions": 45,
                        "totalChatrooms": 12,
                        "typeDistribution": [{"type": 1, "label": "文本", "count": 2100}],
                        "topContacts": [{"username": "12345@chatroom", "nickname": "核心<产品>群", "count": 900}],
                        "hourlyActivity": [10] * 24,
                        "dailyActivity": [{"date": "2026-11-23", "count": 88}],
                        "dateRange": {"earliest": "2026-01-01T00:00:00", "latest": "2026-11-23T18:00:00"},
                    })
                elif parsed.path == "/api/chat-stats":
                    self._send_json({
                        "talker": "12345@chatroom",
                        "nickname": "核心产品群",
                        "isGroup": True,
                        "totalMessages": 900,
                        "myMessages": 300,
                        "theirMessages": 600,
                        "firstMessage": "2026-01-01T09:00:00",
                        "lastMessage": "2026-11-23T18:00:00",
                        "avgDaily": 12.5,
                        "totalDays": 72,
                        "topWords": [{"word": "统计", "count": 42}],
                        "topSenders": [{"sender": "wxid_a", "nickname": "产品经理", "count": 320}],
                        "replyStats": {"myAvgReplyMin": 3.5, "theirAvgReplyMin": 6.2},
                    })
                elif parsed.path == "/api/export":
                    txt = "[2026-11-23 18:00:00] 产品经理: 导出测试"
                    raw = txt.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                else:
                    self._send_json({"error": "not found"}, 404)

        server = HTTPServer(("127.0.0.1", 0), MockWeChatCLI)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            client = WeChatDBClient(base_url=f"http://127.0.0.1:{port}")
            self.assertTrue(client.is_alive())

            g_stats = client.get_global_stats()
            self.assertEqual(g_stats["totalMessages"], 2480)
            c_stats = client.get_chat_stats("12345@chatroom")
            self.assertEqual(c_stats["nickname"], "核心产品群")
            self.assertIn("导出测试", client.export_chat("12345@chatroom", fmt="txt"))

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = BridgeConfig(path=root / "config.json")
                cfg.data["sync_agent_id"] = "local_rules"
                cfg.data["wechat_db_enabled"] = True
                cfg.data["wechat_db_url"] = f"http://127.0.0.1:{port}"
                cfg.save()
                store = InboxStore(db_path=root / "inbox.db")
                wm_path = root / "wm.json"

                # 1st hourly sync: pulls 2 messages, creates 1 bundle, extracts 2 tasks
                r1 = run_progress_sync(
                    config=cfg,
                    store=store,
                    trigger_type="hourly",
                    bundles_root=root / "bundles",
                    seen_path=root / "seen.json",
                    watermarks_path=wm_path,
                    wechat_db_client=client,
                )
                self.assertTrue(r1.ok)
                self.assertEqual(r1.wechat_messages_pulled, 2)
                self.assertEqual(r1.new_bundles_ingested, 1)
                self.assertEqual(len(r1.new_tasks), 2)
                self.assertGreaterEqual(state["refreshed"], 1)

                # 2nd hourly sync without new messages: watermark skips already-seen seqs -> noop
                r2 = run_progress_sync(
                    config=cfg,
                    store=store,
                    trigger_type="hourly",
                    bundles_root=root / "bundles",
                    seen_path=root / "seen.json",
                    watermarks_path=wm_path,
                    wechat_db_client=client,
                )
                self.assertEqual(r2.wechat_messages_pulled, 0)
                self.assertEqual(r2.status, "noop")

                # 3rd hourly sync after 1 new message arrives -> pulls only seq 103 & adds 1 new task
                state["messages"].append(
                    {
                        "seq": 103,
                        "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(int(time.time()) + 10)),
                        "talker": "12345@chatroom",
                        "sender": "设计师",
                        "isSelf": False,
                        "isChatRoom": True,
                        "type": 1,
                        "content": "需要补充一张深色模式的数据趋势图",
                    }
                )
                r3 = run_progress_sync(
                    config=cfg,
                    store=store,
                    trigger_type="hourly",
                    bundles_root=root / "bundles",
                    seen_path=root / "seen.json",
                    watermarks_path=wm_path,
                    wechat_db_client=client,
                )
                self.assertEqual(r3.wechat_messages_pulled, 1)
                self.assertEqual(len(r3.new_tasks), 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_mcp_config_install_and_uninstall_all_clients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_p = root / ".claude.json"
            codex_p = root / "config.toml"
            agy_p = root / "mcp_config.json"

            claude_p.write_text(json.dumps({"existing": True, "mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
            codex_p.write_text('[desktop]\ntheme = "dark"\n\n[mcp_servers.existing]\ncommand = "foo"\n', encoding="utf-8")
            # Simulate Antigravity's 0-byte empty mcp_config.json!
            agy_p.write_text("", encoding="utf-8")

            overrides = {"claude": claude_p, "codex": codex_p, "antigravity": agy_p}
            st_before = get_mcp_clients_status(PROJECT_ROOT, path_overrides=overrides)
            self.assertFalse(any(v["installed"] for v in st_before.values()))

            for cid in ("claude", "codex", "antigravity"):
                ok, msg = install_mcp_client(cid, PROJECT_ROOT, path_overrides=overrides)
                self.assertTrue(ok, msg)

            st_after = get_mcp_clients_status(PROJECT_ROOT, path_overrides=overrides)
            self.assertTrue(all(v["installed"] for v in st_after.values()))

            # Verify existing keys & sections were preserved
            c_data = json.loads(claude_p.read_text(encoding="utf-8"))
            self.assertTrue(c_data["existing"])
            self.assertIn("other", c_data["mcpServers"])
            self.assertIn("wechat-cli", c_data["mcpServers"])

            toml_txt = codex_p.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.existing]", toml_txt)
            self.assertIn("[mcp_servers.wechat-cli]", toml_txt)
            self.assertIn("[mcp_servers.wechat-cli.env]", toml_txt)

            agy_data = json.loads(agy_p.read_text(encoding="utf-8"))
            self.assertIn("wechat-cli", agy_data["mcpServers"])

            # Verify clean uninstall preserves other servers
            for cid in ("claude", "codex", "antigravity"):
                ok, msg = uninstall_mcp_client(cid, path_overrides=overrides)
                self.assertTrue(ok, msg)
            st_removed = get_mcp_clients_status(PROJECT_ROOT, path_overrides=overrides)
            self.assertFalse(any(v["installed"] for v in st_removed.values()))
            self.assertIn("other", json.loads(claude_p.read_text(encoding="utf-8"))["mcpServers"])
            self.assertIn("[mcp_servers.existing]", codex_p.read_text(encoding="utf-8"))

    def test_real_bundled_wechat_cli_web_server_smoke(self) -> None:
        """Start the real compiled Node.js wechat-cli on an ephemeral localhost port (no DB decryption)."""
        svc = WeChatCLIService(PROJECT_ROOT, port=15299)
        if not svc.is_built():
            self.skipTest("third_party/wechat-cli/dist/index.js not built yet (run npm install && npm run build)")
        ok, msg = svc.start(wait_ready_sec=8.0)
        try:
            self.assertTrue(ok, msg)
            cli = svc.client()
            self.assertTrue(cli.is_alive())
            status = cli.get_status()
            self.assertIn("dataDir", status)
            self.assertIn("dbCount", status)
        finally:
            svc.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)

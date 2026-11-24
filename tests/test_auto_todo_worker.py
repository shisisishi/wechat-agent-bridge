"""Tests for offline auto-todo worker (pre-filter, dedup, JSON parsing, cycle commit)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.auto_todo_worker import (
    _extract_json_object,
    compact_conversation_for_llm,
    is_ad_or_ignored_conversation,
    is_fuzzy_duplicate,
    run_single_cycle,
)
from bridge.storage import InboxStore


class AutoTodoWorkerTests(unittest.TestCase):
    def test_prefilter_skips_ads_and_banter_but_keeps_real_notices(self) -> None:
        ad_conv = {
            "talker": "100000001@openim",
            "name": "示例饮品-促销福利群",
            "is_group": True,
            "messages": [{"t": "10:05", "sender": "客服", "text": "新品限时9折喝"}],
        }
        self.assertTrue(is_ad_or_ignored_conversation(ad_conv))
        keep, _ = compact_conversation_for_llm(ad_conv)
        self.assertFalse(keep)

        banter_conv = {
            "talker": "12345@chatroom",
            "name": "闲聊水群",
            "is_group": True,
            "messages": [
                {"t": "14:01", "sender": "A", "text": "哈哈哈哈"},
                {"t": "14:02", "sender": "B", "text": "[Facepalm]"},
                {"t": "14:03", "sender": "C", "text": "ok"},
            ],
        }
        keep_banter, _ = compact_conversation_for_llm(banter_conv)
        self.assertFalse(keep_banter)

        notice_conv = {
            "talker": "200000001@chatroom",
            "name": "产品研发通知群",
            "is_group": True,
            "messages": [
                {"t": "16:12", "sender": "负责人", "text": "@所有人 今晚架构评审在3号会议室开始，请提前5分钟签到。"}
            ],
        }
        keep_notice, digest = compact_conversation_for_llm(notice_conv)
        self.assertTrue(keep_notice)
        self.assertIn("3号会议室", digest)

    def test_prefilter_compresses_homework_essay_flood(self) -> None:
        hw_conv = {
            "talker": "200000002@chatroom",
            "name": "周报与文档提交群",
            "is_group": True,
            "messages": [
                {"t": "21:01", "sender": "成员A", "text": "作业：" + "很长的总结" * 60},
                {"t": "21:02", "sender": "成员B", "text": "作业: " + "另一篇总结" * 60},
                {"t": "21:03", "sender": "成员C", "text": "作业：" + "第三篇总结" * 60},
            ],
        }
        keep, digest = compact_conversation_for_llm(hw_conv)
        self.assertTrue(keep)
        self.assertIn("本人(我)在此批消息中是否已提交: 否", digest)
        self.assertLess(len(digest), 400)

    def test_fuzzy_dedup_and_json_parser(self) -> None:
        self.assertTrue(
            is_fuzzy_duplicate(
                "完成《信息安全合规》线上视频学习与章节测试",
                ["完成年度必修课《信息安全合规》视频学习与章节测试并备考测验"],
            )
        )
        self.assertFalse(
            is_fuzzy_duplicate(
                "明天上午10点提交实验报告",
                ["今晚去3号会议室参加架构评审"],
            )
        )
        parsed = _extract_json_object('```json\n{"tasks":[{"title":"测试待办","detail":"说明","deadline":"明天","priority":"high"}]}\n```')
        self.assertEqual(len(parsed["tasks"]), 1)
        self.assertEqual(parsed["tasks"][0]["title"], "测试待办")

    def test_run_single_cycle_commits_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            store = InboxStore(db_path=tmp_dir / "inbox.db")
            wm = tmp_dir / "wm.json"
            pending = tmp_dir / "pending.json"
            pending.write_text(json.dumps({"200000001@chatroom": 1000}), encoding="utf-8")

            fake_pull = {
                "ok": True,
                "sessions_checked": 10,
                "sessions_with_new": 2,
                "total_new_messages": 4,
                "existing_tasks": [],
                "new_conversations": [
                    {
                        "talker": "111@openim",
                        "name": "示例广告推广",
                        "is_group": True,
                        "messages": [{"t": "10:00", "sender": "客服", "text": "9折优惠券"}],
                    },
                    {
                        "talker": "200000001@chatroom",
                        "name": "产品研发通知群",
                        "is_group": True,
                        "messages": [{"t": "16:12", "sender": "负责人", "text": "@所有人 明晚7点提交评审报告"}],
                    },
                ],
            }

            calls: list[str] = []

            def fake_extractor(digest: str, existing: list[str]) -> list[dict]:
                calls.append(digest)
                return [
                    {"title": "明晚7点提交评审报告", "detail": "产品研发通知群通知", "deadline": "明晚19:00", "priority": "high"},
                    {"title": "明晚7点提交评审报告", "detail": "重复项应被去重", "deadline": "明晚19:00", "priority": "high"},
                ]

            cfg = {"backend": "ollama", "model": "qwen2.5vl:3b", "wechat_url": "http://127.0.0.1:5200"}
            with patch("bridge.auto_todo_worker._is_wechat_cli_fresh", return_value=(True, True)), patch(
                "bridge.auto_todo_worker.pull_new_messages_for_review", return_value=fake_pull, autospec=True
            ):
                res = run_single_cycle(
                    cfg,
                    store=store,
                    watermarks_path=wm,
                    pending_path=pending,
                    extractor_fn=fake_extractor,
                )

            self.assertTrue(res["ok"])
            self.assertEqual(len(calls), 1)  # ad conversation filtered out before LLM
            self.assertEqual(res["added_count"], 1)  # duplicate inside LLM output filtered out
            self.assertTrue(wm.is_file())  # pending watermark committed
            tasks = store.list_tasks(include_done=True)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].title, "明晚7点提交评审报告")


if __name__ == "__main__":
    unittest.main()

"""CLI & IPC helper for Agent-driven hourly WeChat -> To-Do synchronization and Web UI task CRUD.

All reads/writes go through InboxStore (~/.wechat-agent-bridge/inbox.db) so the desktop
FloatingTaskOverlay, the Web UI floating checklist on http://127.0.0.1:5200, and the
Antigravity hourly cron job always share the exact same live to-do list.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR, BridgeConfig, ensure_data_dirs
from .storage import InboxStore
from .wechat_db_client import (
    WeChatDBClient,
    _parse_iso_to_unix,
    session_display_name,
)

AGENT_WATERMARKS_PATH = DATA_DIR / "agent_todo_watermarks.json"
PENDING_WATERMARKS_PATH = DATA_DIR / "agent_todo_watermarks.pending.json"


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_msg_snippet(content: str, max_len: int = 240) -> str:
    txt = " ".join(str(content or "").split()).strip()
    if len(txt) > max_len:
        return txt[: max_len - 1] + "…"
    return txt


def pull_new_messages_for_review(
    *,
    base_url: str = "http://127.0.0.1:5200",
    initial_hours: int = 12,
    max_sessions: int = 20,
    max_msgs_per_session: int = 25,
    do_refresh: bool = True,
    watermarks_path: Path = AGENT_WATERMARKS_PATH,
    pending_path: Path = PENDING_WATERMARKS_PATH,
    store: InboxStore | None = None,
    config: BridgeConfig | None = None,
) -> dict[str, Any]:
    """Refresh local wechat-cli if requested and return compact incremental messages for AI review."""
    ensure_data_dirs()
    cfg = config or BridgeConfig()
    st = store or InboxStore()
    client = WeChatDBClient(base_url=base_url)

    if not client.is_alive(timeout=2.5):
        return {"ok": False, "error": f"wechat-cli 未在 {base_url} 运行"}

    refreshed = False
    if do_refresh:
        try:
            r = client.refresh(timeout=120.0)
            refreshed = bool(r.get("success", True))
        except Exception:
            refreshed = False

    sessions = client.list_sessions(limit=200)
    watch_set = {str(s).strip() for s in (cfg.data.get("wechat_db_watch_sessions") or []) if str(s).strip()}
    if watch_set:
        candidates = [s for s in sessions if str(s.get("username") or "") in watch_set]
    else:
        candidates = [
            s
            for s in sessions
            if str(s.get("accountType") or "normal") == "normal"
            and not bool(s.get("isHidden", False))
        ][: max(1, max_sessions)]

    watermarks = _load_json_dict(watermarks_path)
    next_wm = dict(watermarks)
    now_ts = int(time.time())
    default_since = now_ts - max(1, initial_hours) * 3600

    sessions_out: list[dict[str, Any]] = []
    total_new_msgs = 0

    for sess in candidates:
        talker = str(sess.get("username") or "").strip()
        if not talker:
            continue
        wm = watermarks.get(talker) if isinstance(watermarks.get(talker), dict) else {}
        last_ts = int(wm.get("last_ts") or 0)
        last_seq = int(wm.get("last_seq") or 0)

        sess_last_ts = _parse_iso_to_unix(str(sess.get("lastTime") or ""))
        if last_ts > 0 and sess_last_ts > 0 and sess_last_ts <= last_ts:
            continue
        if last_ts == 0 and sess_last_ts > 0 and sess_last_ts < default_since:
            continue

        since_ts = (last_ts + 1) if last_ts > 0 else default_since
        try:
            msgs = client.get_messages(talker, limit=120, reverse=False, start_time=since_ts)
        except Exception:
            continue

        fresh: list[dict[str, Any]] = []
        max_seq = last_seq
        max_ts = last_ts
        for m in msgs:
            seq = int(m.get("seq") or 0)
            m_ts = _parse_iso_to_unix(str(m.get("time") or ""))
            if last_seq > 0 and seq > 0 and seq <= last_seq:
                continue
            if seq > max_seq:
                max_seq = seq
            if m_ts > max_ts:
                max_ts = m_ts

            m_type = int(m.get("type") or 1)
            # Skip system notifications (10000), revokes (10002), pure stickers (47)
            if m_type in (10000, 10002, 47):
                continue
            snippet = _clean_msg_snippet(str(m.get("content") or ""))
            if not snippet or snippet in ("[表情]", "[图片]", "[视频]", "[动画表情]"):
                continue

            sender = "我" if m.get("isSelf") else str(m.get("sender") or session_display_name(sess))
            t_str = str(m.get("time") or "").replace("T", " ")[5:16]
            fresh.append({"t": t_str, "sender": sender[:24], "text": snippet})

        if max_ts > last_ts or max_seq > last_seq:
            next_wm[talker] = {"last_ts": max(max_ts, sess_last_ts), "last_seq": max_seq}

        if fresh:
            total_new_msgs += len(fresh)
            sessions_out.append(
                {
                    "talker": talker,
                    "name": session_display_name(sess),
                    "is_group": talker.endswith("@chatroom"),
                    "total_new": len(fresh),
                    "messages": fresh[-max_msgs_per_session:],
                }
            )

    _save_json(pending_path, next_wm)

    existing_tasks = [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "deadline": t.deadline,
            "source_title": t.source_title,
        }
        for t in st.list_tasks(include_done=True)[:80]
    ]

    return {
        "ok": True,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "refreshed": refreshed,
        "sessions_checked": len(candidates),
        "sessions_with_new": len(sessions_out),
        "total_new_messages": total_new_msgs,
        "existing_tasks": existing_tasks,
        "new_conversations": sessions_out,
    }


def commit_reviewed_todos(
    payload: dict[str, Any],
    *,
    trigger_type: str = "hourly",
    agent_name: str = "Antigravity 定时巡检",
    watermarks_path: Path = AGENT_WATERMARKS_PATH,
    pending_path: Path = PENDING_WATERMARKS_PATH,
    store: InboxStore | None = None,
) -> dict[str, Any]:
    """Persist AI-curated tasks into InboxStore, log the sync run, and advance watermarks."""
    ensure_data_dirs()
    st = store or InboxStore()

    raw_tasks = payload.get("new_tasks") or []
    added: list[dict[str, Any]] = []
    skipped_dups = 0

    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        prio = str(item.get("priority") or "normal").strip().lower()
        if prio not in ("high", "normal", "low"):
            prio = "normal"
        task, created = st.add_task(
            title=title[:80],
            detail=str(item.get("detail") or "").strip()[:300],
            owner=str(item.get("owner") or "").strip()[:40],
            deadline=str(item.get("deadline") or "").strip()[:40],
            priority=prio,
            source_bundle_id=str(item.get("source_bundle_id") or "").strip(),
            source_title=str(item.get("source_title") or "").strip()[:80],
            deduplicate=True,
        )
        if created:
            added.append(asdict(task))
        else:
            skipped_dups += 1

    sessions_scanned = int(payload.get("sessions_scanned") or 0)
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = (
            f"检查 {sessions_scanned} 个活跃会话，新增 {len(added)} 项待办。"
            if added
            else f"检查 {sessions_scanned} 个活跃会话，暂无需要新增的待办。"
        )
    status = "success" if added or sessions_scanned > 0 else "noop"
    log = st.record_sync_log(
        trigger_type=trigger_type,
        agent_name=agent_name,
        bundles_scanned=sessions_scanned,
        new_tasks_count=len(added),
        summary=summary,
        status=status,
    )

    if pending_path.is_file():
        pending = _load_json_dict(pending_path)
        if pending:
            _save_json(watermarks_path, pending)
        try:
            pending_path.unlink()
        except OSError:
            pass

    all_tasks = st.list_tasks(include_done=True)
    return {
        "ok": True,
        "added_count": len(added),
        "skipped_duplicates": skipped_dups,
        "added_tasks": added,
        "todo_total": sum(1 for t in all_tasks if t.status == "todo"),
        "done_total": sum(1 for t in all_tasks if t.status == "done"),
        "log": asdict(log),
    }


def list_tasks_payload(store: InboxStore | None = None) -> dict[str, Any]:
    st = store or InboxStore()
    tasks = [asdict(t) for t in st.list_tasks(include_done=True)]
    logs = [asdict(l) for l in st.list_sync_logs(limit=15)]
    todo_cnt = sum(1 for t in tasks if t["status"] == "todo")
    done_cnt = sum(1 for t in tasks if t["status"] == "done")
    mobile_sync: dict[str, str] = {}
    mobile_state_path = DATA_DIR / "mobile_sync.json"
    if mobile_state_path.is_file():
        ms = _load_json_dict(mobile_state_path)
        if ms.get("public_url") and ms.get("magic_url"):
            mobile_sync = {
                "public_url": str(ms["public_url"]),
                "magic_url": str(ms["magic_url"]),
            }
    return {
        "ok": True,
        "tasks": tasks,
        "sync_logs": logs,
        "counts": {"todo": todo_cnt, "done": done_cnt, "total": len(tasks)},
        "mobile_sync": mobile_sync,
    }


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdio on Windows so PowerShell pipes & Node.js callers always decode Chinese cleanly
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8-sig")

    parser = argparse.ArgumentParser(prog="hourly-todo-runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pull = sub.add_parser("pull")
    p_pull.add_argument("--url", default="http://127.0.0.1:5200")
    p_pull.add_argument("--initial-hours", type=int, default=12)
    p_pull.add_argument("--max-sessions", type=int, default=20)
    p_pull.add_argument("--no-refresh", action="store_true")

    p_commit = sub.add_parser("commit")
    p_commit.add_argument("--json", dest="json_str", default="")
    p_commit.add_argument("--file", dest="json_file", default="")
    p_commit.add_argument("--trigger", default="hourly")
    p_commit.add_argument("--agent", default="Antigravity 定时巡检")

    sub.add_parser("list-tasks")

    p_add = sub.add_parser("add-task")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--detail", default="")
    p_add.add_argument("--owner", default="")
    p_add.add_argument("--deadline", default="")
    p_add.add_argument("--priority", default="normal")
    p_add.add_argument("--source-title", default="手动添加")

    p_tog = sub.add_parser("toggle-task")
    p_tog.add_argument("--id", type=int, required=True)
    p_tog.add_argument("--done", choices=["true", "false"], required=True)

    p_del = sub.add_parser("delete-task")
    p_del.add_argument("--id", type=int, required=True)

    sub.add_parser("clear-done")

    args = parser.parse_args(argv)
    store = InboxStore()

    if args.cmd == "pull":
        res = pull_new_messages_for_review(
            base_url=args.url,
            initial_hours=args.initial_hours,
            max_sessions=args.max_sessions,
            do_refresh=not args.no_refresh,
            store=store,
        )
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    if args.cmd == "commit":
        raw = args.json_str
        if args.json_file:
            raw = Path(args.json_file).read_text(encoding="utf-8-sig")
        elif not raw:
            raw = sys.stdin.read()
        data = json.loads(raw.lstrip("\ufeff"))
        res = commit_reviewed_todos(data, trigger_type=args.trigger, agent_name=args.agent, store=store)
        print(json.dumps(res, ensure_ascii=False))
        return 0

    if args.cmd == "list-tasks":
        print(json.dumps(list_tasks_payload(store), ensure_ascii=False))
        return 0

    if args.cmd == "add-task":
        task, created = store.add_task(
            title=args.title.strip(),
            detail=args.detail.strip(),
            owner=args.owner.strip(),
            deadline=args.deadline.strip(),
            priority=args.priority.strip() or "normal",
            source_title=args.source_title.strip(),
            deduplicate=False,
        )
        print(json.dumps({"ok": True, "created": created, "task": asdict(task)}, ensure_ascii=False))
        return 0

    if args.cmd == "toggle-task":
        store.toggle_task(args.id, args.done == "true")
        print(json.dumps({"ok": True, "id": args.id, "done": args.done == "true"}, ensure_ascii=False))
        return 0

    if args.cmd == "delete-task":
        store.delete_task(args.id)
        print(json.dumps({"ok": True, "id": args.id}, ensure_ascii=False))
        return 0

    if args.cmd == "clear-done":
        n = store.clear_completed_tasks()
        print(json.dumps({"ok": True, "cleared": n}, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

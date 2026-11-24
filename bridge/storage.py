"""SQLite persistence for WeChat Inbox items and Agent delivery history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import INBOX_DB_PATH, ensure_data_dirs
from .parser import AttachmentInfo, ChatMessage, ParsedBundle


@dataclass
class TaskItem:
    id: int
    title: str
    detail: str
    owner: str
    deadline: str
    priority: str  # high | normal | low
    status: str  # todo | done
    source_bundle_id: str
    source_title: str
    fingerprint: str
    created_at: str
    completed_at: str


@dataclass
class SyncLogRecord:
    id: int
    synced_at: str
    trigger_type: str  # hourly | manual | bundle_extract
    agent_name: str
    bundles_scanned: int
    new_tasks_count: int
    summary: str
    status: str  # success | noop | error


def task_fingerprint(title: str) -> str:
    norm = "".join(ch.lower() for ch in title.strip() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return hashlib.sha1(norm.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class DeliveryRecord:
    id: int
    bundle_id: str
    target_id: str
    target_name: str
    template_name: str
    status: str  # running | success | error
    mode: str
    summary: str
    output_text: str
    output_path: str
    created_at: str


class InboxStore:
    """Thread-safe lightweight SQLite repository for captured bundles and delivery runs."""

    def __init__(self, db_path: Path | None = None) -> None:
        ensure_data_dirs()
        self.db_path = db_path or INBOX_DB_PATH
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bundles (
                    bundle_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    bundle_dir TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    plain_text TEXT NOT NULL,
                    markdown_text TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    participants_json TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    urls_json TEXT NOT NULL,
                    original_archives_json TEXT NOT NULL,
                    batch_count INTEGER NOT NULL DEFAULT 1,
                    queued_for_merge INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bundle_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    template_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    output_text TEXT NOT NULL DEFAULT '',
                    output_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(bundle_id) REFERENCES bundles(bundle_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_bundles_created ON bundles(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_deliveries_bundle ON deliveries(bundle_id, id DESC);

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    deadline TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'todo',
                    source_bundle_id TEXT NOT NULL DEFAULT '',
                    source_title TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    synced_at TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    bundles_scanned INTEGER NOT NULL DEFAULT 0,
                    new_tasks_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, id DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_fp ON tasks(fingerprint);
                CREATE INDEX IF NOT EXISTS idx_sync_logs_time ON sync_logs(id DESC);
                """
            )

    def save_bundle(self, bundle: ParsedBundle, queued_for_merge: bool = False) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bundles (
                    bundle_id, title, source, created_at, bundle_dir, markdown_path,
                    plain_text, markdown_text, messages_json, participants_json,
                    attachments_json, urls_json, original_archives_json, batch_count, queued_for_merge
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle.bundle_id,
                    bundle.title,
                    bundle.source,
                    bundle.created_at,
                    bundle.bundle_dir,
                    bundle.markdown_path,
                    bundle.plain_text,
                    bundle.markdown_text,
                    json.dumps([m.__dict__ for m in bundle.messages], ensure_ascii=False),
                    json.dumps(bundle.participants, ensure_ascii=False),
                    json.dumps([a.__dict__ for a in bundle.attachments], ensure_ascii=False),
                    json.dumps(bundle.urls, ensure_ascii=False),
                    json.dumps(bundle.original_archives, ensure_ascii=False),
                    bundle.batch_count,
                    1 if queued_for_merge else 0,
                ),
            )

    def _row_to_bundle(self, row: sqlite3.Row) -> ParsedBundle:
        msgs = [ChatMessage(**m) for m in json.loads(row["messages_json"] or "[]")]
        atts = [AttachmentInfo(**a) for a in json.loads(row["attachments_json"] or "[]")]
        return ParsedBundle(
            bundle_id=row["bundle_id"],
            title=row["title"],
            source=row["source"],
            created_at=row["created_at"],
            bundle_dir=row["bundle_dir"],
            markdown_path=row["markdown_path"],
            plain_text=row["plain_text"],
            markdown_text=row["markdown_text"],
            messages=msgs,
            participants=json.loads(row["participants_json"] or "[]"),
            attachments=atts,
            urls=json.loads(row["urls_json"] or "[]"),
            original_archives=json.loads(row["original_archives_json"] or "[]"),
            batch_count=int(row["batch_count"] or 1),
        )

    def list_bundles(self, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if search.strip():
                like = f"%{search.strip()}%"
                rows = conn.execute(
                    """
                    SELECT b.*,
                           (SELECT COUNT(*) FROM deliveries d WHERE d.bundle_id = b.bundle_id AND d.status = 'success') AS success_count
                    FROM bundles b
                    WHERE b.title LIKE ? OR b.plain_text LIKE ? OR b.participants_json LIKE ?
                    ORDER BY b.created_at DESC
                    LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT b.*,
                           (SELECT COUNT(*) FROM deliveries d WHERE d.bundle_id = b.bundle_id AND d.status = 'success') AS success_count
                    FROM bundles b
                    ORDER BY b.created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            result = []
            for r in rows:
                result.append(
                    {
                        "bundle": self._row_to_bundle(r),
                        "queued_for_merge": bool(r["queued_for_merge"]),
                        "success_count": int(r["success_count"] or 0),
                    }
                )
            return result

    def get_bundle(self, bundle_id: str) -> ParsedBundle | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM bundles WHERE bundle_id = ?", (bundle_id,)).fetchone()
            return self._row_to_bundle(row) if row else None

    def set_queued_for_merge(self, bundle_id: str, queued: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE bundles SET queued_for_merge = ? WHERE bundle_id = ?",
                (1 if queued else 0, bundle_id),
            )

    def get_queued_bundles(self) -> list[ParsedBundle]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bundles WHERE queued_for_merge = 1 ORDER BY created_at ASC"
            ).fetchall()
            return [self._row_to_bundle(r) for r in rows]

    def clear_merge_queue(self) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE bundles SET queued_for_merge = 0 WHERE queued_for_merge = 1")

    def delete_bundle(self, bundle_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM deliveries WHERE bundle_id = ?", (bundle_id,))
            conn.execute("DELETE FROM bundles WHERE bundle_id = ?", (bundle_id,))

    def record_delivery(
        self,
        *,
        bundle_id: str,
        target_id: str,
        target_name: str,
        template_name: str,
        status: str,
        mode: str,
        summary: str = "",
        output_text: str = "",
        output_path: str = "",
    ) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO deliveries (
                    bundle_id, target_id, target_name, template_name, status,
                    mode, summary, output_text, output_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle_id,
                    target_id,
                    target_name,
                    template_name,
                    status,
                    mode,
                    summary,
                    output_text,
                    output_path,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def update_delivery(
        self,
        delivery_id: int,
        *,
        status: str,
        summary: str,
        output_text: str,
        output_path: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE deliveries
                SET status = ?, summary = ?, output_text = ?, output_path = ?
                WHERE id = ?
                """,
                (status, summary, output_text, output_path, delivery_id),
            )

    def list_deliveries(self, bundle_id: str) -> list[DeliveryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM deliveries WHERE bundle_id = ? ORDER BY id DESC",
                (bundle_id,),
            ).fetchall()
            return [
                DeliveryRecord(
                    id=int(r["id"]),
                    bundle_id=r["bundle_id"],
                    target_id=r["target_id"],
                    target_name=r["target_name"],
                    template_name=r["template_name"],
                    status=r["status"],
                    mode=r["mode"],
                    summary=r["summary"],
                    output_text=r["output_text"],
                    output_path=r["output_path"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    # ── Tasks & Hourly Sync Logs ────────────────────────────────────────────

    def _row_to_task(self, r: sqlite3.Row) -> TaskItem:
        return TaskItem(
            id=int(r["id"]),
            title=r["title"],
            detail=r["detail"],
            owner=r["owner"],
            deadline=r["deadline"],
            priority=r["priority"],
            status=r["status"],
            source_bundle_id=r["source_bundle_id"],
            source_title=r["source_title"],
            fingerprint=r["fingerprint"],
            created_at=r["created_at"],
            completed_at=r["completed_at"],
        )

    def add_task(
        self,
        *,
        title: str,
        detail: str = "",
        owner: str = "",
        deadline: str = "",
        priority: str = "normal",
        source_bundle_id: str = "",
        source_title: str = "",
        deduplicate: bool = True,
    ) -> tuple[TaskItem, bool]:
        clean_title = " ".join(title.strip().split())[:120]
        if not clean_title:
            raise ValueError("Task title cannot be empty")
        prio = priority if priority in ("high", "normal", "low") else "normal"
        fp = task_fingerprint(clean_title)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            if deduplicate and fp:
                existing = conn.execute(
                    "SELECT * FROM tasks WHERE fingerprint = ? ORDER BY id DESC LIMIT 1",
                    (fp,),
                ).fetchone()
                if existing is not None:
                    # Merge fresh detail/deadline if existing lacked it
                    if detail and not existing["detail"]:
                        conn.execute("UPDATE tasks SET detail = ? WHERE id = ?", (detail[:400], existing["id"]))
                        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (existing["id"],)).fetchone()
                    return self._row_to_task(existing), False

            cur = conn.execute(
                """
                INSERT INTO tasks (
                    title, detail, owner, deadline, priority, status,
                    source_bundle_id, source_title, fingerprint, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, 'todo', ?, ?, ?, ?, '')
                """,
                (
                    clean_title,
                    detail.strip()[:400],
                    owner.strip()[:40],
                    deadline.strip()[:40],
                    prio,
                    source_bundle_id,
                    source_title[:80],
                    fp,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_task(row), True

    def list_tasks(self, include_done: bool = True) -> list[TaskItem]:
        with self._connect() as conn:
            if include_done:
                rows = conn.execute(
                    """
                    SELECT * FROM tasks
                    ORDER BY
                        CASE status WHEN 'todo' THEN 0 ELSE 1 END ASC,
                        CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END ASC,
                        id DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE status = 'todo'
                    ORDER BY
                        CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END ASC,
                        id DESC
                    """
                ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def toggle_task(self, task_id: int, done: bool) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if done else ""
        status = "done" if done else "todo"
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
                (status, now, task_id),
            )

    def delete_task(self, task_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def clear_completed_tasks(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE status = 'done'")
            return int(cur.rowcount or 0)

    def get_bundles_since(self, since_ts: str, limit: int = 20) -> list[ParsedBundle]:
        with self._connect() as conn:
            if since_ts:
                rows = conn.execute(
                    "SELECT * FROM bundles WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
                    (since_ts, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bundles ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_bundle(r) for r in rows]

    def record_sync_log(
        self,
        *,
        trigger_type: str,
        agent_name: str,
        bundles_scanned: int,
        new_tasks_count: int,
        summary: str,
        status: str,
    ) -> SyncLogRecord:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO sync_logs (
                    synced_at, trigger_type, agent_name, bundles_scanned,
                    new_tasks_count, summary, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (now, trigger_type, agent_name, bundles_scanned, new_tasks_count, summary, status),
            )
            return SyncLogRecord(
                id=int(cur.lastrowid),
                synced_at=now,
                trigger_type=trigger_type,
                agent_name=agent_name,
                bundles_scanned=bundles_scanned,
                new_tasks_count=new_tasks_count,
                summary=summary,
                status=status,
            )

    def list_sync_logs(self, limit: int = 30) -> list[SyncLogRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                SyncLogRecord(
                    id=int(r["id"]),
                    synced_at=r["synced_at"],
                    trigger_type=r["trigger_type"],
                    agent_name=r["agent_name"],
                    bundles_scanned=int(r["bundles_scanned"]),
                    new_tasks_count=int(r["new_tasks_count"]),
                    summary=r["summary"],
                    status=r["status"],
                )
                for r in rows
            ]

    def get_last_sync_time(self) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT synced_at FROM sync_logs WHERE status IN ('success', 'noop') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return str(row["synced_at"]) if row else ""

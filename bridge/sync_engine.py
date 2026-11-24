"""Hourly progress sync engine: scans new WeChat bundles & watch folders, extracts new tasks via Agent or rules."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .agents import create_adapter
from .config import BUNDLES_DIR, DATA_DIR, BridgeConfig, TargetConfig
from .parser import ParsedBundle, build_bundle
from .storage import InboxStore, SyncLogRecord, TaskItem
from .wechat_db_client import (
    WATERMARKS_PATH,
    WeChatDBClient,
    pull_incremental_wechat_bundles,
)


WATCH_EXTS = {".zip", ".txt", ".md"}
SEEN_FILES_PATH = DATA_DIR / "sync_seen_files.json"

_RE_ACTION_KEYWORDS = re.compile(
    r"(待办|TODO|todo|需要|记得|麻烦|尽快|务必|安排|确认|跟进|提交|修复|排查|上线|发送|整理|补充|评审|准备|更新|对接|回复|检查|开发|加一下|加上|新增|改动)"
)
_RE_HIGH_PRIORITY = re.compile(r"(紧急|立刻|马上|务必|尽快|今天内|今晚|ASAP|加急|优先|高优|阻塞|报错|故障)")
_RE_DEADLINE = re.compile(
    r"(今天[上下午晚]?|今晚|明天[上下午晚]?|后天|本[周月]|下[周月]|周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日?|\d{1,2}[:：]\d{2}前?)"
)
_RE_LIST_PREFIX = re.compile(r"^\s*(?:[-*•·]|\[\s?\]|\d+[.、）)])\s+")


@dataclass
class SyncResult:
    ok: bool
    status: str  # success | noop | error
    agent_name: str
    bundles_scanned: int
    new_bundles_ingested: int
    wechat_messages_pulled: int = 0
    new_tasks: list[TaskItem] = field(default_factory=list)
    summary: str = ""
    log: SyncLogRecord | None = None
    error: str = ""


def _load_seen_files(path: Path = SEEN_FILES_PATH) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_seen_files(seen: dict[str, float], path: Path = SEEN_FILES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def scan_watch_folders(
    watch_dirs: list[str],
    store: InboxStore,
    *,
    bundles_root: Path = BUNDLES_DIR,
    seen_path: Path = SEEN_FILES_PATH,
) -> list[ParsedBundle]:
    """Ingest any newly added/modified .zip/.txt/.md files in configured watch folders."""
    if not watch_dirs:
        return []
    seen = _load_seen_files(seen_path)
    created: list[ParsedBundle] = []
    dirty = False

    for raw_dir in watch_dirs:
        d = Path(str(raw_dir).strip())
        if not d.exists() or not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.suffix.lower() not in WATCH_EXTS:
                continue
            key = str(f.resolve())
            mtime = f.stat().st_mtime
            if key in seen and abs(seen[key] - mtime) < 1.0:
                continue
            try:
                bundle = build_bundle(
                    bundles_root=bundles_root,
                    source="watch_folder",
                    title=f.stem,
                    file_paths=[f],
                )
                store.save_bundle(bundle, queued_for_merge=False)
                created.append(bundle)
                seen[key] = mtime
                dirty = True
            except Exception:
                continue

    if dirty:
        _save_seen_files(seen, seen_path)
    return created


def extract_tasks_by_rules(bundles: list[ParsedBundle]) -> tuple[str, list[dict[str, str]]]:
    """Fast local heuristic extractor for Chinese/English action items & additions."""
    extracted: list[dict[str, str]] = []
    seen_titles: set[str] = set()

    for b in bundles:
        candidates: list[tuple[str, str]] = []  # (owner, line)
        if b.messages:
            for msg in b.messages:
                owner = "" if msg.sender in ("微信内容", "聊天记录", "消息") else msg.sender
                for ln in msg.content.splitlines():
                    candidates.append((owner, ln.strip()))
        else:
            for ln in b.plain_text.splitlines():
                candidates.append(("", ln.strip()))

        for owner, raw_line in candidates:
            if not raw_line or len(raw_line) < 4:
                continue
            is_bullet = bool(_RE_LIST_PREFIX.match(raw_line))
            has_kw = bool(_RE_ACTION_KEYWORDS.search(raw_line))
            if not (is_bullet or has_kw):
                continue

            cleaned = _RE_LIST_PREFIX.sub("", raw_line).strip(" ：:;；。")
            if len(cleaned) < 4:
                continue
            title = cleaned[:48] + ("..." if len(cleaned) > 48 else "")
            key = title.lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)

            dl_match = _RE_DEADLINE.search(cleaned)
            deadline = dl_match.group(1) if dl_match else ""
            prio = "high" if _RE_HIGH_PRIORITY.search(cleaned) else "normal"

            extracted.append(
                {
                    "title": title,
                    "detail": cleaned if len(cleaned) > len(title) else "",
                    "owner": owner,
                    "deadline": deadline,
                    "priority": prio,
                    "source_bundle_id": b.bundle_id,
                    "source_title": b.title,
                }
            )
            if len(extracted) >= 20:
                break

    summary = (
        f"规则扫描 {len(bundles)} 份记录，识别到 {len(extracted)} 条潜在新增待办/事项。"
        if extracted
        else f"已巡检 {len(bundles)} 份记录，暂未发现明显新增待办。"
    )
    return summary, extracted


def _parse_agent_json_output(raw_text: str) -> tuple[str, list[dict[str, str]]] | None:
    """Robustly parse JSON object returned by an AI Agent (handles code fences & surrounding prose)."""
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence:
        candidates = [fence.group(1)]
    else:
        start = text.find("{")
        end = text.rfind("}")
        candidates = [text[start : end + 1]] if (start != -1 and end > start) else []

    for blob in candidates:
        try:
            data = json.loads(blob)
            if not isinstance(data, dict):
                continue
            summary = str(data.get("summary") or "").strip()
            raw_tasks = data.get("new_tasks") or data.get("tasks") or []
            if not isinstance(raw_tasks, list):
                continue
            tasks: list[dict[str, str]] = []
            for item in raw_tasks:
                if isinstance(item, str) and item.strip():
                    tasks.append({"title": item.strip()})
                elif isinstance(item, dict) and str(item.get("title") or "").strip():
                    tasks.append(
                        {
                            "title": str(item.get("title") or "").strip(),
                            "detail": str(item.get("detail") or "").strip(),
                            "owner": str(item.get("owner") or "").strip(),
                            "deadline": str(item.get("deadline") or "").strip(),
                            "priority": str(item.get("priority") or "normal").strip().lower(),
                            "source_bundle_id": str(item.get("source_bundle_id") or "").strip(),
                        }
                    )
            return summary, tasks
        except Exception:
            continue
    return None


def _select_sync_target(config: BridgeConfig) -> TargetConfig | None:
    pref = str(config.data.get("sync_agent_id", "auto") or "auto")
    if pref == "local_rules":
        return None

    targets = [t for t in config.get_targets() if t.enabled]
    if pref != "auto":
        for t in targets:
            if t.id == pref and t.mode in ("cli_exec", "webhook"):
                return t

    # Auto priority: Codex CLI -> Claude Code -> OpenClaw -> Webhook
    priority_order = ("codex_cli", "claude_cli", "openclaw_cli", "custom_cli", "webhook")
    for kind in priority_order:
        for t in targets:
            if t.kind == kind and (t.executable or t.webhook_url or t.command_template):
                return t
    return None


def _build_sync_prompt(bundles: list[ParsedBundle], existing_tasks: list[TaskItem]) -> tuple[ParsedBundle, str]:
    existing_lines = []
    for t in existing_tasks[:60]:
        mark = "[已完成]" if t.status == "done" else "[待办]"
        existing_lines.append(f"- {mark} {t.title}")
    existing_block = "\n".join(existing_lines) if existing_lines else "（当前暂无已有待办）"

    bundle_sections: list[str] = []
    for b in bundles:
        snippet = (b.plain_text or b.markdown_text).strip()[:4000]
        bundle_sections.append(
            f"#### 记录 ID: `{b.bundle_id}` | 标题: {b.title} | 时间: {b.created_at}\n{snippet}"
        )

    combined_text = "\n\n---\n\n".join(bundle_sections)
    synthetic = ParsedBundle(
        bundle_id=bundles[0].bundle_id if bundles else "sync_batch",
        title=f"增量进度同步 ({len(bundles)} 份微信记录)",
        source="hourly_sync",
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        bundle_dir=bundles[0].bundle_dir if bundles else str(BUNDLES_DIR),
        markdown_path=bundles[0].markdown_path if bundles else "",
        plain_text=combined_text,
        markdown_text=combined_text,
    )

    instruction = (
        "你是一个个人进度同步助手。请对比「已有待办清单」与下方「最新微信聊天记录」，"
        "找出所有**新增的待办事项、新增需求、新增改动或需要跟进的事项**。\n\n"
        f"### 已有待办清单（请勿重复输出已包含的事项）\n{existing_block}\n\n"
        "### 输出要求（严格仅输出一个合法 JSON 对象，不要附加额外解释）：\n"
        "```json\n"
        "{\n"
        '  "summary": "用1-2句中文概括本次同步发现了哪些新进展或新增事项",\n'
        '  "new_tasks": [\n'
        "    {\n"
        '      "title": "清晰简短的待办标题（建议10-28字，适合勾选）",\n'
        '      "detail": "简短背景或具体改动内容（可选）",\n'
        '      "owner": "负责人（如未提及留空字符串）",\n'
        '      "deadline": "截止时间（如未提及留空字符串）",\n'
        '      "priority": "high 或 normal 或 low",\n'
        '      "source_bundle_id": "对应记录 ID"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
        "如果确实没有新的待办或新增事项，`new_tasks` 返回空数组 `[]` 并在 `summary` 中说明原因。"
    )
    return synthetic, instruction


def run_progress_sync(
    *,
    config: BridgeConfig,
    store: InboxStore,
    trigger_type: str = "hourly",
    explicit_bundles: list[ParsedBundle] | None = None,
    bundles_root: Path = BUNDLES_DIR,
    seen_path: Path = SEEN_FILES_PATH,
    watermarks_path: Path = WATERMARKS_PATH,
    wechat_db_client: WeChatDBClient | None = None,
    on_progress: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> SyncResult:
    """Execute one progress sync cycle and persist newly discovered tasks & sync log."""
    def emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    ingested_wechat: list[ParsedBundle] = []
    wechat_msgs_pulled = 0
    if explicit_bundles is None and bool(config.data.get("wechat_db_enabled", True)):
        db_url = str(config.data.get("wechat_db_url") or f"http://127.0.0.1:{int(config.data.get('wechat_db_port', 5200))}")
        db_client = wechat_db_client or WeChatDBClient(base_url=db_url)
        ingested_wechat, db_meta = pull_incremental_wechat_bundles(
            db_client,
            store,
            watch_sessions=list(config.data.get("wechat_db_watch_sessions") or []),
            max_sessions=int(config.data.get("wechat_db_max_sessions", 8)),
            lookback_hours=int(config.data.get("wechat_db_initial_lookback_hours", 24)),
            do_refresh=bool(config.data.get("wechat_db_refresh_on_sync", True)),
            bundles_root=bundles_root,
            watermarks_path=watermarks_path,
            on_progress=emit,
        )
        wechat_msgs_pulled = int(db_meta.get("messages_pulled") or 0)
        if ingested_wechat:
            emit(f"直连微信库拉取到 {len(ingested_wechat)} 个会话共 {wechat_msgs_pulled} 条新消息。")

    watch_dirs = list(config.data.get("sync_watch_dirs") or [])
    ingested_watch = scan_watch_folders(watch_dirs, store, bundles_root=bundles_root, seen_path=seen_path)
    if ingested_watch:
        emit(f"监听目录发现 {len(ingested_watch)} 份新文件，已自动入库。")

    total_ingested = len(ingested_wechat) + len(ingested_watch)

    if explicit_bundles is not None:
        target_bundles = list(explicit_bundles)
    else:
        last_sync = store.get_last_sync_time()
        since_bundles = store.get_bundles_since(last_sync, limit=15)
        seen_ids = {b.bundle_id for b in ingested_wechat}
        target_bundles = list(ingested_wechat) + [b for b in since_bundles if b.bundle_id not in seen_ids]
        # On manual sync, if no brand-new bundles arrived, inspect the latest 5 bundles to catch missed items
        if not target_bundles and trigger_type == "manual":
            target_bundles = store.get_bundles_since("", limit=5)

    existing_tasks = store.list_tasks(include_done=True)
    open_count = sum(1 for t in existing_tasks if t.status == "todo")

    if not target_bundles:
        summary = f"暂无新增微信记录（当前浮窗待完成 {open_count} 项）。"
        log = store.record_sync_log(
            trigger_type=trigger_type,
            agent_name="本地巡检",
            bundles_scanned=0,
            new_tasks_count=0,
            summary=summary,
            status="noop",
        )
        return SyncResult(
            ok=True,
            status="noop",
            agent_name="本地巡检",
            bundles_scanned=0,
            new_bundles_ingested=total_ingested,
            wechat_messages_pulled=wechat_msgs_pulled,
            new_tasks=[],
            summary=summary,
            log=log,
        )

    bundle_title_map = {b.bundle_id: b.title for b in target_bundles}
    sync_target = _select_sync_target(config)
    agent_name = sync_target.name if sync_target else "本地快速规则提取"

    parsed_result: tuple[str, list[dict[str, str]]] | None = None
    agent_error = ""

    if sync_target is not None:
        emit(f"正在调用 {sync_target.name} 分析 {len(target_bundles)} 份记录的增量与新增事项...")
        synthetic_bundle, prompt_inst = _build_sync_prompt(target_bundles, existing_tasks)
        try:
            adapter = create_adapter(
                sync_target,
                timeout_sec=min(int(config.data.get("cli_timeout_sec", 120)), 150),
                max_inline_chars=int(config.data.get("max_inline_chars", 28000)),
            )
            res = adapter.deliver(synthetic_bundle, prompt_inst, cancel_event=cancel_event)
            if res.ok and res.output_text:
                parsed_result = _parse_agent_json_output(res.output_text)
                if parsed_result is None:
                    agent_error = "Agent 返回非 JSON 格式，已自动切换本地规则提取。"
            else:
                agent_error = res.error or res.summary or "Agent 调用未成功，已自动切换本地规则提取。"
        except Exception as exc:
            agent_error = f"{exc}，已自动切换本地规则提取。"

    if parsed_result is None:
        if agent_error:
            emit(agent_error)
            agent_name = f"{agent_name} (规则兜底)"
        parsed_result = extract_tasks_by_rules(target_bundles)

    summary_text, candidate_tasks = parsed_result
    added_tasks: list[TaskItem] = []

    for item in candidate_tasks:
        title = item.get("title", "").strip()
        if not title:
            continue
        bid = item.get("source_bundle_id", "").strip()
        if bid not in bundle_title_map and target_bundles:
            bid = target_bundles[0].bundle_id
        src_title = item.get("source_title") or bundle_title_map.get(bid, "")
        task, created = store.add_task(
            title=title,
            detail=item.get("detail", ""),
            owner=item.get("owner", ""),
            deadline=item.get("deadline", ""),
            priority=item.get("priority", "normal"),
            source_bundle_id=bid,
            source_title=src_title,
            deduplicate=True,
        )
        if created:
            added_tasks.append(task)

    final_summary = summary_text or f"扫描 {len(target_bundles)} 份记录，新增 {len(added_tasks)} 项待办。"
    if added_tasks and str(len(added_tasks)) not in final_summary:
        final_summary += f"（新增 {len(added_tasks)} 项待办）"

    log = store.record_sync_log(
        trigger_type=trigger_type,
        agent_name=agent_name,
        bundles_scanned=len(target_bundles),
        new_tasks_count=len(added_tasks),
        summary=final_summary,
        status="success",
    )

    return SyncResult(
        ok=True,
        status="success",
        agent_name=agent_name,
        bundles_scanned=len(target_bundles),
        new_bundles_ingested=total_ingested,
        wechat_messages_pulled=wechat_msgs_pulled,
        new_tasks=added_tasks,
        summary=final_summary,
        log=log,
        error=agent_error,
    )

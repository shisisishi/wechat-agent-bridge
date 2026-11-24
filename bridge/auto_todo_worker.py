"""Standalone Offline AI Auto-Todo Worker (Ollama qwen2.5vl:3b / OpenAI-compatible).

Runs independently in the background every N minutes (default: 30m):
1. Checks local wechat-cli (http://127.0.0.1:5200) and pulls ONLY incremental new messages.
2. Applies deterministic noise filtering (skips ads, stickers, pure banter, essay floods).
3. Sends each candidate conversation individually to local Ollama (`qwen2.5vl:3b` by default)
   using strict JSON Schema structured output and `keep_alive: "30s"` so VRAM is freed
   automatically right after each cycle.
4. Deduplicates and commits curated to-dos directly to ~/.wechat-agent-bridge/inbox.db
   (auto-syncing to PC 127.0.0.1:5200 widget, desktop overlay, and mobile HTTPS URL).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ensure_data_dirs
from .hourly_todo_runner import (
    AGENT_WATERMARKS_PATH,
    PENDING_WATERMARKS_PATH,
    commit_reviewed_todos,
    pull_new_messages_for_review,
)
from .storage import InboxStore

WORKER_CONFIG_PATH = DATA_DIR / "auto_worker.json"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:3b"
DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_WECHAT_URL = "http://127.0.0.1:5200"

# Strong actionability keywords (school/work/deadlines/meetings/mentions)
STRONG_SIGNAL_RE = re.compile(
    r"(@所有人|@全体|作业|截止|ddl|DDL|考试|月考|期中|期末|测验|自测|签到|考勤|调课|补课|停课|"
    r"提交|上交|打印|报告|论文|PPT|ppt|汇报|答辩|presentation|开会|会议|讨论会|讲座|辅导|"
    r"报名|接龙|招募|审批|请假|复习|预习|实验|必修|选修|学分|通知|公告|务必|必须|记得|"
    r"明天|今晚|明晚|本周|下周|周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日|\d{1,2}[:：]\d{2}|\[文件\])",
    re.IGNORECASE,
)

# Noise-only messages to strip before evaluating a conversation
NOISE_LINE_RE = re.compile(
    r"^(\[[^\]]{1,12}\]\s*)+$|^(ok|okk|okok|好的|好滴|收到|谢谢|感谢|嗯|嗯嗯|哈哈+|呵+|行|行吧|可以|对|是的|哦|哦哦|[？?！!。，,\.]+)$",
    re.IGNORECASE,
)

AD_SENDER_KEYWORDS = ("促销", "推广", "福利群", "优惠券", "福利官", "客服", "商城", "秒杀", "返现")

TODO_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "deadline": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                },
                "required": ["title", "detail", "deadline", "priority"],
            },
        }
    },
    "required": ["tasks"],
}

SYSTEM_PROMPT = """你是一个极其克制、精准的中文个人待办提炼助手。
你的任务是从单个微信会话的最新消息中，只提炼真正需要「我」亲自处理、参加、回复或记住截止时间的事项。

严格遵守以下过滤规则：
1. 必须忽略：纯闲聊、表情包、吐槽、转发新闻/短视频/播客/直播、广告优惠券、公共失物招领、别人之间的值班/换班/已经被人接走的任务、已经明确完成结束的事情。
2. 只保留：工作/项目交付、评审与会议通知、课程作业/考试/调课签到、小组汇报、活动报名/接龙、以及他人明确要求「我」审批/提交/回复/打印/下载资料的事项。
3. 严禁重复：如果「已有待办列表」里已经包含相同或高度相似的事项，不要重复输出。
4. 如果该会话没有任何需要「我」执行的真实事项，必须返回 {"tasks": []}，宁缺毋滥！
5. 标题 title 简洁明确（12~36字），detail 说明关键信息（时间/地点/文件名），deadline 写具体时间节点（无则留空字符串），priority 仅限 high / normal / low。"""


def load_or_create_worker_config(
    config_path: Path = WORKER_CONFIG_PATH,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_data_dirs()
    defaults: dict[str, Any] = {
        "backend": "ollama",
        "model": DEFAULT_OLLAMA_MODEL,
        "ollama_url": DEFAULT_OLLAMA_URL,
        "keep_alive": "30s",
        "interval_minutes": 30,
        "max_sessions": 25,
        "wechat_url": DEFAULT_WECHAT_URL,
        "deepseek_url": DEFAULT_DEEPSEEK_URL,
        "deepseek_model": DEFAULT_DEEPSEEK_MODEL,
    }
    data = dict(defaults)
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                data[k] = v
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_ad_or_ignored_conversation(conv: dict[str, Any]) -> bool:
    talker = str(conv.get("talker") or "").strip().lower()
    name = str(conv.get("name") or "").strip()
    if talker.endswith("@openim") or talker.startswith("gh_"):
        return True
    if any(k in name for k in AD_SENDER_KEYWORDS):
        return True
    return False


def compact_conversation_for_llm(conv: dict[str, Any]) -> tuple[bool, str]:
    """Deterministically pre-filter a conversation and format a compact digest for small LLMs.

    Returns (should_send_to_llm, formatted_text).
    """
    if is_ad_or_ignored_conversation(conv):
        return False, ""

    name = str(conv.get("name") or conv.get("talker") or "未知会话").strip()
    is_group = bool(conv.get("is_group", False))
    msgs = conv.get("messages") or []
    if not msgs:
        return False, ""

    # Compress homework-submission floods (e.g. classmates posting long essays starting with "作业")
    hw_msgs = [m for m in msgs if re.match(r"^\s*作业\s*[:：]", str(m.get("text") or ""))]
    non_hw_msgs = [m for m in msgs if m not in hw_msgs]

    lines: list[str] = []
    has_strong_signal = False

    if len(hw_msgs) >= 3:
        self_submitted = any(str(m.get("sender") or "") == "我" for m in hw_msgs)
        has_strong_signal = not self_submitted
        lines.append(
            f"[系统摘要] 群内有 {len(hw_msgs)} 条同学提交的「作业：...」长文本，本人(我)在此批消息中是否已提交: {'是' if self_submitted else '否'}"
        )
        candidate_msgs = non_hw_msgs
    else:
        candidate_msgs = msgs

    meaningful_non_self = 0
    for m in candidate_msgs:
        sender = str(m.get("sender") or "").strip() or "对方"
        raw = str(m.get("text") or "").replace("\r", " ").replace("\n", " ").strip()
        if not raw:
            continue
        if raw.startswith("[视频号直播]") or raw.startswith("[服务通知] 当前微信版本不支持"):
            continue
        if NOISE_LINE_RE.match(raw):
            continue
        if any(ad in raw for ad in ("返现 ¥", "立即购买", "免单券", "官方点单小程序")):
            continue

        if STRONG_SIGNAL_RE.search(raw):
            has_strong_signal = True
        if sender != "我":
            meaningful_non_self += 1

        clipped = raw[:240] + ("…" if len(raw) > 240 else "")
        ts = str(m.get("t") or "").strip()
        lines.append(f"[{ts}] {sender}: {clipped}")

    if not lines:
        return False, ""

    # Private 1-on-1 message from someone else with substantive text or question -> evaluate
    if not is_group and meaningful_non_self > 0:
        joined = " ".join(lines)
        if "?" in joined or "？" in joined or "求" in joined or "帮" in joined or "请" in joined or has_strong_signal:
            has_strong_signal = True

    if not has_strong_signal:
        return False, ""

    # Keep total context super crisp for 3B models (last 18 informative lines, <= 2200 chars)
    selected = lines[-18:]
    body = "\n".join(selected)
    if len(body) > 2200:
        body = body[-2200:]
    conv_type = "群聊" if is_group else "私聊"
    header = f"会话名称：{name}（{conv_type}）\n最新消息："
    return True, f"{header}\n{body}"


def ensure_ollama_running(ollama_url: str = DEFAULT_OLLAMA_URL, wait_sec: float = 12.0) -> bool:
    opener = _no_proxy_opener()
    tags_url = f"{ollama_url.rstrip('/')}/api/tags"
    try:
        with opener.open(tags_url, timeout=3) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass

    ollama_bin = os.environ.get("OLLAMA_BIN") or shutil.which("ollama")
    if not ollama_bin:
        for candidate in (
            Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe",
            Path("C:/Program Files/Ollama/ollama.exe"),
        ):
            if candidate.is_file():
                ollama_bin = str(candidate)
                break
    if not ollama_bin:
        return False

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except Exception:
        return False

    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        time.sleep(0.8)
        try:
            with opener.open(tags_url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        return {"tasks": []}
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"tasks": parsed}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return {"tasks": []}


def call_ollama_extract(
    conv_digest: str,
    existing_titles: list[str],
    *,
    model: str = DEFAULT_OLLAMA_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    keep_alive: str = "30s",
    timeout_sec: float = 90.0,
) -> list[dict[str, Any]]:
    existing_block = (
        "\n".join(f"- {t}" for t in existing_titles[:25])
        if existing_titles
        else "（当前无待办）"
    )
    user_msg = (
        f"已有待办列表（请勿重复输出以下内容）：\n{existing_block}\n\n"
        f"{conv_digest}\n\n"
        "请只输出严格 JSON：{\"tasks\": [{\"title\": \"...\", \"detail\": \"...\", \"deadline\": \"...\", \"priority\": \"high|normal|low\"}]}。"
        "如无需要「我」执行的真实事项，返回 {\"tasks\": []}。"
    )
    payload = {
        "model": model,
        "stream": False,
        "keep_alive": keep_alive,
        "format": TODO_JSON_SCHEMA,
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
            "num_ctx": 4096,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    }
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = _no_proxy_opener()
    with opener.open(req, timeout=timeout_sec) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    content = str(((data.get("message") or {}).get("content")) or "")
    parsed = _extract_json_object(content)
    tasks = parsed.get("tasks") or []
    return [t for t in tasks if isinstance(t, dict)]


def call_deepseek_extract(
    conv_digest: str,
    existing_titles: list[str],
    *,
    api_key: str,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    api_url: str = DEFAULT_DEEPSEEK_URL,
    timeout_sec: float = 45.0,
) -> list[dict[str, Any]]:
    existing_block = (
        "\n".join(f"- {t}" for t in existing_titles[:30])
        if existing_titles
        else "（当前无待办）"
    )
    user_msg = (
        f"已有待办列表（请勿重复输出）：\n{existing_block}\n\n"
        f"{conv_digest}\n\n"
        "严格返回 JSON 对象 {\"tasks\": [{\"title\": \"...\", \"detail\": \"...\", \"deadline\": \"...\", \"priority\": \"high|normal|low\"}]}。"
    )
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    choices = data.get("choices") or []
    content = str((((choices[0].get("message") if choices else {}) or {}).get("content")) or "")
    parsed = _extract_json_object(content)
    tasks = parsed.get("tasks") or []
    return [t for t in tasks if isinstance(t, dict)]


def is_fuzzy_duplicate(title: str, existing_titles: list[str], threshold: float = 0.68) -> bool:
    clean = re.sub(r"[\s《》【】\[\]()（）:：,，。！？!?·\-_]+", "", title.lower())
    if len(clean) < 4:
        return True
    for old in existing_titles:
        old_clean = re.sub(r"[\s《》【】\[\]()（）:：,，。！？!?·\-_]+", "", old.lower())
        if not old_clean:
            continue
        if clean in old_clean or old_clean in clean:
            return True
        if difflib.SequenceMatcher(None, clean, old_clean).ratio() >= threshold:
            return True
    return False


def _is_wechat_cli_fresh(wechat_url: str) -> tuple[bool, bool]:
    """Returns (is_alive, recently_refreshed)."""
    opener = _no_proxy_opener()
    try:
        with opener.open(f"{wechat_url.rstrip('/')}/api/health", timeout=4) as r:
            if r.status != 200:
                return False, False
    except Exception:
        return False, False
    try:
        with opener.open(f"{wechat_url.rstrip('/')}/api/last-refresh", timeout=4) as r:
            lr = json.loads(r.read().decode("utf-8", "replace")).get("lastRefresh")
            return True, bool(lr)
    except Exception:
        return True, False


def run_single_cycle(
    config: dict[str, Any],
    *,
    store: InboxStore | None = None,
    watermarks_path: Path = AGENT_WATERMARKS_PATH,
    pending_path: Path = PENDING_WATERMARKS_PATH,
    extractor_fn: Any | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    wechat_url = str(config.get("wechat_url") or DEFAULT_WECHAT_URL)
    backend = str(config.get("backend") or "ollama").lower()
    model = str(config.get("model") or DEFAULT_OLLAMA_MODEL)
    max_sessions = int(config.get("max_sessions") or 25)

    alive, has_auto_refresh = _is_wechat_cli_fresh(wechat_url)
    if not alive:
        return {"ok": False, "error": f"wechat-cli ({wechat_url}) 未运行，跳过本轮巡检"}

    # Avoid double-decrypt contention when 60s auto-refresh is already active
    do_refresh = force_refresh or (not has_auto_refresh)
    pulled = pull_new_messages_for_review(
        base_url=wechat_url,
        max_sessions=max_sessions,
        do_refresh=do_refresh,
        watermarks_path=watermarks_path,
        pending_path=pending_path,
        store=store,
    )
    if not pulled.get("ok"):
        return {"ok": False, "error": str(pulled.get("error") or "pull failed")}

    convs = pulled.get("new_conversations") or []
    existing_tasks = pulled.get("existing_tasks") or []
    existing_titles = [str(t.get("title") or "") for t in existing_tasks if t.get("title")]

    # Stage 1: Deterministic pre-filter
    candidates: list[tuple[str, str]] = []
    for conv in convs:
        keep, digest = compact_conversation_for_llm(conv)
        if keep and digest:
            candidates.append((str(conv.get("name") or conv.get("talker") or ""), digest))

    curated: list[dict[str, Any]] = []
    llm_errors: list[str] = []

    if candidates:
        if extractor_fn is None and backend == "ollama":
            if not ensure_ollama_running(str(config.get("ollama_url") or DEFAULT_OLLAMA_URL)):
                return {"ok": False, "error": "本地 Ollama 服务未启动且自动拉起失败"}

        seen_titles = list(existing_titles)
        for source_name, digest in candidates:
            try:
                if extractor_fn is not None:
                    raw_items = extractor_fn(digest, seen_titles)
                elif backend == "deepseek":
                    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
                    if not api_key:
                        raise RuntimeError("未检测到 DEEPSEEK_API_KEY 环境变量")
                    raw_items = call_deepseek_extract(
                        digest,
                        seen_titles,
                        api_key=api_key,
                        model=str(config.get("deepseek_model") or DEFAULT_DEEPSEEK_MODEL),
                        api_url=str(config.get("deepseek_url") or DEFAULT_DEEPSEEK_URL),
                    )
                else:
                    raw_items = call_ollama_extract(
                        digest,
                        seen_titles,
                        model=model,
                        ollama_url=str(config.get("ollama_url") or DEFAULT_OLLAMA_URL),
                        keep_alive=str(config.get("keep_alive") or "30s"),
                    )
            except Exception as exc:
                llm_errors.append(f"{source_name}: {exc}")
                continue

            for item in raw_items[:3]:
                title = " ".join(str(item.get("title") or "").strip().split())
                if len(title) < 4:
                    continue
                if is_fuzzy_duplicate(title, seen_titles):
                    continue
                prio = str(item.get("priority") or "normal").strip().lower()
                if prio not in ("high", "normal", "low"):
                    prio = "normal"
                seen_titles.append(title)
                curated.append(
                    {
                        "title": title[:80],
                        "detail": str(item.get("detail") or "").strip()[:280],
                        "owner": "我",
                        "deadline": str(item.get("deadline") or "").strip()[:40],
                        "priority": prio,
                        "source_title": source_name[:80],
                    }
                )

    # If ALL candidate LLM calls failed, do NOT advance watermarks so next cycle retries!
    if candidates and llm_errors and len(llm_errors) == len(candidates):
        return {
            "ok": False,
            "error": "小模型提炼全部失败，保留水位线待下轮重试: " + "; ".join(llm_errors[:3]),
        }

    sessions_checked = int(pulled.get("sessions_checked") or 0)
    sessions_with_new = int(pulled.get("sessions_with_new") or 0)
    total_new_msgs = int(pulled.get("total_new_messages") or 0)
    agent_label = f"本地小模型 ({model})" if backend == "ollama" else f"DeepSeek ({config.get('deepseek_model', DEFAULT_DEEPSEEK_MODEL)})"

    if curated:
        summary = (
            f"静默巡检 {sessions_checked} 个会话（{sessions_with_new} 个有新消息共 {total_new_msgs} 条，"
            f"规则筛出 {len(candidates)} 个候选），{agent_label} 新增 {len(curated)} 项待办。"
        )
    elif total_new_msgs > 0:
        summary = (
            f"静默巡检 {sessions_checked} 个会话（{sessions_with_new} 个有新消息共 {total_new_msgs} 条，"
            f"候选 {len(candidates)} 个），无新增待办。"
        )
    else:
        summary = f"静默巡检 {sessions_checked} 个活跃会话，暂无新消息。"

    committed = commit_reviewed_todos(
        {
            "sessions_scanned": sessions_checked,
            "summary": summary,
            "new_tasks": curated,
        },
        trigger_type="auto_worker",
        agent_name=agent_label,
        watermarks_path=watermarks_path,
        pending_path=pending_path,
        store=store,
    )
    committed["candidates_sent_to_llm"] = len(candidates)
    committed["total_new_messages"] = total_new_msgs
    committed["llm_errors"] = llm_errors
    return committed


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="auto-todo-worker")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--interval-minutes", type=int, default=None)
    parser.add_argument("--backend", choices=["ollama", "deepseek"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_or_create_worker_config(
        overrides={
            "interval_minutes": args.interval_minutes,
            "backend": args.backend,
            "model": args.model,
        }
    )

    interval_min = max(1, int(cfg.get("interval_minutes") or 30))
    model_name = cfg["model"] if cfg["backend"] == "ollama" else cfg["deepseek_model"]
    print(
        f"[AutoTodoWorker] 启动离线静默巡检 | 引擎={cfg['backend']}:{model_name} | 间隔={interval_min}分钟 | 显存释放={cfg.get('keep_alive', '30s')}",
        flush=True,
    )

    if args.once:
        res = run_single_cycle(cfg, force_refresh=args.force_refresh)
        print(json.dumps(res, ensure_ascii=False, indent=2), flush=True)
        return 0 if res.get("ok") else 1

    try:
        while True:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                res = run_single_cycle(cfg, force_refresh=False)
                if res.get("ok"):
                    log_summary = ((res.get("log") or {}).get("summary")) or ""
                    print(
                        f"[{ts}] OK | added={res.get('added_count', 0)} | todo={res.get('todo_total', '?')} | {log_summary}",
                        flush=True,
                    )
                else:
                    print(f"[{ts}] WARN | {res.get('error')}", flush=True)
            except Exception as exc:
                print(f"[{ts}] ERROR | {exc}", flush=True)
            time.sleep(interval_min * 60)
    except KeyboardInterrupt:
        print("[AutoTodoWorker] 已停止。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

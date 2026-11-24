"""Configuration, defaults, runtime discovery persistence, and prompt templates."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


DATA_DIR = Path.home() / ".wechat-agent-bridge"
CONFIG_PATH = DATA_DIR / "config.json"
RUNTIME_INFO_PATH = DATA_DIR / "bridge_runtime.json"
INBOX_DB_PATH = DATA_DIR / "inbox.db"
BUNDLES_DIR = DATA_DIR / "bundles"
INCOMING_DIR = DATA_DIR / "incoming"
RESULTS_DIR = DATA_DIR / "results"
LOGS_DIR = DATA_DIR / "logs"


@dataclass
class PromptTemplate:
    id: str
    name: str
    description: str
    prompt: str


@dataclass
class TargetConfig:
    id: str
    name: str
    kind: str  # antigravity_gui | codex_cli | codex_gui | claude_cli | openclaw_cli | obsidian | custom_cli | webhook | custom_gui
    enabled: bool = True
    mode: str = "cli_exec"  # cli_exec | gui_handoff | obsidian | webhook
    description: str = ""
    executable: str = ""
    working_dir: str = ""
    extra_args: list[str] = field(default_factory=list)
    # Obsidian-specific
    vault_path: str = ""
    subfolder: str = "WeChatInbox"
    open_after_save: bool = True
    # GUI handoff-specific
    clipboard_mode: str = "prompt_and_chat"  # prompt_and_chat | prompt_with_path | files_only
    window_title_hint: str = ""
    process_name: str = ""
    auto_paste: bool = False
    # Webhook / OpenAI-compatible API
    webhook_url: str = ""
    webhook_headers: dict[str, str] = field(default_factory=dict)
    api_model: str = ""
    # OpenClaw-specific
    openclaw_agent_id: str = ""
    openclaw_local: bool = False
    # Custom CLI
    command_template: str = ""


DEFAULT_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "summary_actions",
        "name": "提炼要点与待办",
        "description": "快速梳理聊天核心结论、待办事项、责任人与时间节点",
        "prompt": (
            "请阅读以下微信聊天记录（及附件），帮我整理成结构化纪要：\n"
            "1. **核心背景与结论**（3-5 条精炼总结）\n"
            "2. **待办事项（Action Items）**（列出事项、负责人、截止时间）\n"
            "3. **待确认/风险点**（如有分歧或未决问题）\n"
            "4. **关键原话摘录**（保留重要数据、链接或承诺）"
        ),
    },
    {
        "id": "knowledge_note",
        "name": "沉淀知识库笔记",
        "description": "将碎片群聊整理成可复用的结构化 Markdown 知识卡片",
        "prompt": (
            "请把以下微信聊天记录中的干货提炼为一篇高质量的知识库笔记（Markdown 格式）：\n"
            "- 提炼清晰标题与 3-5 个标签（Tags）\n"
            "- 过滤寒暄和水话，按主题分章节结构化整理\n"
            "- 提取关键观点、方法论、工具/资源链接、经验教训\n"
            "- 结尾附「可落地行动清单」"
        ),
    },
    {
        "id": "business_insights",
        "name": "挖掘商机与用户痛点",
        "description": "分析讨论中的高频痛点、潜在需求、产品机会与竞品线索",
        "prompt": (
            "请以产品经理与商业分析师的视角深度分析以下微信聊天记录：\n"
            "1. **暴露出的核心痛点**（附具体场景和原话依据）\n"
            "2. **潜在商机 / 产品机会**（可做的工具、服务或内容方向）\n"
            "3. **提及的竞品 / 工具 / 报价与决策因素**\n"
            "4. **建议的下一步验证动作**"
        ),
    },
    {
        "id": "tech_debug",
        "name": "技术排障与方案梳理",
        "description": "整理聊天中的报错信息、上下文线索并给出排查建议",
        "prompt": (
            "请整理以下微信沟通中的技术问题上下文：\n"
            "1. **问题现象与复现条件**\n"
            "2. **涉及的系统、组件、报错日志与附件线索**\n"
            "3. **可能的根因分析（按概率排序）**\n"
            "4. **建议的排查命令与修复方案**"
        ),
    },
    {
        "id": "raw_forward",
        "name": "原始记录直发（无附加要求）",
        "description": "保留整理后的聊天记录和附件路径，直接交给 Agent 等待进一步指令",
        "prompt": "以下是我从微信转发过来的聊天记录和附件上下文，请先阅读并等待我的进一步指令，或先简短确认你已理解核心内容。",
    },
]


def ensure_data_dirs() -> None:
    for d in (DATA_DIR, BUNDLES_DIR, INCOMING_DIR, RESULTS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def get_or_create_ipc_token() -> str:
    ensure_data_dirs()
    token_file = DATA_DIR / "ipc_token.txt"
    if token_file.exists():
        tok = token_file.read_text(encoding="utf-8").strip()
        if len(tok) >= 16:
            return tok
    tok = secrets.token_hex(24)
    token_file.write_text(tok, encoding="utf-8")
    return tok


class BridgeConfig:
    """Persistent configuration manager."""

    def __init__(self, path: Path | None = None) -> None:
        ensure_data_dirs()
        self.path = path or CONFIG_PATH
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        self._apply_defaults()

    def _apply_defaults(self) -> None:
        self.data.setdefault("ipc_port", 18792)
        self.data.setdefault("hotkey", "Ctrl+Alt+W")
        self.data.setdefault("show_floating_pill", True)
        self.data.setdefault("floating_pill_pos", None)
        self.data.setdefault("show_task_overlay", True)
        self.data.setdefault("task_overlay_geometry", None)
        self.data.setdefault("auto_sync_enabled", True)
        self.data.setdefault("sync_interval_minutes", 60)
        self.data.setdefault("sync_agent_id", "auto")  # auto | codex_cli | claude_cli | openclaw_cli | local_rules
        self.data.setdefault("sync_watch_dirs", [])
        self.data.setdefault("wechat_db_enabled", True)
        self.data.setdefault("wechat_db_port", 5200)
        self.data.setdefault("wechat_db_url", "http://127.0.0.1:5200")
        self.data.setdefault("wechat_db_auto_start", True)
        self.data.setdefault("wechat_db_refresh_on_sync", True)
        self.data.setdefault("wechat_db_watch_sessions", [])
        self.data.setdefault("wechat_db_max_sessions", 8)
        self.data.setdefault("wechat_db_initial_lookback_hours", 24)
        self.data.setdefault("default_target_id", "antigravity_gui")
        self.data.setdefault("default_template_id", "summary_actions")
        self.data.setdefault("auto_open_on_share", True)
        self.data.setdefault("batch_collect_mode", False)
        self.data.setdefault("max_inline_chars", 28000)
        self.data.setdefault("cli_timeout_sec", 240)
        self.data.setdefault("theme", "dark")
        self.data.setdefault("templates", list(DEFAULT_TEMPLATES))
        self.data.setdefault("targets", [])

    def save(self) -> None:
        ensure_data_dirs()
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def ipc_port(self) -> int:
        return int(self.data.get("ipc_port", 18792))

    @property
    def default_target_id(self) -> str:
        return str(self.data.get("default_target_id", "antigravity_gui"))

    @default_target_id.setter
    def default_target_id(self, value: str) -> None:
        self.data["default_target_id"] = value

    @property
    def default_template_id(self) -> str:
        return str(self.data.get("default_template_id", "summary_actions"))

    @default_template_id.setter
    def default_template_id(self, value: str) -> None:
        self.data["default_template_id"] = value

    def get_templates(self) -> list[PromptTemplate]:
        raw = self.data.get("templates") or DEFAULT_TEMPLATES
        out: list[PromptTemplate] = []
        for item in raw:
            try:
                out.append(PromptTemplate(**item))
            except TypeError:
                continue
        return out

    def get_template(self, template_id: str) -> PromptTemplate:
        for t in self.get_templates():
            if t.id == template_id:
                return t
        templates = self.get_templates()
        return templates[0] if templates else PromptTemplate(**DEFAULT_TEMPLATES[0])

    def set_templates(self, templates: list[PromptTemplate]) -> None:
        self.data["templates"] = [asdict(t) for t in templates]

    def get_targets(self) -> list[TargetConfig]:
        out: list[TargetConfig] = []
        for item in self.data.get("targets", []):
            try:
                out.append(TargetConfig(**item))
            except TypeError:
                # Filter unknown keys gracefully for forward compatibility
                valid_keys = TargetConfig.__dataclass_fields__.keys()
                filtered = {k: v for k, v in item.items() if k in valid_keys}
                out.append(TargetConfig(**filtered))
        return out

    def get_target(self, target_id: str) -> TargetConfig | None:
        for t in self.get_targets():
            if t.id == target_id:
                return t
        return None

    def set_targets(self, targets: list[TargetConfig]) -> None:
        self.data["targets"] = [asdict(t) for t in targets]

    def upsert_target(self, target: TargetConfig) -> None:
        targets = self.get_targets()
        for idx, existing in enumerate(targets):
            if existing.id == target.id:
                targets[idx] = target
                self.set_targets(targets)
                return
        targets.append(target)
        self.set_targets(targets)

    def remove_target(self, target_id: str) -> None:
        targets = [t for t in self.get_targets() if t.id != target_id]
        self.set_targets(targets)

    def ensure_detected_targets(self, detected_targets: list[TargetConfig]) -> None:
        """Merge newly discovered local targets without clobbering user customizations."""
        existing_map = {t.id: t for t in self.get_targets()}
        merged: list[TargetConfig] = list(existing_map.values())
        changed = False
        for dt in detected_targets:
            if dt.id not in existing_map:
                merged.append(dt)
                changed = True
            else:
                cur = existing_map[dt.id]
                # Refresh missing auto-detected paths if user hasn't set one
                if not cur.executable and dt.executable:
                    cur.executable = dt.executable
                    changed = True
                if not cur.vault_path and dt.vault_path:
                    cur.vault_path = dt.vault_path
                    changed = True
        if changed or not self.path.exists():
            self.set_targets(merged)
            # Pick a sensible default target if current doesn't exist
            ids = {t.id for t in merged}
            if self.default_target_id not in ids and merged:
                self.default_target_id = merged[0].id
            self.save()


def write_runtime_info(project_root: Path, port: int, token: str, python_exe: str) -> None:
    """Persist runtime metadata so the native Windows ShareTarget helper can find the bridge."""
    ensure_data_dirs()
    pythonw = Path(python_exe).with_name("pythonw.exe")
    launcher_exe = str(pythonw if pythonw.exists() else Path(python_exe))
    info = {
        "port": port,
        "token": token,
        "project_root": str(project_root.resolve()),
        "launch_script": str((project_root / "launch.pyw").resolve()),
        "cli_script": str((project_root / "run.py").resolve()),
        "python_exe": str(python_exe),
        "pythonw_exe": launcher_exe,
        "incoming_dir": str(INCOMING_DIR.resolve()),
        "pid": os.getpid(),
    }
    RUNTIME_INFO_PATH.write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

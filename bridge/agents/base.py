"""Base adapter interface and structured prompt builder for all Agent targets."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from ..config import TargetConfig
from ..parser import ParsedBundle


ChunkCallback = Callable[[str], None]


@dataclass
class DeliveryResult:
    ok: bool
    mode: str
    summary: str
    output_text: str = ""
    output_path: str = ""
    error: str = ""


def compose_agent_prompt(
    bundle: ParsedBundle,
    prompt_instruction: str,
    *,
    max_inline_chars: int = 28000,
    include_chat_body: bool = True,
) -> str:
    """Assemble a clean, self-contained prompt combining instruction, metadata, attachments, and chat."""
    sections: list[str] = []
    if prompt_instruction.strip():
        sections.append(prompt_instruction.strip())

    meta_lines = [
        "---",
        "### 微信转发上下文元数据",
        f"- **标题**: {bundle.title}",
        f"- **来源通道**: {bundle.source}",
        f"- **捕获时间**: {bundle.created_at}",
        f"- **消息条数**: {len(bundle.messages)}" + (f"（已合并 {bundle.batch_count} 批）" if bundle.batch_count > 1 else ""),
        f"- **完整归档 Markdown**: `{bundle.markdown_path}`",
        f"- **归档文件夹**: `{bundle.bundle_dir}`",
    ]
    if bundle.participants:
        meta_lines.append(f"- **参与人**: {', '.join(bundle.participants[:20])}")
    if bundle.attachments:
        meta_lines.append(f"- **附件 ({len(bundle.attachments)})**:")
        for att in bundle.attachments:
            meta_lines.append(f"  - [{att.kind}] `{att.name}` -> `{att.path}`")
    if bundle.urls:
        meta_lines.append("- **聊天中出现的链接**:")
        for u in bundle.urls[:20]:
            meta_lines.append(f"  - {u}")

    sections.append("\n".join(meta_lines))

    if include_chat_body:
        body = bundle.markdown_text.strip() or bundle.plain_text.strip()
        if body:
            if len(body) > max_inline_chars:
                truncated = body[:max_inline_chars]
                sections.append(
                    "### 聊天记录正文（因内容较长已内联前 "
                    f"{max_inline_chars} 字，完整记录请直接读取 `{bundle.markdown_path}`）\n\n"
                    f"{truncated}\n\n... [其余内容请读取 `{bundle.markdown_path}`]"
                )
            else:
                sections.append(f"### 聊天记录正文\n\n{body}")
    else:
        sections.append(
            f"请直接读取上方完整归档文件 `{bundle.markdown_path}` 及附件目录 `{bundle.bundle_dir}` 中的文件后开始处理。"
        )

    return "\n\n".join(sections).strip() + "\n"


class AgentAdapter(ABC):
    """Abstract interface implemented by every CLI, GUI, Obsidian, or Webhook target."""

    def __init__(self, target: TargetConfig, *, timeout_sec: int = 240, max_inline_chars: int = 28000) -> None:
        self.target = target
        self.timeout_sec = timeout_sec
        self.max_inline_chars = max_inline_chars

    @abstractmethod
    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        """Deliver a parsed WeChat bundle to this target."""

"""Obsidian knowledge base archival adapter with YAML frontmatter and local media wikilinks."""

from __future__ import annotations

import os
import re
import shutil
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path

from ..parser import ParsedBundle
from .base import AgentAdapter, ChunkCallback, DeliveryResult


def _sanitize_note_name(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f#\[\]]+', " ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:70] or "微信聊天记录"


class ObsidianAdapter(AgentAdapter):
    """Exports a WeChat bundle cleanly into an Obsidian vault with embedded attachments."""

    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        vault_str = self.target.vault_path.strip()
        if not vault_str:
            return DeliveryResult(
                ok=False,
                mode="obsidian",
                summary="未配置 Obsidian Vault 目录",
                error="请在目标设置中选择你的 Obsidian 知识库目录。",
            )

        vault_root = Path(vault_str)
        if not vault_root.exists():
            return DeliveryResult(
                ok=False,
                mode="obsidian",
                summary="Obsidian Vault 目录不存在",
                error=f"找不到目录：{vault_root}",
            )

        subfolder = self.target.subfolder.strip().strip("/\\") or "WeChatInbox"
        target_dir = vault_root / subfolder
        target_dir.mkdir(parents=True, exist_ok=True)

        date_prefix = datetime.now().strftime("%Y-%m-%d")
        base_name = f"{date_prefix} {_sanitize_note_name(bundle.title)}"
        note_path = target_dir / f"{base_name}.md"
        idx = 1
        while note_path.exists():
            note_path = target_dir / f"{base_name} ({idx}).md"
            idx += 1

        # Copy attachments into vault assets folder
        copied_links: list[str] = []
        if bundle.attachments:
            assets_dir = target_dir / "assets" / bundle.bundle_id
            assets_dir.mkdir(parents=True, exist_ok=True)
            for att in bundle.attachments:
                src = Path(att.path)
                if not src.exists():
                    continue
                dest = assets_dir / src.name
                shutil.copy2(src, dest)
                rel = dest.relative_to(vault_root).as_posix()
                if att.kind == "image":
                    copied_links.append(f"![[{rel}]]")
                else:
                    copied_links.append(f"- [[{rel}]] (`{att.name}`)")

        participants_yaml = ", ".join(f'"{p.replace(chr(34), "")}"' for p in bundle.participants[:15])
        lines: list[str] = [
            "---",
            f'title: "{bundle.title.replace(chr(34), "")}"',
            f"created: {bundle.created_at}",
            "source: wechat",
            f"channel: {bundle.source}",
            f"message_count: {len(bundle.messages)}",
            f"batch_count: {bundle.batch_count}",
            f"participants: [{participants_yaml}]",
            "tags:",
            "  - wechat",
            "  - inbox",
            "---",
            "",
            f"# {bundle.title}",
            "",
        ]

        if prompt_instruction.strip():
            lines.extend(["> [!note] 记录备注 / 待分析方向", f"> {prompt_instruction.strip().replace(chr(10), chr(10) + '> ')}", ""])

        if copied_links:
            lines.append("## 附件与图片")
            lines.append("")
            lines.extend(copied_links)
            lines.append("")

        if bundle.urls:
            lines.append("## 提及链接")
            lines.append("")
            for u in bundle.urls[:30]:
                lines.append(f"- {u}")
            lines.append("")

        lines.append("## 聊天记录")
        lines.append("")
        if bundle.messages and not (len(bundle.messages) == 1 and bundle.messages[0].sender == "微信内容" and not bundle.messages[0].timestamp):
            for msg in bundle.messages:
                ts = f" `{msg.timestamp}`" if msg.timestamp else ""
                lines.append(f"### {msg.sender}{ts}")
                lines.append(msg.content)
                lines.append("")
        else:
            lines.append(bundle.plain_text or "（仅包含附件）")
            lines.append("")

        content = "\n".join(lines).strip() + "\n"
        note_path.write_text(content, encoding="utf-8")

        if on_chunk:
            on_chunk(f"[Obsidian] 已写入笔记：{note_path}\n")

        if self.target.open_after_save:
            try:
                rel_note = note_path.relative_to(vault_root).as_posix()
                uri = (
                    "obsidian://open?vault="
                    + urllib.parse.quote(vault_root.name)
                    + "&file="
                    + urllib.parse.quote(rel_note)
                )
                os.startfile(uri)  # type: ignore[attr-defined]
            except Exception:
                pass

        return DeliveryResult(
            ok=True,
            mode="obsidian",
            summary=f"已沉淀到 Obsidian：{note_path.name}（附 {len(copied_links)} 个本地附件）",
            output_text=content,
            output_path=str(note_path.resolve()),
        )

"""Desktop GUI handoff adapter for Antigravity, Codex Desktop, Grok Bot, or any Windows app."""

from __future__ import annotations

import threading
from pathlib import Path

from ..parser import ParsedBundle
from ..win_integration import (
    focus_or_launch_desktop_app,
    send_ctrl_v,
    set_clipboard_files,
    set_clipboard_text,
)
from .base import AgentAdapter, ChunkCallback, DeliveryResult, compose_agent_prompt


class DesktopHandoffAdapter(AgentAdapter):
    """Prepares bundle + clipboard payload and brings the target Desktop Agent window to front."""

    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        mode = self.target.clipboard_mode or "prompt_and_chat"
        include_body = mode != "prompt_with_path"
        prompt_text = compose_agent_prompt(
            bundle,
            prompt_instruction,
            max_inline_chars=self.max_inline_chars,
            include_chat_body=include_body,
        )

        bundle_dir = Path(bundle.bundle_dir)
        prompt_path = bundle_dir / "agent_prompt.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")

        if on_chunk:
            on_chunk(f"[{self.target.name}] 已生成上下文文件: {prompt_path}\n")

        if mode == "files_only":
            files_to_copy: list[str] = [bundle.markdown_path]
            for att in bundle.attachments[:15]:
                if Path(att.path).exists():
                    files_to_copy.append(att.path)
            clip_ok = set_clipboard_files(files_to_copy)
            clip_desc = f"已复制 {len(files_to_copy)} 个归档/附件文件到剪贴板"
        else:
            clip_ok = set_clipboard_text(prompt_text)
            clip_desc = (
                "已复制「Prompt + 完整聊天正文 + 附件路径」到剪贴板"
                if include_body
                else "已复制「Prompt + 归档文件路径指针」到剪贴板"
            )

        if not clip_ok:
            return DeliveryResult(
                ok=False,
                mode="gui_handoff",
                summary="写入 Windows 剪贴板失败",
                output_text=prompt_text,
                output_path=str(prompt_path),
                error="无法访问 Windows 剪贴板，请重试或手动打开归档文件。",
            )

        focused, focus_msg = focus_or_launch_desktop_app(
            process_name=self.target.process_name,
            title_hint=self.target.window_title_hint,
            executable=self.target.executable,
        )

        if focused and self.target.auto_paste:
            send_ctrl_v()
            paste_hint = "并已自动执行 Ctrl+V 粘贴"
        else:
            paste_hint = "请在对话框按 Ctrl+V 直接发送"

        summary = f"{clip_desc}；{focus_msg}，{paste_hint}。"
        if on_chunk:
            on_chunk(summary + "\n")

        return DeliveryResult(
            ok=True,
            mode="gui_handoff",
            summary=summary,
            output_text=prompt_text,
            output_path=str(prompt_path),
        )

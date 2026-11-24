"""HTTP Webhook & OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from ..config import RESULTS_DIR, ensure_data_dirs
from ..parser import ParsedBundle
from .base import AgentAdapter, ChunkCallback, DeliveryResult, compose_agent_prompt


class WebhookAdapter(AgentAdapter):
    """Sends WeChat bundle to a custom HTTP Webhook or OpenAI-compatible Chat API."""

    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        url = self.target.webhook_url.strip()
        if not url:
            return DeliveryResult(
                ok=False,
                mode="webhook",
                summary="未配置 Webhook / API 地址",
                error="请在目标设置中填写有效的 HTTP/HTTPS URL。",
            )

        prompt_text = compose_agent_prompt(bundle, prompt_instruction, max_inline_chars=self.max_inline_chars)
        is_openai_chat = url.rstrip("/").endswith("/chat/completions") or bool(self.target.api_model.strip())

        if is_openai_chat:
            payload = {
                "model": self.target.api_model.strip() or "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "你是一位高效的个人 AI 助手，擅长分析微信聊天记录与附件内容。"},
                    {"role": "user", "content": prompt_text},
                ],
                "stream": False,
            }
        else:
            payload = {
                "bundle_id": bundle.bundle_id,
                "title": bundle.title,
                "source": bundle.source,
                "created_at": bundle.created_at,
                "prompt": prompt_instruction,
                "composed_prompt": prompt_text,
                "markdown_path": bundle.markdown_path,
                "bundle_dir": bundle.bundle_dir,
                "participants": bundle.participants,
                "attachments": [a.__dict__ for a in bundle.attachments],
                "urls": bundle.urls,
            }

        headers = {"Content-Type": "application/json; charset=utf-8"}
        headers.update(self.target.webhook_headers or {})
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        if on_chunk:
            on_chunk(f"[Webhook] 正在请求 {url} ...\n")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        is_local = any(h in url.lower() for h in ("//127.0.0.1", "//localhost", "//[::1]"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if is_local else urllib.request.build_opener()
        try:
            with opener.open(req, timeout=self.timeout_sec) as resp:
                raw_resp = resp.read().decode("utf-8", errors="replace")
                status_code = resp.status
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:1000]
            return DeliveryResult(
                ok=False,
                mode="webhook",
                summary=f"HTTP 错误 {exc.code}",
                error=f"HTTP {exc.code}: {err_body}",
            )
        except Exception as exc:
            return DeliveryResult(
                ok=False,
                mode="webhook",
                summary="Webhook 请求失败",
                error=str(exc),
            )

        output_text = raw_resp
        try:
            parsed = json.loads(raw_resp)
            if isinstance(parsed, dict) and "choices" in parsed:
                choices = parsed.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message") or {}
                    if "content" in msg:
                        output_text = str(msg["content"])
            elif isinstance(parsed, dict) and "reply" in parsed:
                output_text = str(parsed["reply"])
            elif isinstance(parsed, dict) and "text" in parsed:
                output_text = str(parsed["text"])
        except Exception:
            pass

        if on_chunk and output_text:
            on_chunk(output_text + "\n")

        ensure_data_dirs()
        out_path = RESULTS_DIR / f"{bundle.bundle_id}_{self.target.id}_{datetime.now().strftime('%H%M%S')}.md"
        out_path.write_text(output_text, encoding="utf-8")

        return DeliveryResult(
            ok=200 <= status_code < 300,
            mode="webhook",
            summary=f"HTTP {status_code} 请求完成",
            output_text=output_text,
            output_path=str(out_path.resolve()),
        )

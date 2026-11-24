"""Agent adapter factory."""

from __future__ import annotations

from ..config import TargetConfig
from .base import AgentAdapter, DeliveryResult, compose_agent_prompt
from .cli_agents import ClaudeCodeAdapter, CodexCLIAdapter, CustomCLIAdapter, OpenClawAdapter
from .gui_agents import DesktopHandoffAdapter
from .obsidian_adapter import ObsidianAdapter
from .webhook_adapter import WebhookAdapter


def create_adapter(
    target: TargetConfig,
    *,
    timeout_sec: int = 240,
    max_inline_chars: int = 28000,
) -> AgentAdapter:
    kwargs = {"timeout_sec": timeout_sec, "max_inline_chars": max_inline_chars}
    if target.kind == "codex_cli":
        return CodexCLIAdapter(target, **kwargs)
    if target.kind == "claude_cli":
        return ClaudeCodeAdapter(target, **kwargs)
    if target.kind == "openclaw_cli":
        return OpenClawAdapter(target, **kwargs)
    if target.kind == "custom_cli":
        return CustomCLIAdapter(target, **kwargs)
    if target.kind == "obsidian":
        return ObsidianAdapter(target, **kwargs)
    if target.kind == "webhook":
        return WebhookAdapter(target, **kwargs)
    # Default: Desktop GUI handoff (antigravity_gui, codex_gui, custom_gui)
    return DesktopHandoffAdapter(target, **kwargs)


__all__ = ["AgentAdapter", "DeliveryResult", "compose_agent_prompt", "create_adapter"]

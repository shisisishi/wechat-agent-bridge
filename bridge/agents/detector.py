"""Auto-detection of installed local AI Agents, Obsidian vaults, and Windows WeChat version."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import winreg
from dataclasses import dataclass
from pathlib import Path

from ..config import TargetConfig


@dataclass
class WeChatEnvironment:
    installed: bool = False
    version: str = ""
    install_path: str = ""
    supports_native_share: bool = False


def _parse_version_tuple(version_str: str) -> tuple[int, ...]:
    nums = []
    for part in version_str.split("."):
        try:
            nums.append(int(part))
        except ValueError:
            break
    return tuple(nums)


def detect_wechat_environment() -> WeChatEnvironment:
    env = WeChatEnvironment()
    uninstall_roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in uninstall_roots:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                count = winreg.QueryInfoKey(root)[0]
                for i in range(count):
                    try:
                        name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, name) as item:
                            display_name = str(winreg.QueryValueEx(item, "DisplayName")[0])
                            if any(k in display_name for k in ("微信", "WeChat", "Weixin")):
                                env.installed = True
                                try:
                                    env.version = str(winreg.QueryValueEx(item, "DisplayVersion")[0])
                                except OSError:
                                    pass
                                try:
                                    env.install_path = str(winreg.QueryValueEx(item, "InstallLocation")[0]).strip('"')
                                except OSError:
                                    pass
                                break
                    except OSError:
                        continue
        except OSError:
            continue
        if env.installed:
            break

    if not env.install_path:
        for reg_path in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
                    env.install_path = str(winreg.QueryValueEx(key, "InstallPath")[0]).strip('"')
                    env.installed = True
                    break
            except OSError:
                continue

    if env.version:
        vt = _parse_version_tuple(env.version)
        env.supports_native_share = vt >= (4, 1, 13)
    return env


def _find_cli_executable(names: list[str]) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    # Also scan common npm/local bin locations explicitly
    candidates_dirs = [
        Path(os.environ.get("APPDATA", "")) / "npm",
        Path("C:/Program Files/nodejs"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs",
        Path.home() / ".local" / "bin",
    ]
    for d in candidates_dirs:
        if not d.exists():
            continue
        for name in names:
            for ext in ("", ".exe", ".cmd", ".ps1"):
                p = d / f"{name}{ext}"
                if p.exists():
                    return str(p)
    return ""


def _find_codex_cli_and_aumid() -> tuple[str, str]:
    cli_path = _find_cli_executable(["codex.exe", "codex.cmd", "codex"])
    aumid = ""

    # Query OpenAI.Codex MSIX via Get-AppxPackage (WindowsApps directory itself blocks listing)
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-AppxPackage -Name 'OpenAI.Codex' | Select-Object -First 1 InstallLocation, PackageFamilyName | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (proc.stdout or "").strip()
        if out.startswith("{"):
            info = json.loads(out)
            install_loc = str(info.get("InstallLocation") or "").strip()
            pfn = str(info.get("PackageFamilyName") or "").strip()
            if install_loc and not cli_path:
                bundled = Path(install_loc) / "app" / "resources" / "codex.exe"
                if bundled.exists():
                    cli_path = str(bundled)
            if pfn:
                aumid = f"shell:AppsFolder\\{pfn}!App"
    except Exception:
        pass

    return cli_path, aumid


def discover_obsidian_vaults() -> list[dict[str, object]]:
    """Read Obsidian's obsidian.json registry to discover local vaults cleanly in UTF-8."""
    cfg_path = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    if not cfg_path.exists():
        return []
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        vaults = []
        for vid, vinfo in (raw.get("vaults") or {}).items():
            vp = Path(str(vinfo.get("path", "")))
            if vp.exists():
                vaults.append(
                    {
                        "id": vid,
                        "name": vp.name,
                        "path": str(vp),
                        "open": bool(vinfo.get("open", False)),
                        "ts": int(vinfo.get("ts", 0)),
                    }
                )
        vaults.sort(key=lambda v: (not v["open"], -int(v["ts"])))
        return vaults
    except Exception:
        return []


def detect_local_targets() -> list[TargetConfig]:
    """Build the initial list of detected local Agent targets on this Windows machine."""
    targets: list[TargetConfig] = []
    local_programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"

    # 1. Antigravity Desktop
    ag_exe = local_programs / "antigravity" / "Antigravity.exe"
    targets.append(
        TargetConfig(
            id="antigravity_gui",
            name="Antigravity 桌面端",
            kind="antigravity_gui",
            enabled=True,
            mode="gui_handoff",
            description="整理聊天记录与附件并置顶 Antigravity 窗口，按 Ctrl+V 直接开聊",
            executable=str(ag_exe) if ag_exe.exists() else "",
            process_name="Antigravity.exe",
            window_title_hint="Antigravity",
            clipboard_mode="prompt_and_chat",
        )
    )

    # 2. Codex CLI (Direct Execution)
    codex_cli, codex_aumid = _find_codex_cli_and_aumid()
    targets.append(
        TargetConfig(
            id="codex_cli",
            name="Codex CLI（直连执行）",
            kind="codex_cli",
            enabled=bool(codex_cli),
            mode="cli_exec",
            description="直接调用本机 codex exec 分析聊天记录和图片附件，实时流式输出并保存 Markdown",
            executable=codex_cli,
            extra_args=["--skip-git-repo-check"],
        )
    )

    # 3. Codex Desktop GUI Handoff
    targets.append(
        TargetConfig(
            id="codex_gui",
            name="Codex 桌面端",
            kind="codex_gui",
            enabled=bool(codex_aumid or codex_cli),
            mode="gui_handoff",
            description="打包上下文与 Prompt 到剪贴板，自动唤起本机 Codex 桌面窗口",
            executable=codex_aumid,
            process_name="Codex.exe",
            window_title_hint="Codex",
            clipboard_mode="prompt_and_chat",
        )
    )

    # 4. Claude Code CLI
    claude_cli = _find_cli_executable(["claude.cmd", "claude.exe", "claude.ps1", "claude"])
    targets.append(
        TargetConfig(
            id="claude_cli",
            name="Claude Code（直连执行）",
            kind="claude_cli",
            enabled=bool(claude_cli),
            mode="cli_exec",
            description="通过本机 claude -p 直连执行，自动挂载归档目录读取全部附件并输出纪要",
            executable=claude_cli,
        )
    )

    # 5. OpenClaw CLI
    openclaw_cli = _find_cli_executable(["openclaw.cmd", "openclaw.exe", "openclaw.ps1", "openclaw"])
    targets.append(
        TargetConfig(
            id="openclaw_cli",
            name="OpenClaw（直连执行）",
            kind="openclaw_cli",
            enabled=bool(openclaw_cli),
            mode="cli_exec",
            description="通过本机 openclaw agent --message-file 将聊天内容发给你的 OpenClaw Agent",
            executable=openclaw_cli,
        )
    )

    # 6. Obsidian Knowledge Vault
    vaults = discover_obsidian_vaults()
    primary_vault = str(vaults[0]["path"]) if vaults else ""
    targets.append(
        TargetConfig(
            id="obsidian_vault",
            name=f"Obsidian 知识库" + (f" ({vaults[0]['name']})" if vaults else ""),
            kind="obsidian",
            enabled=bool(primary_vault),
            mode="obsidian",
            description="将聊天记录与图片附件沉淀为带 YAML 属性的 Obsidian Markdown 笔记",
            vault_path=primary_vault,
            subfolder="WeChatInbox",
            open_after_save=True,
        )
    )

    # 7. Grok Bot Desktop (optional, if present on user's PC)
    grok_dir = local_programs / "Grok Bot"
    if grok_dir.exists():
        grok_exes = list(grok_dir.glob("*.exe"))
        grok_exe = str(grok_exes[0]) if grok_exes else ""
        targets.append(
            TargetConfig(
                id="grokbot_gui",
                name="Grok Bot 桌面端",
                kind="custom_gui",
                enabled=bool(grok_exe),
                mode="gui_handoff",
                description="整理上下文并唤起本机 Grok Bot 桌面端直接粘贴",
                executable=grok_exe,
                process_name=Path(grok_exe).name if grok_exe else "Grok Bot.exe",
                window_title_hint="Grok",
                clipboard_mode="prompt_and_chat",
            )
        )

    return targets

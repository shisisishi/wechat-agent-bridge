"""CLI & GUI entry point for WeChat Agent Bridge."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wechat-agent-bridge",
        description="将微信聊天记录与附件直接桥接到本机 AI Agent (Antigravity / Codex / Claude / OpenClaw / Obsidian)",
    )
    parser.add_argument(
        "--ingest",
        nargs="+",
        default=[],
        help="导入一个或多个文件/ZIP 归档并发送到运行中的 Bridge 窗口",
    )
    parser.add_argument(
        "--ingest-manifest",
        default="",
        help="导入由 Windows ShareTarget 助手生成的 manifest.json",
    )
    parser.add_argument(
        "--text",
        default="",
        help="直接导入一段聊天文本",
    )
    parser.add_argument(
        "--source",
        default="sendto",
        help="来源标识 (windows_share_target / sendto / cli / drop)",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="静默启动到系统托盘（保留悬浮球与全局快捷键）",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="仅在命令行打印本机检测到的微信版本、数据库目录、MCP 状态与 AI Agent 列表",
    )
    parser.add_argument(
        "--setup-mcp",
        choices=["claude", "codex", "antigravity", "all"],
        default="",
        help="一键将内置 wechat-cli MCP Server 注册到本机 AI Agent (claude / codex / antigravity / all)",
    )
    parser.add_argument(
        "--remove-mcp",
        choices=["claude", "codex", "antigravity", "all"],
        default="",
        help="从本机 AI Agent 移除 wechat-cli MCP Server 配置",
    )

    args = parser.parse_args(argv)

    if args.setup_mcp:
        from bridge.wechat_db_client import WeChatCLIService, install_mcp_client

        svc = WeChatCLIService(PROJECT_ROOT)
        svc.ensure_env_file()
        targets = ["claude", "codex", "antigravity"] if args.setup_mcp == "all" else [args.setup_mcp]
        rc = 0
        for cid in targets:
            ok, msg = install_mcp_client(cid, PROJECT_ROOT)
            print(f"[{'OK' if ok else 'ERR'}] {cid}: {msg}")
            if not ok:
                rc = 1
        return rc

    if args.remove_mcp:
        from bridge.wechat_db_client import uninstall_mcp_client

        targets = ["claude", "codex", "antigravity"] if args.remove_mcp == "all" else [args.remove_mcp]
        rc = 0
        for cid in targets:
            ok, msg = uninstall_mcp_client(cid)
            print(f"[{'OK' if ok else 'ERR'}] {cid}: {msg}")
            if not ok:
                rc = 1
        return rc

    if args.detect:
        from bridge.agents.detector import detect_local_targets, detect_wechat_environment
        from bridge.wechat_db_client import WeChatCLIService, detect_wechat_db_paths, get_mcp_clients_status
        from bridge.win_integration import get_share_target_status, is_sendto_installed

        w = detect_wechat_environment()
        db_info = detect_wechat_db_paths()
        svc = WeChatCLIService(PROJECT_ROOT)
        mcp_st = get_mcp_clients_status(PROJECT_ROOT)
        st = get_share_target_status()
        print(f"WeChat: installed={w.installed}, version={w.version}, native_share_ready={w.supports_native_share}")
        print(f"WeChat DB dir: {db_info.db_storage_dir or '(not detected)'} | self_wxid={db_info.self_wxid or '-'}")
        print(f"wechat-cli bundled: built={svc.is_built()}, url={svc.base_url}")
        print("MCP clients:")
        for cid, info in mcp_st.items():
            print(f"  [{'INSTALLED' if info['installed'] else 'NOT SET  '}] {cid:<12} -> {info['config_path']}")
        print(f"ShareTarget MSIX: installed={st['installed']}, developer_mode={st['developer_mode']}")
        print(f"Explorer SendTo: installed={is_sendto_installed()}")
        print("Detected local targets:")
        for t in detect_local_targets():
            state = "ENABLED " if t.enabled else "DISABLED"
            detail = t.executable or t.vault_path or "(window)"
            print(f"  [{state}] {t.id:<16} | {t.mode:<11} | {t.name} -> {detail}")
        return 0

    print("桌面端 GUI (WeChat Agent Bridge) 已移除；请使用浏览器访问 Web 界面: http://127.0.0.1:5200")
    print("可用 CLI 参数: --detect / --setup-mcp all / --remove-mcp all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Launch the isolated mobile to-do sync server + Cloudflare HTTPS tunnel together.

Double-click `启动_手机在线同步.bat` (or run `python -m bridge.start_mobile_tunnel`)
to start 127.0.0.1:5201 + Cloudflare HTTPS quick tunnel and print the magic mobile URL.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from .mobile_todo_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MOBILE_SYNC_STATE_PATH,
    create_mobile_server,
    load_or_create_mobile_state,
    update_public_url,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARED_BIN = PROJECT_ROOT / "third_party" / "bin" / "cloudflared.exe"
TRYCF_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def _is_port_healthy(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _resolve_cloudflared_bin() -> Path | None:
    env_bin = os.environ.get("CLOUDFLARED_BIN", "").strip()
    if env_bin and Path(env_bin).is_file():
        return Path(env_bin)
    if CLOUDFLARED_BIN.is_file():
        return CLOUDFLARED_BIN
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    return None


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="start-mobile-tunnel")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--rotate-token", action="store_true")
    parser.add_argument("--protocol", default="http2", choices=["http2", "quic", "auto"])
    args = parser.parse_args(argv)

    cf_bin = _resolve_cloudflared_bin()
    if cf_bin is None:
        print(
            "[ERROR] 未找到 cloudflared 可执行文件。\n"
            "请任选其一安装：\n"
            "  1) winget install Cloudflare.cloudflared\n"
            "  2) 从 https://github.com/cloudflare/cloudflared/releases 下载 cloudflared-windows-amd64.exe\n"
            f"     并重命名为 {CLOUDFLARED_BIN}",
            flush=True,
        )
        return 1

    state = load_or_create_mobile_state(rotate_token=args.rotate_token, port=args.port)
    token = str(state["token"])

    server = None
    if not _is_port_healthy(args.port):
        server = create_mobile_server(host=DEFAULT_HOST, port=args.port, token=token)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[OK] 本地手机待办同步服务已启动: http://{DEFAULT_HOST}:{args.port}（仅同步待办，不暴露微信聊天记录）", flush=True)
    else:
        print(f"[OK] 复用已运行的本地手机待办服务: http://{DEFAULT_HOST}:{args.port}", flush=True)

    cmd = [
        str(cf_bin),
        "tunnel",
        "--url",
        f"http://{DEFAULT_HOST}:{args.port}",
        "--protocol",
        args.protocol,
        "--no-autoupdate",
    ]
    print("[INFO] 正在建立 Cloudflare HTTPS 加密隧道（已开启休眠断线自动重建）...", flush=True)
    proc = None
    try:
        while True:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            found_url = ""
            need_restart = False
            assert proc.stdout is not None
            for line in proc.stdout:
                clean = line.rstrip()
                m = TRYCF_RE.search(clean)
                if m and not found_url:
                    found_url = m.group(0)
                    updated = update_public_url(found_url)
                    print("\n============================================================", flush=True)
                    print("  微信实时待办 · 手机在线同步网址已就绪（HTTPS 加密）", flush=True)
                    print("============================================================", flush=True)
                    print("  一键免密链接（直接发到手机打开，自动记住30天）：", flush=True)
                    print(f"  {updated['magic_url']}", flush=True)
                    print(f"\n  基础网址：{found_url}", flush=True)
                    print(f"  访问口令：{token}", flush=True)
                    print(f"  状态文件：{MOBILE_SYNC_STATE_PATH}", flush=True)
                    print("============================================================\n", flush=True)
                elif "Unauthorized: Tunnel not found" in clean:
                    print("[WARN] 检测到休眠/网络切换导致临时隧道过期，3秒后自动重建新隧道...", flush=True)
                    need_restart = True
                    break
                elif "ERR" in clean or "failed" in clean.lower():
                    print(f"[tunnel] {clean}", flush=True)

            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if not need_restart:
                print("[WARN] 隧道进程退出，5秒后自动重启...", flush=True)
                time.sleep(5)
            else:
                time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if server is not None:
            server.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

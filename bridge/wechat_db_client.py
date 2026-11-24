"""Direct local WeChat 4.x database backend client (powered by bundled wechat-cli),
incremental sync puller, chat statistics fetcher, and AI Agent MCP config helper.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .agents.detector import detect_wechat_environment
from .config import BUNDLES_DIR, DATA_DIR, LOGS_DIR, ensure_data_dirs
from .parser import ParsedBundle, build_bundle
from .storage import InboxStore

WATERMARKS_PATH = DATA_DIR / "wechat_db_watermarks.json"
DEFAULT_WECHAT_CLI_PORT = 5200


@dataclass
class WeChatDBPaths:
    wechat_exe: str = ""
    wechat_version: str = ""
    data_root: str = ""
    account_dir: str = ""
    db_storage_dir: str = ""
    self_wxid: str = ""
    all_accounts: list[str] = field(default_factory=list)


def _strip_wxid_suffix(folder_name: str) -> str:
    """Turn 'wxid_abc123_52c6' into 'wxid_abc123' while preserving custom IDs."""
    m = re.match(r"^(.+)_[0-9a-fA-F]{4}$", folder_name.strip())
    if m and m.group(1):
        return m.group(1)
    return folder_name.strip()


def _read_xwechat_ini_roots() -> list[Path]:
    """Read WeChat 4.x FileSavePath hints from %APPDATA%/Tencent/xwechat/config/*.ini."""
    roots: list[Path] = []
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return roots
    cfg_dir = Path(appdata) / "Tencent" / "xwechat" / "config"
    if not cfg_dir.is_dir():
        return roots
    for ini in sorted(cfg_dir.glob("*.ini")):
        try:
            raw = ini.read_text(encoding="utf-8", errors="ignore").strip().strip("\x00")
            if raw and len(raw) >= 2:
                p = Path(raw)
                if p.exists() and p not in roots:
                    roots.append(p)
        except Exception:
            continue
    return roots


def _read_registry_save_path() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin") as k:
            val, _ = winreg.QueryValueEx(k, "FileSavePath")
            if val and str(val).strip() and str(val).strip() != "MyDocument:":
                p = Path(str(val).strip())
                if p.exists():
                    return p
    except Exception:
        return None
    return None


def detect_wechat_db_paths() -> WeChatDBPaths:
    """Locate Windows WeChat 4.x installation and xwechat_files/*/db_storage directory (path check only)."""
    env = detect_wechat_environment()
    wx_exe = ""
    if env.install_path:
        p = Path(env.install_path)
        if p.is_file():
            wx_exe = str(p)
        elif (p / "Weixin.exe").is_file():
            wx_exe = str(p / "Weixin.exe")
        elif (p / "WeChat.exe").is_file():
            wx_exe = str(p / "WeChat.exe")
        else:
            wx_exe = env.install_path
    info = WeChatDBPaths(wechat_exe=wx_exe, wechat_version=env.version)

    candidate_bases: list[Path] = []
    for p in _read_xwechat_ini_roots():
        candidate_bases.append(p / "xwechat_files")
        candidate_bases.append(p)

    reg_p = _read_registry_save_path()
    if reg_p:
        candidate_bases.append(reg_p / "xwechat_files")
        candidate_bases.append(reg_p)

    home = Path.home()
    candidate_bases.extend(
        [
            home / "Documents" / "xwechat_files",
            Path("D:/xwechat_files"),
            Path("E:/xwechat_files"),
        ]
    )

    found_accounts: list[tuple[float, Path, Path]] = []  # (mtime, account_dir, db_storage)
    seen_db_dirs: set[str] = set()

    for base in candidate_bases:
        if not base.is_dir():
            continue
        try:
            for sub in base.iterdir():
                if not sub.is_dir():
                    continue
                if sub.name.lower() in ("all_users", "backup", "applet", "wmpf"):
                    continue
                db_dir = sub / "db_storage"
                if db_dir.is_dir():
                    key = str(db_dir.resolve()).lower()
                    if key in seen_db_dirs:
                        continue
                    seen_db_dirs.add(key)
                    try:
                        mtime = db_dir.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    found_accounts.append((mtime, sub, db_dir))
        except OSError:
            continue

    found_accounts.sort(key=lambda x: x[0], reverse=True)
    info.all_accounts = [str(db_dir) for _, _, db_dir in found_accounts]
    if found_accounts:
        _, acc_dir, db_dir = found_accounts[0]
        info.data_root = str(acc_dir.parent)
        info.account_dir = str(acc_dir)
        info.db_storage_dir = str(db_dir)
        info.self_wxid = _strip_wxid_suffix(acc_dir.name)
    return info


def find_node_executable() -> str:
    """Locate node.exe on Windows."""
    found = shutil.which("node")
    if found:
        return found
    for cand in (
        Path("C:/Program Files/nodejs/node.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe",
    ):
        if cand.is_file():
            return str(cand)
    return ""


def _parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        result[k.strip()] = v
    return result


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in values.items() if v is not None]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class WeChatDBClient:
    """HTTP client talking to local wechat-cli Web API (bypasses system VPN/HTTP proxies)."""

    def __init__(self, base_url: str = f"http://127.0.0.1:{DEFAULT_WECHAT_CLI_PORT}", auth_token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token.strip()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if json_body:
            h["Content-Type"] = "application/json"
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
        return h

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float = 15.0,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            cleaned = {k: str(v) for k, v in params.items() if v is not None and v != ""}
            if cleaned:
                url = f"{url}?{urllib.parse.urlencode(cleaned)}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers(body is not None))
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"HTTP {exc.code} {path}: {err_body or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接本地 wechat-cli 服务 ({self.base_url}): {exc.reason}") from exc

    def _request_text(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> str:
        url = f"{self.base_url}{path}"
        if params:
            cleaned = {k: str(v) for k, v in params.items() if v is not None and v != ""}
            if cleaned:
                url = f"{url}?{urllib.parse.urlencode(cleaned)}"
        req = urllib.request.Request(url, method="GET", headers=self._headers(False))
        with self._opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def is_alive(self, timeout: float = 1.5) -> bool:
        try:
            data = self._request_json("GET", "/api/health", timeout=timeout)
            return isinstance(data, dict) and data.get("status") == "ok"
        except Exception:
            return False

    def get_status(self, timeout: float = 5.0) -> dict[str, Any]:
        data = self._request_json("GET", "/api/status", timeout=timeout)
        return data if isinstance(data, dict) else {}

    def save_config(
        self,
        *,
        wechat_db_src_path: str = "",
        wechat_path: str = "",
        self_wxid: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if wechat_db_src_path:
            payload["wechatDbSrcPath"] = wechat_db_src_path
        if wechat_path:
            payload["wechatPath"] = wechat_path
        if self_wxid:
            payload["selfWxid"] = self_wxid
        return self._request_json("POST", "/api/save-config", body=payload, timeout=10.0)

    def extract_key(self, timeout: float = 180.0) -> dict[str, Any]:
        return self._request_json("POST", "/api/extract-key", body={}, timeout=timeout)

    def decrypt(self, src_path: str = "", timeout: float = 300.0) -> dict[str, Any]:
        body = {"src_path": src_path} if src_path else {}
        return self._request_json("POST", "/api/decrypt", body=body, timeout=timeout)

    def refresh(self, timeout: float = 180.0) -> dict[str, Any]:
        return self._request_json("POST", "/api/refresh", body={}, timeout=timeout)

    def list_sessions(self, *, keyword: str = "", limit: int = 150, offset: int = 0) -> list[dict[str, Any]]:
        data = self._request_json(
            "GET",
            "/api/sessions",
            params={"keyword": keyword, "limit": limit, "offset": offset},
            timeout=15.0,
        )
        return data if isinstance(data, list) else []

    def get_messages(
        self,
        talker_id: str,
        *,
        keyword: str = "",
        limit: int = 100,
        offset: int = 0,
        reverse: bool = True,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "talker_id": talker_id,
            "limit": limit,
            "offset": offset,
            "reverse": "true" if reverse else "false",
        }
        if keyword:
            params["keyword"] = keyword
        if start_time is not None:
            params["start_time"] = int(start_time)
        if end_time is not None:
            params["end_time"] = int(end_time)
        data = self._request_json("GET", "/api/messages", params=params, timeout=20.0)
        return data if isinstance(data, list) else []

    def search_messages(
        self,
        keyword: str,
        *,
        limit: int = 50,
        offset: int = 0,
        talker: str = "",
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"keyword": keyword, "limit": limit, "offset": offset}
        if talker:
            params["talker"] = talker
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        data = self._request_json("GET", "/api/search", params=params, timeout=25.0)
        return data if isinstance(data, list) else []

    def get_global_stats(self, timeout: float = 35.0) -> dict[str, Any]:
        data = self._request_json("GET", "/api/stats", timeout=timeout)
        return data if isinstance(data, dict) else {}

    def get_chat_stats(self, talker: str, timeout: float = 25.0) -> dict[str, Any]:
        data = self._request_json("GET", "/api/chat-stats", params={"talker": talker}, timeout=timeout)
        return data if isinstance(data, dict) else {}

    def get_year_report(self, year: int | None = None, timeout: float = 35.0) -> dict[str, Any]:
        params = {"year": year} if year else None
        data = self._request_json("GET", "/api/year-report", params=params, timeout=timeout)
        return data if isinstance(data, dict) else {}

    def export_chat(self, talker: str, fmt: str = "txt", timeout: float = 45.0) -> str:
        return self._request_text("/api/export", params={"talker": talker, "format": fmt}, timeout=timeout)


class WeChatCLIService:
    """Lifecycle manager for the bundled Node.js wechat-cli local server."""

    def __init__(self, project_root: Path, port: int = DEFAULT_WECHAT_CLI_PORT) -> None:
        self.project_root = project_root
        self.cli_dir = project_root / "third_party" / "wechat-cli"
        self.dist_entry = self.cli_dir / "dist" / "index.js"
        self.port = int(port)
        self._proc: subprocess.Popen[Any] | None = None
        self._log_fh: Any = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def is_built(self) -> bool:
        return self.dist_entry.is_file()

    def client(self) -> WeChatDBClient:
        env_vals = _parse_env_file(self.cli_dir / ".env")
        return WeChatDBClient(base_url=self.base_url, auth_token=env_vals.get("AUTH_TOKEN", ""))

    def ensure_env_file(self, *, auto_refresh_ms: int = 60000) -> Path:
        """Seed third_party/wechat-cli/.env with auto-detected paths while preserving user keys."""
        env_path = self.cli_dir / ".env"
        current = _parse_env_file(env_path)
        detected = detect_wechat_db_paths()

        if not current.get("WECHAT_DB_SRC_PATH") and detected.db_storage_dir:
            current["WECHAT_DB_SRC_PATH"] = detected.db_storage_dir
        if not current.get("WECHAT_PATH") and detected.wechat_exe:
            current["WECHAT_PATH"] = detected.wechat_exe
        if not current.get("SELF_WXID") and detected.self_wxid:
            current["SELF_WXID"] = detected.self_wxid
        current.setdefault("DATA_DIR", "decrypted")
        current.setdefault("PORT", str(DEFAULT_WECHAT_CLI_PORT))
        current.setdefault("AUTO_REFRESH_MS", str(auto_refresh_ms))

        self.cli_dir.mkdir(parents=True, exist_ok=True)
        _write_env_file(env_path, current)
        return env_path

    def start(self, wait_ready_sec: float = 7.0) -> tuple[bool, str]:
        if not self.is_built():
            return False, f"未找到编译产物：{self.dist_entry}（请先在 third_party/wechat-cli 运行 npm install && npm run build）"

        cli = self.client()
        if cli.is_alive(timeout=1.2):
            return True, f"本地直连服务已在运行 ({self.base_url})"

        node_exe = find_node_executable()
        if not node_exe:
            return False, "未找到 Node.js，请确认已安装 Node.js >= 18。"

        self.ensure_env_file()
        ensure_data_dirs()
        log_path = LOGS_DIR / "wechat_cli.log"

        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["HOST"] = "127.0.0.1"

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._close_log()
            self._log_fh = open(log_path, "a", encoding="utf-8")
            self._proc = subprocess.Popen(
                [node_exe, str(self.dist_entry), "web"],
                cwd=str(self.cli_dir),
                env=env,
                stdout=self._log_fh,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
        except Exception as exc:
            self._close_log()
            return False, f"启动 wechat-cli 失败: {exc}"

        deadline = time.monotonic() + wait_ready_sec
        while time.monotonic() < deadline:
            if cli.is_alive(timeout=0.8):
                return True, f"本地直连服务已启动 ({self.base_url})"
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self._proc = None
                return False, f"wechat-cli 提前退出 (exit={code})，请查看日志 {log_path}"
            time.sleep(0.25)

        return False, f"等待 wechat-cli 启动超时，请查看日志 {log_path}"

    def stop(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
            except Exception:
                pass
            self._proc = None
        self._close_log()

    def _close_log(self) -> None:
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None


# ── Incremental WeChat DB -> ParsedBundle Sync ─────────────────────────────


def _parse_iso_to_unix(iso_str: str) -> int:
    if not iso_str:
        return 0
    s = iso_str.strip()
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return int(datetime.strptime(s[:19], fmt).timestamp())
        except Exception:
            continue
    return 0


def _format_msg_time(iso_str: str) -> str:
    ts = _parse_iso_to_unix(iso_str)
    if ts > 0:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    return iso_str.replace("T", " ")[:19]


def session_display_name(session: dict[str, Any]) -> str:
    return (
        str(session.get("remark") or "").strip()
        or str(session.get("nickname") or "").strip()
        or str(session.get("alias") or "").strip()
        or str(session.get("username") or "未知会话").strip()
    )


def format_wechat_messages_as_text(messages: list[dict[str, Any]], session_title: str = "") -> str:
    """Convert wechat-cli message dicts into bridge's standard timestamped chat transcript."""
    lines: list[str] = []
    for m in messages:
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        ts_label = _format_msg_time(str(m.get("time") or ""))
        if m.get("isSelf"):
            sender = "我"
        else:
            sender = str(m.get("sender") or session_title or m.get("talker") or "对方").strip()
        # Collapse embedded newlines gracefully while keeping readability
        lines.append(f"{sender} {ts_label}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def build_bundle_from_wechat_messages(
    *,
    talker: str,
    session_name: str,
    messages: list[dict[str, Any]],
    bundles_root: Path = BUNDLES_DIR,
    source: str = "wechat_db",
) -> ParsedBundle:
    """Persist pulled wechat-cli messages as a first-class ParsedBundle."""
    transcript = format_wechat_messages_as_text(messages, session_title=session_name)
    now_tag = datetime.now().strftime("%m-%d %H:%M")
    title = f"[微信直连] {session_name} ({len(messages)}条 · {now_tag})"
    return build_bundle(
        bundles_root=bundles_root,
        source=source,
        title=title,
        text=transcript,
    )


def _load_watermarks(path: Path = WATERMARKS_PATH) -> dict[str, dict[str, int]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_watermarks(wm: dict[str, dict[str, int]], path: Path = WATERMARKS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wm, ensure_ascii=False, indent=2), encoding="utf-8")


def pull_incremental_wechat_bundles(
    client: WeChatDBClient,
    store: InboxStore,
    *,
    watch_sessions: list[str] | None = None,
    max_sessions: int = 8,
    lookback_hours: int = 24,
    do_refresh: bool = True,
    bundles_root: Path = BUNDLES_DIR,
    watermarks_path: Path = WATERMARKS_PATH,
    on_progress: Any = None,
) -> tuple[list[ParsedBundle], dict[str, Any]]:
    """Pull only new messages since last sync watermark from local wechat-cli and save as bundles."""
    meta: dict[str, Any] = {
        "connected": False,
        "has_data": False,
        "refreshed": False,
        "sessions_checked": 0,
        "messages_pulled": 0,
        "error": "",
    }

    def emit(msg: str) -> None:
        if callable(on_progress):
            on_progress(msg)

    if not client.is_alive(timeout=2.0):
        meta["error"] = "wechat-cli 本地服务未运行"
        return [], meta
    meta["connected"] = True

    try:
        status = client.get_status(timeout=5.0)
    except Exception as exc:
        meta["error"] = str(exc)
        return [], meta

    if not status.get("hasData") or int(status.get("dbCount") or 0) <= 0:
        meta["error"] = "wechat-cli 尚未完成数据库解密"
        return [], meta
    meta["has_data"] = True

    if do_refresh:
        try:
            emit("正在刷新本地微信最新聊天数据库...")
            r = client.refresh(timeout=120.0)
            meta["refreshed"] = bool(r.get("success", True))
        except Exception:
            meta["refreshed"] = False

    try:
        sessions = client.list_sessions(limit=200)
    except Exception as exc:
        meta["error"] = f"获取会话列表失败: {exc}"
        return [], meta

    watch_set = {s.strip() for s in (watch_sessions or []) if str(s).strip()}
    if watch_set:
        selected = [s for s in sessions if str(s.get("username") or "") in watch_set]
    else:
        normal = [
            s
            for s in sessions
            if str(s.get("accountType") or "normal") == "normal"
            and not bool(s.get("isHidden", False))
        ]
        selected = normal[: max(1, int(max_sessions))]

    watermarks = _load_watermarks(watermarks_path)
    dirty_wm = False
    now_ts = int(time.time())
    default_since = now_ts - max(1, int(lookback_hours)) * 3600

    created: list[ParsedBundle] = []
    for sess in selected:
        talker = str(sess.get("username") or "").strip()
        if not talker:
            continue
        meta["sessions_checked"] += 1
        wm = watermarks.get(talker) or {}
        last_ts = int(wm.get("last_ts") or 0)
        last_seq = int(wm.get("last_seq") or 0)

        # Quick skip if session's lastTime is older than our watermark
        sess_last_ts = _parse_iso_to_unix(str(sess.get("lastTime") or ""))
        if last_ts > 0 and sess_last_ts > 0 and sess_last_ts <= last_ts:
            continue

        since_ts = (last_ts + 1) if last_ts > 0 else default_since
        try:
            msgs = client.get_messages(
                talker,
                limit=200,
                reverse=False,
                start_time=since_ts,
            )
        except Exception:
            continue

        # Filter out already-seen sequences & pure system noise (10000)
        fresh: list[dict[str, Any]] = []
        max_seq = last_seq
        max_ts = last_ts
        for m in msgs:
            seq = int(m.get("seq") or 0)
            m_ts = _parse_iso_to_unix(str(m.get("time") or ""))
            if last_seq > 0 and seq > 0 and seq <= last_seq:
                continue
            if seq > max_seq:
                max_seq = seq
            if m_ts > max_ts:
                max_ts = m_ts
            m_type = int(m.get("type") or 1)
            content = str(m.get("content") or "").strip()
            if m_type == 10000 or not content:
                continue
            fresh.append(m)

        if max_ts > last_ts or max_seq > last_seq:
            watermarks[talker] = {"last_ts": max(max_ts, now_ts if fresh else last_ts), "last_seq": max_seq}
            dirty_wm = True

        if not fresh:
            continue

        name = session_display_name(sess)
        bundle = build_bundle_from_wechat_messages(
            talker=talker,
            session_name=name,
            messages=fresh,
            bundles_root=bundles_root,
            source="wechat_db",
        )
        store.save_bundle(bundle, queued_for_merge=False)
        created.append(bundle)
        meta["messages_pulled"] += len(fresh)

    if dirty_wm:
        _save_watermarks(watermarks, watermarks_path)

    return created, meta


# ── AI Agent MCP Configuration Helpers (Claude Code / Codex / Antigravity) ─


def build_wechat_mcp_server_spec(project_root: Path) -> dict[str, Any]:
    """Return canonical stdio MCP server specification for bundled wechat-cli."""
    cli_dir = (project_root / "third_party" / "wechat-cli").resolve()
    entry = (cli_dir / "dist" / "index.js").resolve()
    node_exe = find_node_executable() or "node"
    env_file = _parse_env_file(cli_dir / ".env")
    detected = detect_wechat_db_paths()

    data_dir = env_file.get("DATA_DIR") or "decrypted"
    abs_data_dir = str((cli_dir / data_dir).resolve()) if not Path(data_dir).is_absolute() else data_dir
    env_map: dict[str, str] = {"DATA_DIR": abs_data_dir}
    src_path = env_file.get("WECHAT_DB_SRC_PATH") or detected.db_storage_dir
    if src_path:
        env_map["WECHAT_DB_SRC_PATH"] = src_path
    wx_path = env_file.get("WECHAT_PATH") or detected.wechat_exe
    if wx_path:
        env_map["WECHAT_PATH"] = wx_path
    self_wxid = env_file.get("SELF_WXID") or detected.self_wxid
    if self_wxid:
        env_map["SELF_WXID"] = self_wxid

    return {
        "command": node_exe,
        "args": [str(entry)],
        "env": env_map,
    }


def _default_mcp_paths() -> dict[str, Path]:
    home = Path.home()
    return {
        "claude": home / ".claude.json",
        "codex": home / ".codex" / "config.toml",
        "antigravity": home / ".gemini" / "config" / "mcp_config.json",
    }


def _backup_if_exists(path: Path) -> None:
    if path.is_file() and path.stat().st_size > 0:
        bak = path.with_suffix(path.suffix + ".wechat_bridge.bak")
        if not bak.exists():
            shutil.copy2(path, bak)


def _format_toml_value(val: Any) -> str:
    if isinstance(val, list):
        inner = ", ".join(json.dumps(str(x), ensure_ascii=False) for x in val)
        return f"[{inner}]"
    return json.dumps(str(val), ensure_ascii=False)


def _remove_toml_section(text: str, section_header: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    target = f"[{section_header}]"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if stripped == target:
                skipping = True
                continue
            skipping = False
        if not skipping:
            out.append(line)
    # Trim trailing excess blank lines
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


def get_mcp_clients_status(
    project_root: Path,
    *,
    path_overrides: dict[str, Path] | None = None,
) -> dict[str, dict[str, Any]]:
    """Inspect whether 'wechat-cli' MCP server is configured for Claude Code, Codex, and Antigravity."""
    paths = _default_mcp_paths()
    if path_overrides:
        paths.update(path_overrides)

    result: dict[str, dict[str, Any]] = {}
    for key, label in (
        ("claude", "Claude Code CLI"),
        ("codex", "Codex CLI / Desktop"),
        ("antigravity", "Antigravity"),
    ):
        p = paths[key]
        installed = False
        if p.is_file():
            try:
                raw = p.read_text(encoding="utf-8", errors="ignore").strip()
                if key == "codex":
                    installed = "[mcp_servers.wechat-cli]" in raw
                elif raw:
                    parsed = json.loads(raw)
                    servers = parsed.get("mcpServers") if isinstance(parsed, dict) else None
                    installed = isinstance(servers, dict) and "wechat-cli" in servers
            except Exception:
                installed = False
        result[key] = {
            "id": key,
            "name": label,
            "config_path": str(p),
            "file_exists": p.is_file(),
            "installed": installed,
        }
    return result


def install_mcp_client(
    client_id: str,
    project_root: Path,
    *,
    path_overrides: dict[str, Path] | None = None,
) -> tuple[bool, str]:
    """Safely register 'wechat-cli' MCP server into Claude Code, Codex, or Antigravity config."""
    paths = _default_mcp_paths()
    if path_overrides:
        paths.update(path_overrides)
    if client_id not in paths:
        return False, f"不支持的客户端：{client_id}"

    spec = build_wechat_mcp_server_spec(project_root)
    target = paths[client_id]
    target.parent.mkdir(parents=True, exist_ok=True)
    _backup_if_exists(target)

    try:
        if client_id == "codex":
            existing = target.read_text(encoding="utf-8") if target.is_file() else ""
            cleaned = _remove_toml_section(existing, "mcp_servers.wechat-cli")
            cleaned = _remove_toml_section(cleaned, "mcp_servers.wechat-cli.env")
            block_lines = [
                "",
                "[mcp_servers.wechat-cli]",
                f"command = {_format_toml_value(spec['command'])}",
                f"args = {_format_toml_value(spec['args'])}",
                "",
                "[mcp_servers.wechat-cli.env]",
            ]
            for ek, ev in spec["env"].items():
                block_lines.append(f"{ek} = {_format_toml_value(ev)}")
            new_text = cleaned.rstrip() + ("\n" if cleaned.strip() else "") + "\n".join(block_lines).lstrip("\n" if not cleaned.strip() else "") + "\n"
            target.write_text(new_text, encoding="utf-8")
            return True, f"已写入 Codex MCP 配置：{target}"

        # JSON clients: claude & antigravity
        data: dict[str, Any] = {}
        if target.is_file():
            raw = target.read_text(encoding="utf-8").strip()
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    data = loaded
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            data["mcpServers"] = servers
        servers["wechat-cli"] = spec
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, f"已写入 {client_id} MCP 配置：{target}"
    except Exception as exc:
        return False, f"写入 MCP 配置失败 ({target}): {exc}"


def uninstall_mcp_client(
    client_id: str,
    *,
    path_overrides: dict[str, Path] | None = None,
) -> tuple[bool, str]:
    """Remove 'wechat-cli' MCP entry without touching other user settings."""
    paths = _default_mcp_paths()
    if path_overrides:
        paths.update(path_overrides)
    if client_id not in paths:
        return False, f"不支持的客户端：{client_id}"

    target = paths[client_id]
    if not target.is_file():
        return True, "配置文件不存在，无需移除。"
    try:
        if client_id == "codex":
            existing = target.read_text(encoding="utf-8")
            cleaned = _remove_toml_section(existing, "mcp_servers.wechat-cli")
            cleaned = _remove_toml_section(cleaned, "mcp_servers.wechat-cli.env")
            target.write_text(cleaned, encoding="utf-8")
            return True, f"已从 Codex 移除 wechat-cli MCP：{target}"

        raw = target.read_text(encoding="utf-8").strip()
        if not raw:
            return True, "配置文件为空。"
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
            data["mcpServers"].pop("wechat-cli", None)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, f"已从 {client_id} 移除 wechat-cli MCP：{target}"
    except Exception as exc:
        return False, f"移除失败：{exc}"

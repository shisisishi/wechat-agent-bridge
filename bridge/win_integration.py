"""Windows OS integration: clipboard (text/files/images), window focus, SendTo, ShareTarget status."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct
import subprocess
import tempfile
import time
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    import win32clipboard
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover
    win32clipboard = None  # type: ignore
    win32con = None  # type: ignore
    win32gui = None  # type: ignore
    win32process = None  # type: ignore

try:
    from PIL import ImageGrab
except ImportError:  # pragma: no cover
    ImageGrab = None  # type: ignore


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
SW_SHOW = 5
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

SHARE_TARGET_PACKAGE_NAME = "WeChatAgentBridge.ShareTarget"
SENDTO_SHORTCUT_NAME = "发送到 WeChat Agent Bridge.lnk"


@dataclass
class ClipboardPayload:
    text: str = ""
    html: str = ""
    files: list[str] = field(default_factory=list)
    image_path: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.text.strip() or self.html.strip() or self.files or self.image_path)


def set_clipboard_text(text: str) -> bool:
    if win32clipboard is None:
        return False
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.05)
    return False


def set_clipboard_files(file_paths: Iterable[str | Path]) -> bool:
    """Place one or more existing file paths onto the Windows clipboard as CF_HDROP."""
    if win32clipboard is None:
        return False
    existing = [str(Path(p).resolve()) for p in file_paths if Path(p).exists()]
    if not existing:
        return False

    # DROPFILES struct: pFiles (DWORD=20), pt (LONG x, LONG y), fNC (BOOL), fWide (BOOL=1)
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    files_blob = ("\0".join(existing) + "\0\0").encode("utf-16le")
    payload = header + files_blob

    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, payload)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.05)
    return False


def read_clipboard_payload(temp_dir: Path | None = None) -> ClipboardPayload:
    """Capture files, text, HTML, or screenshot image currently on the Windows clipboard."""
    payload = ClipboardPayload()
    if win32clipboard is None:
        return payload

    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                dropped = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                payload.files = [str(p) for p in dropped if Path(p).exists()]

            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                txt = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if isinstance(txt, str):
                    payload.text = txt

            cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
            if win32clipboard.IsClipboardFormatAvailable(cf_html):
                raw_html = win32clipboard.GetClipboardData(cf_html)
                if isinstance(raw_html, bytes):
                    payload.html = raw_html.decode("utf-8", errors="replace")
                elif isinstance(raw_html, str):
                    payload.html = raw_html
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass

    # If no files and no text, check if an image bitmap is on the clipboard (e.g. WeChat screenshot)
    if not payload.files and not payload.text.strip() and ImageGrab is not None:
        try:
            grabbed = ImageGrab.grabclipboard()
            if grabbed is not None and not isinstance(grabbed, list):
                save_root = temp_dir or Path(tempfile.gettempdir())
                save_root.mkdir(parents=True, exist_ok=True)
                img_path = save_root / f"wechat_clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                grabbed.save(img_path, format="PNG")
                payload.image_path = str(img_path.resolve())
                payload.files.append(payload.image_path)
        except Exception:
            pass

    return payload


def _get_process_image_name(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _find_window_for_app(process_name: str = "", title_hint: str = "") -> int | None:
    if win32gui is None or win32process is None:
        return None
    proc_target = process_name.lower().strip()
    if proc_target and not proc_target.endswith(".exe"):
        proc_target += ".exe"
    title_target = title_hint.lower().strip()

    matches: list[tuple[int, int]] = []  # (score, hwnd)

    def enum_cb(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        if not title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        img = _get_process_image_name(pid)
        score = 0
        if proc_target and img == proc_target:
            score += 10
        if title_target and title_target in title.lower():
            score += 5
        if score > 0:
            matches.append((score, hwnd))
        return True

    win32gui.EnumWindows(enum_cb, None)
    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return matches[0][1]


def bring_window_to_front(hwnd: int) -> bool:
    if win32gui is None:
        return False
    try:
        user32 = ctypes.windll.user32
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, SW_SHOW)

        fg_hwnd = win32gui.GetForegroundWindow()
        cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
        target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]

        attached_fg = False
        attached_target = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached_fg = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
            if target_tid and target_tid != cur_tid:
                attached_target = bool(user32.AttachThreadInput(target_tid, cur_tid, True))
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            return True
        finally:
            if attached_target:
                user32.AttachThreadInput(target_tid, cur_tid, False)
            if attached_fg:
                user32.AttachThreadInput(fg_tid, cur_tid, False)
    except Exception:
        return False


def send_ctrl_v() -> None:
    user32 = ctypes.windll.user32
    time.sleep(0.18)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def focus_or_launch_desktop_app(
    *,
    process_name: str = "",
    title_hint: str = "",
    executable: str = "",
) -> tuple[bool, str]:
    """Activate a running desktop app window, or launch it if not running."""
    hwnd = _find_window_for_app(process_name=process_name, title_hint=title_hint)
    if hwnd:
        bring_window_to_front(hwnd)
        return True, "已置顶已打开的桌面窗口"

    exe = executable.strip()
    if not exe:
        return False, "未找到运行中的目标窗口，且未配置启动路径"

    try:
        if exe.lower().startswith("shell:appsfolder\\"):
            subprocess.Popen(["explorer.exe", exe])
            return True, f"已拉起桌面应用 ({exe})"
        if Path(exe).exists():
            subprocess.Popen([exe], cwd=str(Path(exe).parent))
            return True, f"已启动桌面应用 ({Path(exe).name})"
        # Fallback: try shell start (e.g. URI scheme)
        os.startfile(exe)  # type: ignore[attr-defined]
        return True, f"已调用系统启动 ({exe})"
    except Exception as exc:
        return False, f"拉起应用失败: {exc}"


def open_folder_in_explorer(path: str | Path) -> None:
    p = Path(path)
    if p.is_file():
        subprocess.Popen(["explorer.exe", "/select,", str(p.resolve())])
    elif p.exists():
        subprocess.Popen(["explorer.exe", str(p.resolve())])


def open_file_default(path: str | Path) -> None:
    p = Path(path)
    if p.exists():
        os.startfile(str(p.resolve()))  # type: ignore[attr-defined]


def get_sendto_shortcut_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "SendTo" / SENDTO_SHORTCUT_NAME


def is_sendto_installed() -> bool:
    return get_sendto_shortcut_path().exists()


def install_sendto_shortcut(project_root: Path, python_exe: str) -> tuple[bool, str]:
    """Install a Windows Explorer right-click 'Send To' shortcut (no Admin required)."""
    try:
        import win32com.client

        lnk_path = get_sendto_shortcut_path()
        lnk_path.parent.mkdir(parents=True, exist_ok=True)
        pythonw = Path(python_exe).with_name("pythonw.exe")
        target_exe = str(pythonw if pythonw.exists() else Path(python_exe))
        script = str((project_root / "run.py").resolve())

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(lnk_path))
        shortcut.TargetPath = target_exe
        shortcut.Arguments = f'"{script}" --ingest'
        shortcut.WorkingDirectory = str(project_root.resolve())
        shortcut.Description = "转发文件或微信归档到 WeChat Agent Bridge"
        shortcut.Save()
        return True, f"已添加到 Windows 右键「发送到」菜单：{lnk_path}"
    except Exception as exc:
        return False, f"安装右键「发送到」失败：{exc}"


def remove_sendto_shortcut() -> tuple[bool, str]:
    lnk_path = get_sendto_shortcut_path()
    try:
        if lnk_path.exists():
            lnk_path.unlink()
        return True, "已移除 Windows 右键「发送到」快捷方式"
    except Exception as exc:
        return False, f"移除失败：{exc}"


def is_developer_mode_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")
            return bool(val)
    except Exception:
        return False


def get_share_target_status() -> dict[str, object]:
    """Check whether our native Windows ShareTarget MSIX package is registered."""
    installed = False
    version = ""
    family = ""
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Get-AppxPackage -Name '{SHARE_TARGET_PACKAGE_NAME}' | Select-Object Version, PackageFamilyName | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (proc.stdout or "").strip()
        if out and out.startswith("{"):
            import json

            info = json.loads(out)
            installed = True
            version = str(info.get("Version", ""))
            family = str(info.get("PackageFamilyName", ""))
    except Exception:
        pass
    return {
        "installed": installed,
        "version": version,
        "package_family": family,
        "developer_mode": is_developer_mode_enabled(),
    }


def parse_hotkey_string(hotkey: str) -> tuple[int, int] | None:
    """Convert 'Ctrl+Alt+W' into Win32 (modifiers, vk_code) for RegisterHotKey."""
    mod_map = {
        "ALT": 0x0001,
        "CTRL": 0x0002,
        "CONTROL": 0x0002,
        "SHIFT": 0x0004,
        "WIN": 0x0008,
    }
    special_vks = {
        "SPACE": 0x20,
        "ENTER": 0x0D,
        "TAB": 0x09,
        "ESC": 0x1B,
        "INSERT": 0x2D,
    }
    for i in range(1, 13):
        special_vks[f"F{i}"] = 0x6F + i

    parts = [p.strip().upper() for p in hotkey.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    key_part = parts[-1]
    for p in parts[:-1]:
        if p in mod_map:
            mods |= mod_map[p]
        else:
            return None
    if mods == 0:
        return None
    if key_part in special_vks:
        vk = special_vks[key_part]
    elif len(key_part) == 1 and ("A" <= key_part <= "Z" or "0" <= key_part <= "9"):
        vk = ord(key_part)
    else:
        return None
    # MOD_NOREPEAT = 0x4000
    return mods | 0x4000, vk

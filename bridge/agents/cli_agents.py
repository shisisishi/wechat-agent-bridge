"""Direct CLI Agent adapters (Codex CLI, Claude Code, OpenClaw, Custom CLI) with live streaming."""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from ..config import RESULTS_DIR, ensure_data_dirs
from ..parser import ParsedBundle
from .base import AgentAdapter, ChunkCallback, DeliveryResult, compose_agent_prompt


def _build_windows_command(executable: str, args: list[str]) -> list[str]:
    """Safely wrap .ps1 or .cmd/.bat launchers on Windows without shell-string injection."""
    ext = Path(executable).suffix.lower()
    if ext == ".ps1":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            executable,
            *args,
        ]
    if ext in (".cmd", ".bat"):
        # Prefer sibling .ps1 if available? No, cmd /d /s /c with list2cmdline works reliably for npm shims
        return ["cmd.exe", "/d", "/c", executable, *args]
    return [executable, *args]


def _save_result_markdown(bundle: ParsedBundle, target_id: str, target_name: str, output_text: str) -> str:
    ensure_data_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{bundle.bundle_id}_{target_id}_{stamp}.md"
    header = (
        f"# {bundle.title} — {target_name} 分析结果\n\n"
        f"- **原始归档**: `{bundle.markdown_path}`\n"
        f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "---\n\n"
    )
    out_path.write_text(header + output_text.strip() + "\n", encoding="utf-8")
    return str(out_path.resolve())


def _run_streaming_process(
    cmd: list[str],
    *,
    cwd: str,
    stdin_text: str | None,
    timeout_sec: int,
    on_chunk: ChunkCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str, str, bool]:
    """Execute a subprocess with live UTF-8 stdout/stderr streaming, timeout, and cancellation."""
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("NO_COLOR", "1")

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    if stdin_text is not None and proc.stdin is not None:
        def _feed_stdin() -> None:
            try:
                proc.stdin.write(stdin_text.encode("utf-8"))  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()  # type: ignore[union-attr]
                except Exception:
                    pass

        threading.Thread(target=_feed_stdin, daemon=True).start()

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def _drain(stream, bucket: list[str], emit: bool) -> None:
        try:
            while True:
                line_bytes = stream.readline()
                if not line_bytes:
                    break
                text = line_bytes.decode("utf-8", errors="replace")
                bucket.append(text)
                if emit and on_chunk:
                    on_chunk(text)
        except Exception:
            pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_parts, True), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_parts, False), daemon=True)
    t_out.start()
    t_err.start()

    start_time = time.monotonic()
    cancelled = False
    timed_out = False

    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            _terminate_tree(proc)
            break
        if time.monotonic() - start_time > timeout_sec:
            timed_out = True
            _terminate_tree(proc)
            break
        time.sleep(0.1)

    t_out.join(timeout=2)
    t_err.join(timeout=2)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    rc = proc.returncode if proc.returncode is not None else -1
    out_text = "".join(stdout_parts)
    err_text = "".join(stderr_parts)
    if cancelled:
        err_text = "用户取消了执行。"
    elif timed_out:
        err_text = f"执行超时（超过 {timeout_sec} 秒）。\n{err_text}".strip()
    return rc, out_text, err_text, cancelled or timed_out


def _terminate_tree(proc: subprocess.Popen) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class CodexCLIAdapter(AgentAdapter):
    """Runs local `codex exec` with stdin prompt, native image attachments, and clean output file."""

    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        exe = self.target.executable.strip()
        if not exe or not Path(exe).exists():
            return DeliveryResult(
                ok=False,
                mode="cli_exec",
                summary="未检测到 Codex CLI 可执行文件",
                error="请在设置中配置正确的 codex.exe 路径。",
            )

        prompt_text = compose_agent_prompt(bundle, prompt_instruction, max_inline_chars=self.max_inline_chars)
        bundle_dir = Path(bundle.bundle_dir)
        last_msg_path = bundle_dir / f"codex_last_{datetime.now().strftime('%H%M%S')}.md"

        args = ["exec", "--skip-git-repo-check", "-C", str(bundle_dir), "-o", str(last_msg_path)]
        # Attach up to 5 image files natively via `-i`
        img_atts = [a.path for a in bundle.attachments if a.kind == "image" and Path(a.path).exists()][:5]
        for img in img_atts:
            args.extend(["-i", img])
        for extra in self.target.extra_args:
            if extra not in args:
                args.append(extra)
        args.append("-")

        if on_chunk:
            on_chunk(f"[Codex CLI] 启动执行（附件图片 {len(img_atts)} 张）...\n")

        cmd = _build_windows_command(exe, args)
        cwd = self.target.working_dir.strip() or str(bundle_dir)
        rc, stdout_text, stderr_text, aborted = _run_streaming_process(
            cmd,
            cwd=cwd,
            stdin_text=prompt_text,
            timeout_sec=self.timeout_sec,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )

        final_output = ""
        if last_msg_path.exists():
            final_output = last_msg_path.read_text(encoding="utf-8", errors="replace").strip()
        if not final_output:
            final_output = stdout_text.strip()

        if rc == 0 and not aborted and final_output:
            saved = _save_result_markdown(bundle, self.target.id, self.target.name, final_output)
            return DeliveryResult(
                ok=True,
                mode="cli_exec",
                summary=f"Codex CLI 执行完成，结果已保存至 {Path(saved).name}",
                output_text=final_output,
                output_path=saved,
            )

        err_msg = stderr_text.strip() or stdout_text.strip() or f"Codex CLI 退出码 {rc}"
        return DeliveryResult(
            ok=False,
            mode="cli_exec",
            summary=f"Codex CLI 执行失败 (code={rc})",
            output_text=final_output,
            error=err_msg,
        )


class ClaudeCodeAdapter(AgentAdapter):
    """Runs local `claude -p --add-dir <bundle_dir>` piping full UTF-8 prompt via stdin."""

    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        exe = self.target.executable.strip()
        if not exe or not Path(exe).exists():
            return DeliveryResult(
                ok=False,
                mode="cli_exec",
                summary="未检测到 Claude Code CLI",
                error="请确认已安装 claude CLI 并在设置中配置路径。",
            )

        prompt_text = compose_agent_prompt(bundle, prompt_instruction, max_inline_chars=self.max_inline_chars)
        bundle_dir = Path(bundle.bundle_dir)
        args = ["-p", "--add-dir", str(bundle_dir), *self.target.extra_args]

        if on_chunk:
            on_chunk("[Claude Code] 启动执行...\n")

        cmd = _build_windows_command(exe, args)
        cwd = self.target.working_dir.strip() or str(bundle_dir)
        rc, stdout_text, stderr_text, aborted = _run_streaming_process(
            cmd,
            cwd=cwd,
            stdin_text=prompt_text,
            timeout_sec=self.timeout_sec,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )

        final_output = stdout_text.strip()
        if rc == 0 and not aborted and final_output:
            saved = _save_result_markdown(bundle, self.target.id, self.target.name, final_output)
            return DeliveryResult(
                ok=True,
                mode="cli_exec",
                summary=f"Claude Code 执行完成，已保存至 {Path(saved).name}",
                output_text=final_output,
                output_path=saved,
            )

        return DeliveryResult(
            ok=False,
            mode="cli_exec",
            summary=f"Claude Code 执行失败 (code={rc})",
            output_text=final_output,
            error=stderr_text.strip() or final_output or f"退出码 {rc}",
        )


class OpenClawAdapter(AgentAdapter):
    """Runs local `openclaw agent --message-file <prompt_file>`."""

    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        exe = self.target.executable.strip()
        if not exe or not Path(exe).exists():
            return DeliveryResult(
                ok=False,
                mode="cli_exec",
                summary="未检测到 OpenClaw CLI",
                error="请确认本机已安装 openclaw 并在设置中配置路径。",
            )

        prompt_text = compose_agent_prompt(bundle, prompt_instruction, max_inline_chars=self.max_inline_chars)
        bundle_dir = Path(bundle.bundle_dir)
        msg_file = bundle_dir / "openclaw_prompt.md"
        msg_file.write_text(prompt_text, encoding="utf-8")

        args = ["agent", "--message-file", str(msg_file)]
        if self.target.openclaw_agent_id.strip():
            args.extend(["--agent", self.target.openclaw_agent_id.strip()])
        if self.target.openclaw_local:
            args.append("--local")
        args.extend(self.target.extra_args)

        if on_chunk:
            on_chunk("[OpenClaw] 正在向 OpenClaw Agent 发送消息...\n")

        cmd = _build_windows_command(exe, args)
        cwd = self.target.working_dir.strip() or str(bundle_dir)
        rc, stdout_text, stderr_text, aborted = _run_streaming_process(
            cmd,
            cwd=cwd,
            stdin_text=None,
            timeout_sec=self.timeout_sec,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )

        final_output = stdout_text.strip()
        if rc == 0 and not aborted:
            saved = _save_result_markdown(bundle, self.target.id, self.target.name, final_output or "（OpenClaw 已成功接收处理）")
            return DeliveryResult(
                ok=True,
                mode="cli_exec",
                summary=f"OpenClaw 执行完成，结果已保存至 {Path(saved).name}",
                output_text=final_output or "OpenClaw 已完成执行。",
                output_path=saved,
            )

        return DeliveryResult(
            ok=False,
            mode="cli_exec",
            summary=f"OpenClaw 执行失败 (code={rc})",
            output_text=final_output,
            error=stderr_text.strip() or final_output or f"退出码 {rc}（如未启动 Gateway，可在目标设置中勾选 --local）",
        )


class CustomCLIAdapter(AgentAdapter):
    """Runs any user-defined CLI command template with placeholders or stdin piping."""

    def deliver(
        self,
        bundle: ParsedBundle,
        prompt_instruction: str,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DeliveryResult:
        template = self.target.command_template.strip()
        if not template and self.target.executable.strip():
            template = f'"{self.target.executable.strip()}"'
        if not template:
            return DeliveryResult(
                ok=False,
                mode="cli_exec",
                summary="自定义 CLI 命令为空",
                error="请在目标设置中填写命令模板（支持 {prompt_file} / {bundle_md} / {bundle_dir} 占位符）。",
            )

        prompt_text = compose_agent_prompt(bundle, prompt_instruction, max_inline_chars=self.max_inline_chars)
        bundle_dir = Path(bundle.bundle_dir)
        prompt_file = bundle_dir / "agent_prompt.md"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        formatted = (
            template.replace("{prompt_file}", str(prompt_file))
            .replace("{bundle_md}", bundle.markdown_path)
            .replace("{bundle_dir}", str(bundle_dir))
            .replace("{title}", bundle.title)
        )
        tokens = shlex.split(formatted, posix=False)
        tokens = [t.strip('"') for t in tokens]
        cmd = _build_windows_command(tokens[0], tokens[1:])

        if on_chunk:
            on_chunk(f"[Custom CLI] 运行: {' '.join(cmd[:4])} ...\n")

        cwd = self.target.working_dir.strip() or str(bundle_dir)
        rc, stdout_text, stderr_text, aborted = _run_streaming_process(
            cmd,
            cwd=cwd,
            stdin_text=prompt_text,
            timeout_sec=self.timeout_sec,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )

        final_output = stdout_text.strip()
        if rc == 0 and not aborted:
            saved = _save_result_markdown(bundle, self.target.id, self.target.name, final_output or "（命令执行成功）")
            return DeliveryResult(
                ok=True,
                mode="cli_exec",
                summary=f"{self.target.name} 执行完成",
                output_text=final_output or "命令执行成功（无标准输出）。",
                output_path=saved,
            )

        return DeliveryResult(
            ok=False,
            mode="cli_exec",
            summary=f"{self.target.name} 执行失败 (code={rc})",
            output_text=final_output,
            error=stderr_text.strip() or final_output or f"退出码 {rc}",
        )

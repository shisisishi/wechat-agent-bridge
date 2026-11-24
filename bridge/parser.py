"""Smart parser for WeChat 4.1 ZIP archives, copied chat logs, text/HTML, and attachments."""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import re
import shutil
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
TEXT_EXTS = {".txt", ".md", ".log", ".csv", ".json", ".xml", ".yaml", ".yml"}
HTML_EXTS = {".html", ".htm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".silk", ".amr", ".ogg", ".aac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}

# Common WeChat desktop copy/export message header patterns:
# 1) "张三 2026-11-23 14:20:05" or "张三  2026/11/23 14:20"
# 2) "2026-11-23 14:20:05 张三"
# 3) "[2026-11-23 14:20:05] 张三:"
# 4) "张三:" followed by content (WeChat desktop copy format: "昵称:\n消息内容")
_RE_SENDER_TIME = re.compile(
    r"^(?P<sender>.{1,60}?)\s+(?P<time>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*$"
)
_RE_TIME_SENDER = re.compile(
    r"^\[?(?P<time>\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\]?\s+(?P<sender>.{1,60}?)\s*[:：]?\s*$"
)
_RE_WECHAT_COPY_HEADER = re.compile(
    r"^(?P<sender>[^\s:：][^\r\n:：]{0,40})[:：]\s*$"
)
_RE_DATE_DIVIDER = re.compile(r"^[—\-=]{2,}\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)\s*[—\-=]{2,}$")
_RE_URL = re.compile(r"https?://[^\s<>\"'\]）)]+")
_RE_HTML_TAG = re.compile(r"<[^>]+>")


@dataclass
class ChatMessage:
    sender: str
    timestamp: str
    content: str

    def fingerprint(self) -> str:
        norm = f"{self.sender.strip()}|{self.timestamp.strip()}|{' '.join(self.content.split())}"
        return hashlib.sha1(norm.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class AttachmentInfo:
    name: str
    path: str
    kind: str  # image | document | audio | video | archive | other
    size_bytes: int = 0
    mime_type: str = ""
    source_zip: str = ""


@dataclass
class ParsedBundle:
    bundle_id: str
    title: str
    source: str  # windows_share_target | clipboard | drop | sendto | batch_merge | manual
    created_at: str
    bundle_dir: str
    markdown_path: str
    plain_text: str
    markdown_text: str
    messages: list[ChatMessage] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    attachments: list[AttachmentInfo] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    original_archives: list[str] = field(default_factory=list)
    batch_count: int = 1

    def to_metadata_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "title": self.title,
            "source": self.source,
            "created_at": self.created_at,
            "bundle_dir": self.bundle_dir,
            "markdown_path": self.markdown_path,
            "message_count": len(self.messages),
            "participants": self.participants,
            "attachments": [asdict(a) for a in self.attachments],
            "urls": self.urls,
            "original_archives": self.original_archives,
            "batch_count": self.batch_count,
        }


def decode_bytes_safely(raw: bytes) -> str:
    """Decode text bytes trying UTF-8 BOM, UTF-8, GB18030, UTF-16."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def strip_html(raw_html: str) -> str:
    """Convert HTML fragment (including Windows CF_HTML) to readable plain text."""
    # Strip Windows CF_HTML header if present
    start_frag = raw_html.find("<!--StartFragment-->")
    end_frag = raw_html.find("<!--EndFragment-->")
    if start_frag != -1 and end_frag != -1 and end_frag > start_frag:
        raw_html = raw_html[start_frag + len("<!--StartFragment-->") : end_frag]
    text = re.sub(r"(?i)<br\s*/?>", "\n", raw_html)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
    text = _RE_HTML_TAG.sub("", text)
    text = html.unescape(text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Collapse >2 consecutive blank lines
    cleaned: list[str] = []
    blanks = 0
    for ln in lines:
        if ln.strip():
            blanks = 0
            cleaned.append(ln)
        else:
            blanks += 1
            if blanks <= 1:
                cleaned.append("")
    return "\n".join(cleaned).strip()


def classify_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_EXTS or ext in TEXT_EXTS or ext in HTML_EXTS:
        return "document"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext == ".zip":
        return "archive"
    return "other"


def parse_wechat_chat_text(text: str) -> list[ChatMessage]:
    """Parse common WeChat exported / copied chat transcripts into structured messages."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    lines = text.split("\n")
    messages: list[ChatMessage] = []
    current_date = ""
    cur_sender = ""
    cur_time = ""
    cur_buf: list[str] = []

    def flush() -> None:
        nonlocal cur_sender, cur_time, cur_buf
        body = "\n".join(cur_buf).strip()
        if cur_sender and body:
            messages.append(ChatMessage(sender=cur_sender, timestamp=cur_time, content=body))
        cur_buf = []

    # First pass: try timestamped headers (most accurate)
    has_timestamped = any(_RE_SENDER_TIME.match(ln.strip()) or _RE_TIME_SENDER.match(ln.strip()) for ln in lines)

    if has_timestamped:
        for ln in lines:
            stripped = ln.strip()
            dm = _RE_DATE_DIVIDER.match(stripped)
            if dm:
                current_date = dm.group(1)
                continue
            m1 = _RE_SENDER_TIME.match(stripped)
            m2 = _RE_TIME_SENDER.match(stripped) if not m1 else None
            if m1 or m2:
                flush()
                m = m1 or m2
                assert m is not None
                cur_sender = m.group("sender").strip()
                cur_time = m.group("time").strip()
            else:
                if cur_sender:
                    cur_buf.append(ln)
                elif stripped:
                    # Leading preamble before first header
                    cur_sender = "聊天记录"
                    cur_time = current_date
                    cur_buf.append(ln)
        flush()
        return messages

    # Second pass: WeChat PC Ctrl+C multi-select copy format ("张三:\n消息内容\n\n李四:\n消息内容")
    copy_headers = sum(1 for ln in lines if _RE_WECHAT_COPY_HEADER.match(ln.strip()))
    if copy_headers >= 2:
        for ln in lines:
            stripped = ln.strip()
            dm = _RE_DATE_DIVIDER.match(stripped)
            if dm:
                current_date = dm.group(1)
                continue
            mc = _RE_WECHAT_COPY_HEADER.match(stripped)
            if mc and not stripped.startswith(("http", "https", "C:", "D:", "E:", "F:")):
                flush()
                cur_sender = mc.group("sender").strip()
                cur_time = current_date
            else:
                if cur_sender:
                    cur_buf.append(ln)
                elif stripped:
                    cur_sender = "消息"
                    cur_time = current_date
                    cur_buf.append(ln)
        flush()
        return messages

    # Fallback: treat as a single unparsed note block
    return [ChatMessage(sender="微信内容", timestamp="", content=text)]


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(". ")
    return cleaned[:120] or "file"


def _fix_zip_filename(info: zipfile.ZipInfo) -> str:
    """Handle Windows WeChat ZIP entries that may use UTF-8 or GBK/CP437 encoding."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
        for enc in ("utf-8", "gb18030"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    except UnicodeEncodeError:
        pass
    return info.filename


def _extract_wechat_zip(
    zip_path: Path,
    extract_root: Path,
) -> tuple[list[str], list[AttachmentInfo], str]:
    """Extract a WeChat merged-forward ZIP archive safely and collect chat texts + attachments."""
    texts: list[str] = []
    attachments: list[AttachmentInfo] = []
    inferred_title = zip_path.stem

    extract_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            decoded_name = _fix_zip_filename(info)
            rel_parts = [p for p in Path(decoded_name.replace("\\", "/")).parts if p not in ("", ".", "..")]
            if not rel_parts:
                continue
            safe_rel = Path(*[_safe_filename(p) for p in rel_parts])
            dest = extract_root / safe_rel
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Avoid zip-slip by ensuring dest stays under extract_root
            if not str(dest.resolve()).startswith(str(extract_root.resolve())):
                continue

            data = zf.read(info)
            dest.write_bytes(data)

            ext = dest.suffix.lower()
            if ext in TEXT_EXTS:
                content = decode_bytes_safely(data).strip()
                if content:
                    texts.append(content)
                    if inferred_title == zip_path.stem and dest.stem:
                        inferred_title = dest.stem
            elif ext in HTML_EXTS:
                content = strip_html(decode_bytes_safely(data))
                if content:
                    texts.append(content)
            else:
                mime, _ = mimetypes.guess_type(str(dest))
                attachments.append(
                    AttachmentInfo(
                        name=dest.name,
                        path=str(dest.resolve()),
                        kind=classify_file(dest),
                        size_bytes=len(data),
                        mime_type=mime or "",
                        source_zip=zip_path.name,
                    )
                )

    return texts, attachments, inferred_title


def render_bundle_markdown(
    title: str,
    source: str,
    created_at: str,
    messages: list[ChatMessage],
    plain_text: str,
    participants: list[str],
    attachments: list[AttachmentInfo],
    urls: list[str],
    batch_count: int = 1,
) -> str:
    """Render a clean, Agent-friendly Markdown document for the forwarded chat bundle."""
    lines: list[str] = [
        f"# {title}",
        "",
        f"- **来源**: `{source}`",
        f"- **时间**: {created_at}",
        f"- **消息条数**: {len(messages)}" + (f"（合并 {batch_count} 批）" if batch_count > 1 else ""),
    ]
    if participants:
        lines.append(f"- **参与人**: {', '.join(participants[:20])}")
    if attachments:
        lines.append(f"- **附件数量**: {len(attachments)}")
    lines.append("")

    if attachments:
        lines.append("## 附件清单")
        lines.append("")
        for att in attachments:
            size_kb = max(1, att.size_bytes // 1024) if att.size_bytes else 0
            lines.append(f"- [{att.kind}] `{att.name}` ({size_kb} KB) — `{att.path}`")
        lines.append("")

    if urls:
        lines.append("## 提取链接")
        lines.append("")
        for u in urls[:30]:
            lines.append(f"- {u}")
        lines.append("")

    lines.append("## 聊天记录正文")
    lines.append("")

    if messages and not (len(messages) == 1 and messages[0].sender == "微信内容" and not messages[0].timestamp):
        for msg in messages:
            ts = f" `{msg.timestamp}`" if msg.timestamp else ""
            lines.append(f"### {msg.sender}{ts}")
            lines.append(msg.content)
            lines.append("")
    else:
        lines.append(plain_text or "（仅包含附件，无正文文本）")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _infer_title(
    explicit_title: str,
    messages: list[ChatMessage],
    plain_text: str,
    attachments: list[AttachmentInfo],
) -> str:
    if explicit_title.strip():
        return explicit_title.strip()[:80]
    participants = _extract_participants(messages)
    if len(participants) >= 2:
        sample = "、".join(participants[:3])
        if len(participants) > 3:
            sample += f" 等{len(participants)}人"
        return f"微信聊天 · {sample}"
    first_line = next((ln.strip() for ln in plain_text.splitlines() if ln.strip()), "")
    if first_line:
        return first_line[:48] + ("..." if len(first_line) > 48 else "")
    if attachments:
        return f"微信附件 · {attachments[0].name}"
    return f"微信转发 · {datetime.now().strftime('%m-%d %H:%M')}"


def _extract_participants(messages: list[ChatMessage]) -> list[str]:
    seen: list[str] = []
    ignore = {"微信内容", "聊天记录", "消息", "系统消息"}
    for m in messages:
        s = m.sender.strip()
        if s and s not in ignore and s not in seen:
            seen.append(s)
    return seen


def build_bundle(
    *,
    bundles_root: Path,
    source: str,
    title: str = "",
    text: str = "",
    html_text: str = "",
    url: str = "",
    file_paths: Iterable[str | Path] = (),
) -> ParsedBundle:
    """Ingest raw files/ZIPs/text/HTML/URLs into a structured, persistent bundle directory."""
    now = datetime.now()
    bundle_id = now.strftime("%Y%m%d_%H%M%S_") + hashlib.sha1(
        f"{now.isoformat()}{title}{text[:64]}".encode("utf-8", errors="ignore")
    ).hexdigest()[:6]
    bundle_dir = bundles_root / bundle_id
    files_dir = bundle_dir / "files"
    archives_dir = bundle_dir / "archives"
    extracted_dir = bundle_dir / "extracted"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    text_chunks: list[str] = []
    attachments: list[AttachmentInfo] = []
    original_archives: list[str] = []
    zip_title_hint = ""

    if text and text.strip():
        text_chunks.append(text.strip())
    elif html_text and html_text.strip():
        stripped = strip_html(html_text)
        if stripped:
            text_chunks.append(stripped)

    if url and url.strip() and url.strip() not in "\n".join(text_chunks):
        text_chunks.append(url.strip())

    for raw_fp in file_paths:
        src = Path(raw_fp)
        if not src.exists():
            continue
        if src.is_dir():
            # Collect files inside a dropped folder (non-recursive 2 levels max)
            for child in sorted(src.rglob("*")):
                if child.is_file():
                    _ingest_single_file(
                        child, files_dir, archives_dir, extracted_dir,
                        text_chunks, attachments, original_archives
                    )
            continue
        hint = _ingest_single_file(
            src, files_dir, archives_dir, extracted_dir,
            text_chunks, attachments, original_archives
        )
        if hint and not zip_title_hint:
            zip_title_hint = hint

    plain_text = "\n\n".join(chunk for chunk in text_chunks if chunk.strip()).strip()
    messages = parse_wechat_chat_text(plain_text)
    participants = _extract_participants(messages)
    urls = sorted(set(_RE_URL.findall(plain_text)))
    final_title = _infer_title(title or zip_title_hint, messages, plain_text, attachments)
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")

    md_text = render_bundle_markdown(
        title=final_title,
        source=source,
        created_at=created_at,
        messages=messages,
        plain_text=plain_text,
        participants=participants,
        attachments=attachments,
        urls=urls,
        batch_count=1,
    )
    md_path = bundle_dir / "chat_bundle.md"
    md_path.write_text(md_text, encoding="utf-8")

    bundle = ParsedBundle(
        bundle_id=bundle_id,
        title=final_title,
        source=source,
        created_at=created_at,
        bundle_dir=str(bundle_dir.resolve()),
        markdown_path=str(md_path.resolve()),
        plain_text=plain_text,
        markdown_text=md_text,
        messages=messages,
        participants=participants,
        attachments=attachments,
        urls=urls,
        original_archives=original_archives,
        batch_count=1,
    )
    (bundle_dir / "metadata.json").write_text(
        json.dumps(bundle.to_metadata_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return bundle


def _ingest_single_file(
    src: Path,
    files_dir: Path,
    archives_dir: Path,
    extracted_dir: Path,
    text_chunks: list[str],
    attachments: list[AttachmentInfo],
    original_archives: list[str],
) -> str:
    ext = src.suffix.lower()
    if ext == ".zip" and zipfile.is_zipfile(src):
        archives_dir.mkdir(parents=True, exist_ok=True)
        saved_zip = archives_dir / _safe_filename(src.name)
        if src.resolve() != saved_zip.resolve():
            shutil.copy2(src, saved_zip)
        original_archives.append(str(saved_zip.resolve()))
        sub_extract = extracted_dir / _safe_filename(src.stem)
        z_texts, z_atts, title_hint = _extract_wechat_zip(saved_zip, sub_extract)
        text_chunks.extend(z_texts)
        attachments.extend(z_atts)
        return title_hint

    files_dir.mkdir(parents=True, exist_ok=True)
    dest = files_dir / _safe_filename(src.name)
    counter = 1
    while dest.exists() and dest.resolve() != src.resolve():
        dest = files_dir / f"{_safe_filename(src.stem)}_{counter}{ext}"
        counter += 1
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)

    if ext in TEXT_EXTS and dest.stat().st_size <= 2 * 1024 * 1024:
        content = decode_bytes_safely(dest.read_bytes()).strip()
        if content:
            text_chunks.append(content)
    elif ext in HTML_EXTS and dest.stat().st_size <= 2 * 1024 * 1024:
        content = strip_html(decode_bytes_safely(dest.read_bytes()))
        if content:
            text_chunks.append(content)

    mime, _ = mimetypes.guess_type(str(dest))
    attachments.append(
        AttachmentInfo(
            name=dest.name,
            path=str(dest.resolve()),
            kind=classify_file(dest),
            size_bytes=dest.stat().st_size,
            mime_type=mime or "",
        )
    )
    return ""


def merge_bundles(bundles_root: Path, bundles: list[ParsedBundle], title: str = "") -> ParsedBundle:
    """Merge multiple WeChat batches into one deduplicated bundle (bypassing the 100-msg limit)."""
    now = datetime.now()
    bundle_id = now.strftime("%Y%m%d_%H%M%S_merged_") + hashlib.sha1(
        ",".join(b.bundle_id for b in bundles).encode("utf-8")
    ).hexdigest()[:6]
    bundle_dir = bundles_root / bundle_id
    assets_dir = bundle_dir / "attachments"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    merged_messages: list[ChatMessage] = []
    seen_fps: set[str] = set()
    merged_attachments: list[AttachmentInfo] = []
    seen_att_hashes: set[str] = set()
    merged_archives: list[str] = []
    merged_urls: list[str] = []

    for b in bundles:
        for msg in b.messages:
            fp = msg.fingerprint()
            if fp not in seen_fps:
                seen_fps.add(fp)
                merged_messages.append(msg)
        for att in b.attachments:
            src = Path(att.path)
            if not src.exists():
                continue
            h = hashlib.sha1(src.read_bytes()).hexdigest()[:16] if src.stat().st_size <= 20 * 1024 * 1024 else f"{att.name}:{att.size_bytes}"
            if h in seen_att_hashes:
                continue
            seen_att_hashes.add(h)
            assets_dir.mkdir(parents=True, exist_ok=True)
            dest = assets_dir / _safe_filename(src.name)
            idx = 1
            while dest.exists():
                dest = assets_dir / f"{_safe_filename(src.stem)}_{idx}{src.suffix}"
                idx += 1
            shutil.copy2(src, dest)
            merged_attachments.append(
                AttachmentInfo(
                    name=dest.name,
                    path=str(dest.resolve()),
                    kind=att.kind,
                    size_bytes=att.size_bytes,
                    mime_type=att.mime_type,
                    source_zip=att.source_zip,
                )
            )
        merged_archives.extend(b.original_archives)
        for u in b.urls:
            if u not in merged_urls:
                merged_urls.append(u)

    plain_parts = []
    for m in merged_messages:
        header = f"{m.sender} {m.timestamp}".strip()
        plain_parts.append(f"{header}\n{m.content}")
    plain_text = "\n\n".join(plain_parts).strip()
    participants = _extract_participants(merged_messages)
    final_title = title.strip() or f"合并聊天记录 ({len(bundles)} 批 · {len(merged_messages)} 条)"
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")

    md_text = render_bundle_markdown(
        title=final_title,
        source="batch_merge",
        created_at=created_at,
        messages=merged_messages,
        plain_text=plain_text,
        participants=participants,
        attachments=merged_attachments,
        urls=merged_urls,
        batch_count=len(bundles),
    )
    md_path = bundle_dir / "chat_bundle.md"
    md_path.write_text(md_text, encoding="utf-8")

    result = ParsedBundle(
        bundle_id=bundle_id,
        title=final_title,
        source="batch_merge",
        created_at=created_at,
        bundle_dir=str(bundle_dir.resolve()),
        markdown_path=str(md_path.resolve()),
        plain_text=plain_text,
        markdown_text=md_text,
        messages=merged_messages,
        participants=participants,
        attachments=merged_attachments,
        urls=merged_urls,
        original_archives=merged_archives,
        batch_count=len(bundles),
    )
    (bundle_dir / "metadata.json").write_text(
        json.dumps(result.to_metadata_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result

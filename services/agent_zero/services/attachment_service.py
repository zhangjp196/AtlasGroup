"""对话附件：图片 / 文本代码 / Office / PDF 的落盘、文本抽取与读取。

- 图片：原样保存，发送给支持视觉的模型时转成 data URL；
- 文本 / 代码：按 UTF-8 / GB18030 等编码解码为文本；
- Office：docx / xlsx / xlsm / pptx / odt / ods 使用标准库解析；
  doc / ppt 依次尝试 textutil / soffice / antiword / catdoc，最后退回可读字符串；
- PDF：优先 pypdf，其次 pdftotext，最后用标准库解压内容流做基础提取。
  （抽取逻辑与工作区文件工具共用 `document_service`，两边行为必须一致。）

附件文件统一放在 `data/agent_zero/uploads/<conversation_id>/`，
消息节点只保存安全的元数据（id / name / mime / size / kind / text_chars）。
"""

from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from services.agent_zero.services import document_service
from services.shared.project_paths import get_project_root

logger = logging.getLogger(__name__)

MAX_FILES = 8
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_EXTRACT_CHARS = 400_000
MAX_CONTEXT_TEXT_CHARS = 60_000
MAX_IMAGE_BYTES_FOR_MODEL = 8 * 1024 * 1024
MAX_IMAGE_BYTES_TOTAL = 22 * 1024 * 1024

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"}
PDF_EXT = document_service.PDF_EXT
OFFICE_ZIP_EXT = document_service.OFFICE_ZIP_EXT
LEGACY_OFFICE_EXT = document_service.LEGACY_OFFICE_EXT
TEXT_EXT = {
    ".txt", ".text", ".md", ".markdown", ".mdx", ".rst", ".log", ".csv", ".tsv",
    ".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml", ".ini", ".conf", ".cfg",
    ".properties", ".env", ".html", ".htm", ".xhtml", ".css", ".scss", ".sass",
    ".less", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".py", ".pyw", ".java", ".kt", ".kts", ".scala", ".groovy", ".cs", ".c", ".h",
    ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm", ".go", ".rs", ".rb", ".php",
    ".swift", ".dart", ".lua", ".r", ".pl", ".pm", ".sh", ".bash", ".zsh", ".fish",
    ".bat", ".cmd", ".ps1", ".sql", ".gradle", ".make", ".mk", ".dockerfile",
    ".gitignore", ".gitattributes", ".editorconfig", ".tex", ".vue", ".proto",
}
TEXT_NAMES = {"Dockerfile", "Makefile", "Rakefile", "Gemfile", "Procfile", "LICENSE"}

_ATT_ID_RE = re.compile(r"^att-[0-9a-f]{16}$")
_UNSAFE_NAME_RE = re.compile(r"[\x00-\x1f\x7f/\\]")


def _uploads_root() -> Path:
    return Path(get_project_root()) / "data" / "agent_zero" / "uploads"


def _conv_dir(conv_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(conv_id or "default"))
    return _uploads_root() / safe


def _safe_name(name: Any) -> str:
    raw = Path(str(name or "").replace("\\", "/")).name
    raw = _UNSAFE_NAME_RE.sub("_", raw).strip().strip(".")
    return (raw or "file")[:180]


def _ext_of(name: str) -> str:
    return Path(name).suffix.lower()


def _kind_of(ext: str, mime: str, name: str = "") -> str:
    if ext in IMAGE_EXT or mime.startswith("image/"):
        return "image"
    if ext in PDF_EXT or mime == "application/pdf":
        return "pdf"
    if ext in OFFICE_ZIP_EXT or ext in LEGACY_OFFICE_EXT:
        return "office"
    if mime.startswith(("audio/", "video/")):
        return "media"
    if Path(name or "").name in TEXT_NAMES:
        return "text"
    if ext in TEXT_EXT or mime.startswith("text/"):
        return "text"
    return "file"


def _guess_mime(name: str, declared: str = "") -> str:
    mime = str(declared or "").strip().lower()
    if mime and mime != "application/octet-stream":
        return mime
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _decode_base64(data: Any) -> bytes:
    raw = str(data or "").strip()
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("附件数据不是合法的 base64") from e


# ── 文本解码 ────────────────────────────────────────────────────────────────

def _decode_text_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16", errors="replace")
        except Exception:
            pass
    # NUL 通常意味着二进制；但 UTF-16 文本也可能出现，已在上方处理。
    if b"\x00" in data[:4096]:
        return ""
    for enc in ("utf-8", "gb18030", "big5", "shift_jis", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_text_file(path: Path) -> str:
    return _decode_text_bytes(path.read_bytes()[:MAX_TEXT_BYTES])


# ── Office / PDF 文本抽取 ──────────────────────────────────────────────────

def _extract_text(path: Path, ext: str, kind: str) -> str:
    """附件正文抽取：Office / PDF 走 document_service，其余按文本解码。"""
    if kind in ("office", "pdf"):
        try:
            text = document_service.extract_text(path)
        except document_service.DocumentError as e:
            raise ValueError(str(e)) from e
    else:
        text = _read_text_file(path)
    return (text or "").replace("\x00", "")


# ── 上传 / 读取 ────────────────────────────────────────────────────────────

def _meta_path(folder: Path, att_id: str) -> Path:
    return folder / f"{att_id}.meta.json"


def _text_path(folder: Path, att_id: str) -> Path:
    return folder / f"{att_id}.extracted.txt"


def _read_meta(folder: Path, att_id: str) -> Optional[Dict[str, Any]]:
    path = _meta_path(folder, att_id)
    if not path.is_file():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("附件元数据读取失败: %s", path, exc_info=True)
        return None


def process_uploads(conv_id: str, items: List[Dict[str, Any]],
                    *, allow_images: bool) -> List[Dict[str, Any]]:
    """校验并保存上传附件，返回可直接写入消息节点的公开元数据。"""
    if not items:
        return []
    if len(items) > MAX_FILES:
        raise ValueError(f"一次最多上传 {MAX_FILES} 个附件")
    folder = _conv_dir(conv_id)
    folder.mkdir(parents=True, exist_ok=True)

    prepared: List[Tuple[bytes, str, str, str]] = []
    total = 0
    for item in items:
        name = _safe_name(item.get("name"))
        ext = _ext_of(name)
        mime = _guess_mime(name, item.get("mime") or "")
        kind = _kind_of(ext, mime, name)
        raw = _decode_base64(item.get("data"))
        if not raw:
            raise ValueError(f"附件为空：{name}")
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(f"附件过大（单个上限 25 MB）：{name}")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("附件总大小超过 50 MB")
        if kind == "image" and not allow_images:
            raise ValueError("当前模型未开启图片支持，无法上传图片")
        if kind == "file":
            # 未识别的扩展名：只有能当文本读出来才接受。
            if not _decode_text_bytes(raw[:MAX_TEXT_BYTES]).strip():
                raise ValueError(f"不支持的文件类型：{name}")
            kind = "text"
        prepared.append((raw, name, ext, mime))

    out: List[Dict[str, Any]] = []
    for raw, name, ext, mime in prepared:
        att_id = f"att-{uuid.uuid4().hex[:16]}"
        kind = _kind_of(ext, mime, name)
        unique = f"{att_id}{ext or '.bin'}"
        target = folder / unique
        target.write_bytes(raw)
        meta: Dict[str, Any] = {
            "id": att_id,
            "name": name,
            "mime": mime,
            "size": len(raw),
            "kind": kind,
            "text_chars": 0,
            "truncated": False,
        }
        if kind in ("text", "office", "pdf"):
            try:
                text = _extract_text(target, ext, kind)
            except Exception as e:
                target.unlink(missing_ok=True)
                raise ValueError(f"无法解析 {name}：{e}") from e
            if not text.strip():
                target.unlink(missing_ok=True)
                raise ValueError(f"无法从 {name} 中提取文本")
            truncated = len(text) > MAX_EXTRACT_CHARS
            text = text[:MAX_EXTRACT_CHARS]
            _text_path(folder, att_id).write_text(text, encoding="utf-8")
            meta["text_chars"] = len(text)
            meta["truncated"] = truncated
        try:
            import json

            _meta_path(folder, att_id).write_text(
                json.dumps({**meta, "_file": unique}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("附件元数据写入失败: %s", target, exc_info=True)
        out.append(meta)
    return out


def read_attachment_text(conv_id: str, attachment: Dict[str, Any],
                         max_chars: int = MAX_CONTEXT_TEXT_CHARS) -> str:
    att_id = str(attachment.get("id") or "")
    if not _ATT_ID_RE.match(att_id):
        return ""
    path = _text_path(_conv_dir(conv_id), att_id)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")[:max_chars]
        if attachment.get("truncated") or len(text) >= max_chars:
            text += "\n…[附件文本已截断]"
        return text
    except Exception:
        logger.warning("附件文本读取失败: %s", path, exc_info=True)
        return ""


def image_data_url(conv_id: str, attachment: Dict[str, Any],
                   max_bytes: int = MAX_IMAGE_BYTES_FOR_MODEL) -> str:
    att_id = str(attachment.get("id") or "")
    if not _ATT_ID_RE.match(att_id):
        return ""
    folder = _conv_dir(conv_id)
    meta = _read_meta(folder, att_id) or {}
    filename = str(meta.get("_file") or "")
    if not filename:
        matches = [p for p in folder.glob(f"{att_id}.*")
                   if p.is_file() and not p.name.endswith((".json", ".txt"))]
        filename = matches[0].name if matches else ""
    if not filename:
        return ""
    path = folder / filename
    if not path.is_file() or path.stat().st_size > max_bytes:
        return ""
    mime = str(attachment.get("mime") or meta.get("mime") or "image/png")
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"
    except Exception:
        logger.warning("图片读取失败: %s", path, exc_info=True)
        return ""


def resolve_attachment(conv_id: str, att_id: str) -> Tuple[Optional[Path], Dict[str, Any]]:
    if not _ATT_ID_RE.match(str(att_id or "")):
        return None, {}
    folder = _conv_dir(conv_id)
    meta = _read_meta(folder, att_id) or {}
    filename = str(meta.get("_file") or "")
    if not filename:
        return None, meta
    path = folder / filename
    if not path.is_file():
        return None, meta
    return path, meta


def public_attachments(conv_id: str, attachments: Any) -> List[Dict[str, Any]]:
    """前端展示用的附件元数据（带受控下载地址，不暴露磁盘路径）。"""
    out: List[Dict[str, Any]] = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        att_id = str(att.get("id") or "")
        if not _ATT_ID_RE.match(att_id):
            continue
        item = {
            key: att.get(key) for key in
            ("id", "name", "mime", "size", "kind", "text_chars", "truncated")
        }
        item["url"] = (
            f"/api/agent-zero/conversations/{quote(str(conv_id), safe='')}"
            f"/attachments/{quote(att_id, safe='')}"
        )
        out.append(item)
    return out


def remove_conversation(conv_id: str) -> None:
    """删除对话时清理其附件目录（尽力而为，不影响主流程）。"""
    folder = _conv_dir(conv_id)
    try:
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        logger.warning("清理对话附件失败: %s", folder, exc_info=True)


def remove_attachments(conv_id: str, attachments: Any) -> None:
    folder = _conv_dir(conv_id)
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        att_id = str(att.get("id") or "")
        if not _ATT_ID_RE.match(att_id):
            continue
        for path in folder.glob(f"{att_id}.*"):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def copy_conversation_attachments(src_conv_id: str, dst_conv_id: str,
                                  attachments: Any = None) -> int:
    """复制附件目录（会话 fork 用）：元数据与正文文件按 att-id 原样同名拷贝。

    `attachments` 给出消息里引用的附件元数据时只复制这些 id 对应的文件；
    省略则复制整个会话目录。返回复制的文件数。
    """
    src = _conv_dir(src_conv_id)
    if not src.is_dir():
        return 0
    only: Optional[set] = None
    if attachments:
        only = {str(a.get("id")) for a in attachments
                if isinstance(a, dict) and a.get("id")}
        if not only:
            return 0
    dst = _conv_dir(dst_conv_id)
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in src.iterdir():
        if not path.is_file():
            continue
        if only is not None and not any(
                path.name.startswith(f"{aid}.") for aid in only):
            continue
        try:
            shutil.copy2(path, dst / path.name)
            copied += 1
        except OSError:
            logger.warning("附件复制失败: %s", path, exc_info=True)
    return copied

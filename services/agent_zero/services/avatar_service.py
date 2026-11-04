"""头像上传：角色 / 部门 / 群组共用一套落盘与校验。

- 文件放在 `data/agent_zero/avatars/<scope>/`，数据库只存 URL；
- 常见浏览器格式（PNG / JPEG / GIF / WebP / AVIF）原样保留；
- BMP / TIFF / ICO 等其它格式在 Pillow 可用时转成 PNG；
- SVG 只做基本安全校验，并在读取时用 CSP 限制脚本。
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from services.shared.project_paths import get_project_root

SCOPES = {"role", "department", "team"}
MAX_BYTES = 8 * 1024 * 1024
URL_PREFIX = "/api/agent-zero/avatars"

_WEB_EXT = {
    "PNG": (".png", "image/png"),
    "JPEG": (".jpg", "image/jpeg"),
    "GIF": (".gif", "image/gif"),
    "WEBP": (".webp", "image/webp"),
    "AVIF": (".avif", "image/avif"),
}
_FALLBACK_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
    ".bmp": "image/bmp", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}
_SAFE_NAME_RE = re.compile(r"^avatar-[0-9a-f]{16}\.(png|jpg|jpeg|gif|webp|avif|svg)$")


def _root() -> Path:
    return Path(get_project_root()) / "data" / "agent_zero" / "avatars"


def _scope_dir(scope: str) -> Path:
    scope = str(scope or "").strip().lower()
    if scope not in SCOPES:
        raise ValueError(f"非法头像类型: {scope or '(空)'}")
    return _root() / scope


def _normalize_ext(name: str, content_type: str) -> str:
    ext = Path(str(name or "")).suffix.lower()
    if ext in _FALLBACK_EXT:
        return ext
    ctype = str(content_type or "").lower()
    for candidate, mime in _FALLBACK_EXT.items():
        if ctype == mime:
            return candidate
    return ""


def _svg_ok(data: bytes) -> bool:
    head = data[:8192].decode("utf-8", errors="ignore").lower()
    if "<svg" not in head:
        return False
    lowered = data.lower()
    return not any(bad in lowered for bad in (
        b"<script", b"javascript:", b"onload=", b"onerror=",
    ))


def save_avatar(scope: str, data: bytes, filename: str = "",
                content_type: str = "") -> Dict[str, Any]:
    """校验并保存一张头像，返回 {url, name, mime, size, width, height}。"""
    if not data:
        raise ValueError("头像文件为空")
    if len(data) > MAX_BYTES:
        raise ValueError(f"头像不能超过 {MAX_BYTES // (1024 * 1024)} MB")

    declared_ext = _normalize_ext(filename, content_type)
    is_svg = declared_ext == ".svg" or str(content_type or "").lower() == "image/svg+xml"
    width = height = 0

    if is_svg:
        if not _svg_ok(data):
            raise ValueError("SVG 头像包含不安全的脚本或不是合法 SVG")
        ext, mime = ".svg", "image/svg+xml"
    else:
        try:
            from PIL import Image, ImageOps
        except Exception:  # pragma: no cover - 仅缺 Pillow 的打包环境
            if declared_ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"):
                raise ValueError("该图片格式需要 Pillow 转换，请换 PNG / JPG / GIF / WebP")
            ext = ".jpg" if declared_ext == ".jpeg" else declared_ext
            mime = _FALLBACK_EXT[declared_ext]
        else:
            try:
                with Image.open(io.BytesIO(data)) as img:
                    img.load()
                    fmt = str(img.format or "").upper()
                    width, height = img.size
                    if fmt in _WEB_EXT:
                        ext, mime = _WEB_EXT[fmt]
                    else:
                        # BMP / TIFF / ICO / HEIF 等转成 PNG，保证浏览器能显示。
                        converted = ImageOps.exif_transpose(img)
                        if converted.mode not in ("RGB", "RGBA"):
                            converted = converted.convert("RGBA")
                        buf = io.BytesIO()
                        converted.save(buf, format="PNG", optimize=True)
                        data = buf.getvalue()
                        ext, mime = ".png", "image/png"
            except Exception as e:
                raise ValueError("无法识别的图片文件") from e

    folder = _scope_dir(scope)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"avatar-{uuid.uuid4().hex[:16]}{ext}"
    (folder / name).write_bytes(data)
    return {
        "url": f"{URL_PREFIX}/{scope}/{name}",
        "name": name,
        "mime": mime,
        "size": len(data),
        "width": width,
        "height": height,
    }


def resolve_avatar(scope: str, filename: str) -> Optional[Tuple[Path, str]]:
    """按 URL 片段找回头像文件；文件名不合法或不存在时返回 None。"""
    scope = str(scope or "").strip().lower()
    name = str(filename or "").strip()
    if scope not in SCOPES or not _SAFE_NAME_RE.match(name):
        return None
    path = _scope_dir(scope) / name
    if not path.is_file():
        return None
    return path, _FALLBACK_EXT.get(path.suffix.lower(), "application/octet-stream")

"""Workspace 文件预览渲染。

把 html / office(docx、xlsx、pptx、odt、ods) / markdown / 图片 / pdf 转换成前端
可以展示的内容。Office 文件本质是 zip 里的 XML，这里只用标准库解析，不引入额外
依赖；生成的 HTML 文本全部转义，html 文件本身用沙箱 iframe 渲染（不执行脚本）。
"""

from __future__ import annotations

import html
import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from services.agent_zero.services.workspace_service import (
    WorkspacePathError,
    resolve_workspace_path,
    safe_relpath,
)

logger = logging.getLogger(__name__)

MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 60000
MAX_TABLE_ROWS = 300
MAX_TABLE_COLS = 40

HTML_EXT = {".html", ".htm", ".xhtml"}
SVG_EXT = {".svg"}
OFFICE_EXT = {".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"}
PDF_EXT = {".pdf"}
MARKDOWN_EXT = {".md", ".markdown", ".mdx"}

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OD_DOC = "urn:oasis:names:tc:opendocument:xmlns:document:0.0"
OD_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:0.0"
OD_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:0.0"


def _tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _table_html(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    head, *body = rows
    out = ['<table class="pv-table"><thead><tr>']
    out.extend(f"<th>{_esc(c)}</th>" for c in head)
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


# ── Office (OOXML / ODF) ────────────────────────────────────────────────────

def _docx_preview(target: Path) -> Tuple[str, str]:
    with zipfile.ZipFile(target) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))

    body = root.find(_tag(W_NS, "body"))
    if body is None:
        raise ValueError("word/document.xml 结构异常")

    blocks: List[str] = []
    plain: List[str] = []

    for el in body:
        tag = el.tag
        if tag == _tag(W_NS, "p"):
            text = "".join(
                ("\n" if node.tag == _tag(W_NS, "br") else (node.text or ""))
                for node in el.iter()
                if node.tag in (_tag(W_NS, "t"), _tag(W_NS, "br"))
            ).strip()
            if not text:
                continue
            plain.append(text)
            ppr = el.find(_tag(W_NS, "pPr"))
            style = ""
            if ppr is not None:
                pstyle = ppr.find(_tag(W_NS, "pStyle"))
                if pstyle is not None:
                    style = (pstyle.get(_tag(W_NS, "val")) or "").lower()
                if ppr.find(_tag(W_NS, "numPr")) is not None and not style:
                    blocks.append(f'<p class="pv-li">{_esc(text)}</p>')
                    continue
            if style.startswith("heading"):
                level = min(6, (int(style[7:]) if style[7:].isdigit() else 2) + 1)
                blocks.append(f"<h{level}>{_esc(text)}</h{level}>")
            else:
                blocks.append(f"<p>{_esc(text)}</p>")
        elif tag == _tag(W_NS, "tbl"):
            rows: List[List[str]] = []
            for tr in el.findall(_tag(W_NS, "tr")):
                cells = []
                for tc in tr.findall(_tag(W_NS, "tc")):
                    cell_text = " ".join(
                        "".join(
                            node.text or "" for node in p.iter() if node.tag == _tag(W_NS, "t")
                        )
                        for p in tc.findall(_tag(W_NS, "p"))
                    ).strip()
                    cells.append(cell_text)
                rows.append(cells)
            if rows:
                blocks.append(_table_html(rows))
                plain.append("\n".join(" | ".join(r) for r in rows))

    return f'<div class="pv-doc">{"".join(blocks)}</div>', "\n".join(plain)


def _xlsx_preview(target: Path) -> Tuple[str, str, bool]:
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rel_map: Dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")):
                rel_map[rel.get("Id")] = rel.get("Target") or ""

        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).iter(_tag(S_NS, "si")):
                shared.append("".join(t.text or "" for t in si.iter(_tag(S_NS, "t"))))

        sheets: List[Tuple[str, str]] = []
        for sheet in workbook.iter(_tag(S_NS, "sheet")):
            rid = sheet.get(_tag(R_NS, "id"))
            rel = (rel_map.get(rid) or "").lstrip("/")
            if not rel:
                continue
            path = rel if rel.startswith("xl/") else f"xl/{rel}"
            if path in names:
                sheets.append((sheet.get("name") or f"Sheet {len(sheets) + 1}", zf.read(path)))

    blocks: List[str] = []
    plain: List[str] = []
    truncated = False
    for name, sheet_xml in sheets:
        sheet_root = ET.fromstring(sheet_xml)
        rows: List[List[str]] = []
        for tr in sheet_root.iter(_tag(S_NS, "row")):
            cells: List[str] = []
            for cell in tr.findall(_tag(S_NS, "c")):
                ctype = cell.get("t") or ""
                if ctype == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(_tag(S_NS, "t")))
                else:
                    v = cell.find(_tag(S_NS, "v"))
                    raw = (v.text or "") if v is not None else ""
                    if ctype == "s" and raw.isdigit():
                        value = shared[int(raw)]
                    else:
                        value = raw
                cells.append(value)
            if any(c.strip() for c in cells):
                rows.append(cells[:MAX_TABLE_COLS])
            if len(rows) >= MAX_TABLE_ROWS:
                truncated = True
                break
        if not rows:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        blocks.append(f'<h3 class="pv-sheet">{_esc(name)}</h3>{_table_html(rows)}')
        plain.append(f"## {name}\n" + "\n".join(" | ".join(r) for r in rows))

    if not blocks:
        raise ValueError("工作表为空")
    return f'<div class="pv-doc">{"".join(blocks)}</div>', "\n".join(plain), truncated


def _pptx_preview(target: Path) -> Tuple[str, str]:
    with zipfile.ZipFile(target) as zf:
        slides = sorted(
            (n for n in zf.namelist()
             if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
            key=lambda n: int(n[len("ppt/slides/slide"): -4] or 0),
        )
        blocks: List[str] = []
        plain: List[str] = []
        for idx, name in enumerate(slides, start=1):
            root = ET.fromstring(zf.read(name))
            content = root.find(_tag(P_NS, "content"))
            parts: List[str] = []
            texts: List[str] = []
            if content is None:
                continue
            for el in content:
                if el.tag == _tag(P_NS, "sp"):
                    runs = [t.text or "" for t in el.iter(_tag(A_NS, "t"))]
                    line = "\n".join(r.strip() for r in runs if r.strip())
                    if not line:
                        continue
                    ph = el.find(f"{_tag(P_NS, 'spPr')}/{_tag(P_NS, 'ph')}")
                    is_title = ph is not None and (ph.get("type") or "") in ("title", "centerTitle")
                    texts.append(line)
                    parts.append(f"<h3>{_esc(line)}</h3>" if is_title else f"<p>{_esc(line)}</p>")
                elif el.tag == _tag(P_NS, "graphicFrame"):
                    rows = []
                    for tr in el.iter(_tag(A_NS, "tr")):
                        rows.append([
                            "".join(t.text or "" for t in tc.iter(_tag(A_NS, "t"))).strip()
                            for tc in tr.findall(_tag(A_NS, "tc"))
                        ])
                    if rows:
                        blocks_truncated = rows[:MAX_TABLE_ROWS]
                        parts.append(_table_html(blocks_truncated))
                        texts.append(" | ".join(" | ".join(r) for r in blocks_truncated))
            blocks.append(
                f'<section class="pv-slide"><h2>{_esc(f"Slide {idx}")}</h2>{"".join(parts)}</section>'
            )
            plain.append(f"### Slide {idx}\n" + "\n".join(texts))

    if not blocks:
        raise ValueError("幻灯片为空")
    return f'<div class="pv-doc">{"".join(blocks)}</div>', "\n".join(plain)


def _attr(el, *names: str) -> Optional[str]:
    for name in names:
        value = el.get(name)
        if value:
            return value
    return None


def _odt_preview(target: Path) -> Tuple[str, str]:
    root = ET.fromstring(zipfile.ZipFile(target).read("content.xml"))
    parents = {child: el for el in root.iter() for child in el}

    def _in_table(el) -> bool:
        cur = parents.get(el)
        while cur is not None:
            if cur.tag in (_tag(OD_TABLE, "table"), _tag(OD_TABLE, "cell")):
                return True
            cur = parents.get(cur)
        return False

    blocks: List[str] = []
    plain: List[str] = []
    for el in root.iter():
        if _in_table(el):
            continue
        if el.tag in (_tag(OD_TEXT, "p"), _tag(OD_DOC, "p")):
            text = "".join(el.itertext()).strip()
            if text:
                blocks.append(f"<p>{_esc(text)}</p>")
                plain.append(text)
        elif el.tag in (_tag(OD_TEXT, "h"), _tag(OD_DOC, "h")):
            raw_level = _attr(el, _tag(OD_TEXT, "level"), _tag(OD_DOC, "level")) or "1"
            try:
                level = int(raw_level)
            except ValueError:
                level = 1
            text = "".join(el.itertext()).strip()
            if text:
                tag = f"h{min(6, level + 1)}"
                blocks.append(f"<{tag}>{_esc(text)}</{tag}>")
                plain.append(text)
    for table in root.iter(_tag(OD_TABLE, "table")):
        rows = []
        for tr in table.findall(_tag(OD_TABLE, "table-row")):
            rows.append(["".join(tc.itertext()).strip() for tc in tr.findall(_tag(OD_TABLE, "table-cell"))])
        if rows:
            blocks.append(_table_html(rows))
            plain.append("\n".join(" | ".join(r) for r in rows))
    if not blocks:
        raise ValueError("文档为空")
    return f'<div class="pv-doc">{"".join(blocks)}</div>', "\n".join(plain)


def _ods_preview(target: Path) -> Tuple[str, str]:
    root = ET.fromstring(zipfile.ZipFile(target).read("content.xml"))
    blocks: List[str] = []
    plain: List[str] = []
    for table in root.iter(_tag(OD_TABLE, "table")):
        name = table.get(_tag(OD_TABLE, "name") or "") or "Sheet"
        rows = []
        for tr in table.findall(_tag(OD_TABLE, "table-row")):
            rows.append(["".join(tc.itertext()).strip() for tc in tr.findall(_tag(OD_TABLE, "table-cell"))])
        rows = rows[:MAX_TABLE_ROWS]
        if rows:
            blocks.append(f'<h3 class="pv-sheet">{_esc(name)}</h3>{_table_html(rows)}')
            plain.append("\n".join(" | ".join(r) for r in rows))
    if not blocks:
        raise ValueError("表格为空")
    return f'<div class="pv-doc">{"".join(blocks)}</div>', "\n".join(plain)


def _office_preview(ext: str, target: Path) -> Tuple[str, str, bool]:
    if ext == ".docx":
        rendered, plain = _docx_preview(target)
        return rendered, plain, False
    if ext in (".xlsx", ".xlsm"):
        return _xlsx_preview(target)
    if ext == ".pptx":
        rendered, plain = _pptx_preview(target)
        return rendered, plain, False
    if ext == ".odt":
        rendered, plain = _odt_preview(target)
        return rendered, plain, False
    if ext == ".ods":
        rendered, plain = _ods_preview(target)
        return rendered, plain, False
    raise ValueError(f"不支持的 office 格式: {ext}")


def _markdown_to_html(text: str) -> Optional[str]:
    try:
        import markdown  # type: ignore
    except ImportError:
        return None
    try:
        return f'<div class="pv-doc">{markdown.markdown(text, extensions=["tables", "fenced_code"])}</div>'
    except Exception:  # pragma: no cover - markdown 渲染失败时退回纯文本
        logger.warning("markdown 渲染失败", exc_info=True)
        return None


# ── 入口 ─────────────────────────────────────────────────────────────────────

def preview_file(ws_id: str, rel_path: str) -> Dict[str, Any]:
    """返回预览信息：kind / render / html / text / note_key。"""
    target = resolve_workspace_path(ws_id, rel_path)
    if not target.exists() or not target.is_file():
        raise WorkspacePathError(f"文件不存在: {rel_path}")

    ext = target.suffix.lower()
    size = target.stat().st_size
    result: Dict[str, Any] = {
        "path": safe_relpath(ws_id, target),
        "size": size,
        "ext": ext,
        "kind": "text",
        "render": "none",
        "html": "",
        "text": "",
        "note_key": "",
        "truncated": False,
    }

    if ext in HTML_EXT or ext in SVG_EXT:
        data = target.read_bytes()[:MAX_PREVIEW_BYTES]
        text = data.decode("utf-8", errors="replace")
        result["kind"] = "html"
        result["render"] = "iframe"
        result["html"] = text
        result["text"] = text[:MAX_TEXT_CHARS]
        result["truncated"] = size > MAX_PREVIEW_BYTES
        return result

    if ext in OFFICE_EXT:
        try:
            rendered, plain, rows_cut = _office_preview(ext, target)
        except zipfile.BadZipFile:
            result["kind"] = "binary"
            result["note_key"] = "preview.corrupt"
            return result
        except Exception:
            logger.warning("预览解析失败: %s", target, exc_info=True)
            result["kind"] = "binary"
            result["note_key"] = "preview.unavailable"
            return result
        result["kind"] = "office"
        result["render"] = "html"
        result["html"] = rendered
        result["text"] = plain[:MAX_TEXT_CHARS]
        result["truncated"] = rows_cut or len(plain) > MAX_TEXT_CHARS
        return result

    if ext in IMAGE_EXT:
        result["kind"] = "image"
        result["render"] = "image"
        return result

    if ext in PDF_EXT:
        result["kind"] = "pdf"
        result["render"] = "pdf"
        return result

    # 其它文件：能按文本读出来的就读，读不出来的提示下载。
    data = target.read_bytes()[:MAX_PREVIEW_BYTES]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    if "\ufffd" in text or "\x00" in text:
        result["kind"] = "binary"
        result["note_key"] = "preview.unavailable"
        return result

    result["kind"] = "markdown" if ext in MARKDOWN_EXT else "text"
    result["text"] = text[:MAX_TEXT_CHARS]
    result["truncated"] = size > MAX_PREVIEW_BYTES or len(text) > MAX_TEXT_CHARS
    if ext in MARKDOWN_EXT:
        rendered = _markdown_to_html(text[:MAX_TEXT_CHARS])
        if rendered:
            result["render"] = "html"
            result["html"] = rendered
    return result

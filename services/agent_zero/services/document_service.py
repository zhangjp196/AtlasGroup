"""Office / PDF 的文本抽取——附件上传与工作区文件工具共用同一份实现。

为什么单独抽出来：此前「附件里的 docx 能不能读」和「模型 read_file 能不能读
同一个 docx」是两套代码，行为必然分叉（一个能读、一个报「二进制文件」）。
这里只依赖标准库：

- docx / xlsx / xlsm / pptx / odt / ods：zip 里的 XML，按段落 / 工作表 / 幻灯片
  拆成 parts，模型可以只读某个工作表或某页；
- 旧版 doc / xls / ppt：依次试 textutil（macOS）→ antiword → catdoc → soffice，
  最后退回可读字符串兜底；
- pdf：优先 pypdf，其次 pdftotext，最后标准库解压内容流兜底（按页拆 parts）。

parts 是「逻辑分段」（工作表 / 幻灯片 / 页 / 整篇），不是物理行。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

OFFICE_ZIP_EXT = {".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods"}
LEGACY_OFFICE_EXT = {".doc", ".xls", ".ppt"}
PDF_EXT = {".pdf"}
DOCUMENT_EXT = OFFICE_ZIP_EXT | LEGACY_OFFICE_EXT | PDF_EXT

MAX_DOC_BYTES = 12 * 1024 * 1024
MAX_PART_CHARS = 400_000
MAX_TABLE_ROWS = 5000
MAX_TABLE_COLS = 60
MAX_PDF_PAGES = 400


class DocumentError(ValueError):
    """抽取失败——消息直接给模型看，必须写明下一步能做什么。"""


def is_document(name: Any) -> bool:
    ext = Path(str(name or "")).suffix.lower()
    return ext in DOCUMENT_EXT


def document_parts(path: Path) -> Dict[str, Any]:
    """返回 {kind, ext, parts:[{name, text}], note}。kind ∈ office / pdf。"""
    target = Path(path)
    ext = target.suffix.lower()
    if ext in OFFICE_ZIP_EXT:
        parts = _office_zip_parts(ext, target)
        return {"kind": "office", "ext": ext, "parts": parts, "note": ""}
    if ext in LEGACY_OFFICE_EXT:
        return {"kind": "office", "ext": ext,
                "parts": [{"name": "text", "text": _legacy_office_text(target, ext)}],
                "note": "旧版 Office 格式，文本抽取可能不完整"}
    if ext in PDF_EXT:
        return {"kind": "pdf", "ext": ext, "parts": _pdf_parts(target), "note": ""}
    raise DocumentError(f"不是可解析的文档: {target.name}")


def extract_text(path: Path) -> str:
    """整篇纯文本（附件注入上下文用）。"""
    return "\n\n".join(p["text"] for p in document_parts(path)["parts"]).strip()


def select_parts(parts: List[Dict[str, Any]], part: Optional[str]) -> List[Dict[str, Any]]:
    """按名称或 1 起序号挑 part；没有匹配时返回原列表（让模型看到全文而不是报错）。"""
    key = str(part or "").strip()
    if not key or len(parts) <= 1:
        return parts
    if key.isdigit():
        idx = int(key) - 1
        if 0 <= idx < len(parts):
            return [parts[idx]]
    low = key.lower()
    hits = [p for p in parts if p["name"].lower() == low
            or low in p["name"].lower() or p["name"].lower() in low]
    return hits or parts


# ── OOXML / ODF ─────────────────────────────────────────────────────────────

def _tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OD_DOC = "urn:oasis:names:tc:opendocument:xmlns:document:0.0"
OD_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:0.0"
OD_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:0.0"


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _rows_text(rows: List[List[str]]) -> str:
    rows = [r[:MAX_TABLE_COLS] for r in rows]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return "\n".join(" | ".join(r for r in row) for row in rows)


def _docx_parts(target: Path) -> List[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(target) as zf:
            root = ET.fromstring(zf.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise DocumentError(f"docx 结构异常（{e}）") from e

    body = root.find(_tag(W_NS, "body")) or root
    lines: List[str] = []
    for el in body:
        if el.tag == _tag(W_NS, "p"):
            text = "".join(
                ("\n" if node.tag == _tag(W_NS, "br") else (node.text or ""))
                for node in el.iter()
                if node.tag in (_tag(W_NS, "t"), _tag(W_NS, "br"))
            ).strip()
            if not text:
                continue
            style = ""
            ppr = el.find(_tag(W_NS, "pPr"))
            if ppr is not None:
                pstyle = ppr.find(_tag(W_NS, "pStyle"))
                if pstyle is not None:
                    style = (pstyle.get(_tag(W_NS, "val")) or "").lower()
            lines.append(f"## {text}" if style.startswith("heading") else text)
        elif el.tag == _tag(W_NS, "tbl"):
            rows: List[List[str]] = []
            for tr in el.findall(_tag(W_NS, "tr")):
                cells = []
                for tc in tr.findall(_tag(W_NS, "tc")):
                    cells.append(_clean(" ".join(
                        "".join(node.text or "" for node in p.iter()
                                 if node.tag == _tag(W_NS, "t"))
                        for p in tc.findall(_tag(W_NS, "p"))
                    )))
                rows.append(cells)
            if rows:
                lines.append(_rows_text(rows))
        if len("\n".join(lines)) > MAX_PART_CHARS:
            break
    if not lines:
        raise DocumentError("docx 里没有可提取的文本")
    return [{"name": "正文", "text": "\n".join(lines)}]


def _xlsx_parts(target: Path) -> List[Dict[str, Any]]:
    try:
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

            parts: List[Dict[str, Any]] = []
            for sheet in workbook.iter(_tag(S_NS, "sheet")):
                rel = (rel_map.get(sheet.get(_tag(R_NS, "id"))) or "").lstrip("/")
                path = rel if rel.startswith("xl/") else f"xl/{rel}"
                if path not in names:
                    continue
                rows: List[List[str]] = []
                for tr in ET.fromstring(zf.read(path)).iter(_tag(S_NS, "row")):
                    cells: List[str] = []
                    for cell in tr.findall(_tag(S_NS, "c")):
                        ctype = cell.get("t") or ""
                        if ctype == "inlineStr":
                            value = "".join(t.text or "" for t in cell.iter(_tag(S_NS, "t")))
                        else:
                            v = cell.find(_tag(S_NS, "v"))
                            raw = (v.text or "") if v is not None else ""
                            value = shared[int(raw)] if (ctype == "s" and raw.isdigit()) else raw
                        cells.append(value)
                    if any(c.strip() for c in cells):
                        rows.append(cells)
                    if len(rows) >= MAX_TABLE_ROWS:
                        break
                if rows:
                    parts.append({"name": sheet.get("name") or f"Sheet {len(parts) + 1}",
                                  "text": _rows_text(rows)})
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise DocumentError(f"xlsx 结构异常（{e}）") from e
    if not parts:
        raise DocumentError("工作表里没有可提取的文本")
    return parts


def _pptx_parts(target: Path) -> List[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(target) as zf:
            slides = sorted(
                (n for n in zf.namelist()
                 if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                key=lambda n: int(n[len("ppt/slides/slide"): -4] or 0),
            )
            parts: List[Dict[str, Any]] = []
            for idx, name in enumerate(slides, start=1):
                content = ET.fromstring(zf.read(name)).find(_tag(P_NS, "content"))
                if content is None:
                    continue
                lines: List[str] = []
                for el in content:
                    if el.tag == _tag(P_NS, "sp"):
                        for run in el.iter(_tag(A_NS, "t")):
                            text = _clean(run.text or "")
                            if text:
                                lines.append(text)
                    elif el.tag == _tag(P_NS, "graphicFrame"):
                        rows = [[_clean("".join(t.text or "" for t in tc.iter(_tag(A_NS, "t"))))
                                 for tc in tr.findall(_tag(A_NS, "tc"))]
                                for tr in el.iter(_tag(A_NS, "tr"))]
                        if rows:
                            lines.append(_rows_text(rows))
                if lines:
                    parts.append({"name": f"Slide {idx}", "text": "\n".join(lines)})
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise DocumentError(f"pptx 结构异常（{e}）") from e
    if not parts:
        raise DocumentError("幻灯片里没有可提取的文本")
    return parts


def _odt_parts(target: Path) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(zipfile.ZipFile(target).read("content.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise DocumentError(f"odt 结构异常（{e}）") from e

    parents = {child: el for el in root.iter() for child in el}

    def _in_table(el) -> bool:
        cur = parents.get(el)
        while cur is not None:
            if cur.tag in (_tag(OD_TABLE, "table"), _tag(OD_TABLE, "cell")):
                return True
            cur = parents.get(cur)
        return False

    lines: List[str] = []
    for el in root.iter():
        if _in_table(el):
            continue
        if el.tag in (_tag(OD_TEXT, "h"), _tag(OD_DOC, "h")):
            raw = el.get(_tag(OD_TEXT, "level")) or el.get(_tag(OD_DOC, "level")) or "1"
            level = int(raw) if str(raw).isdigit() else 1
            text = _clean("".join(el.itertext()))
            if text:
                lines.append(f"{'#' * min(6, level + 1)} {text}")
        elif el.tag in (_tag(OD_TEXT, "p"), _tag(OD_DOC, "p")):
            text = _clean("".join(el.itertext()))
            if text:
                lines.append(text)
    for table in root.iter(_tag(OD_TABLE, "table")):
        rows = [["".join(tc.itertext()).strip()
                for tc in tr.findall(_tag(OD_TABLE, "table-cell"))]
                for tr in table.findall(_tag(OD_TABLE, "table-row"))]
        if rows:
            lines.append(_rows_text(rows))
    if not lines:
        raise DocumentError("odt 里没有可提取的文本")
    return [{"name": "正文", "text": "\n".join(lines)}]


def _ods_parts(target: Path) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(zipfile.ZipFile(target).read("content.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise DocumentError(f"ods 结构异常（{e}）") from e

    parts: List[Dict[str, Any]] = []
    for table in root.iter(_tag(OD_TABLE, "table")):
        rows = [["".join(tc.itertext()).strip()
                for tc in tr.findall(_tag(OD_TABLE, "table-cell"))]
                for tr in table.findall(_tag(OD_TABLE, "table-row"))]
        rows = rows[:MAX_TABLE_ROWS]
        if rows:
            parts.append({"name": table.get(_tag(OD_TABLE, "name")) or f"Sheet {len(parts) + 1}",
                          "text": _rows_text(rows)})
    if not parts:
        raise DocumentError("ods 里没有可提取的文本")
    return parts


def _office_zip_parts(ext: str, target: Path) -> List[Dict[str, Any]]:
    if ext == ".docx":
        return _docx_parts(target)
    if ext in (".xlsx", ".xlsm"):
        return _xlsx_parts(target)
    if ext == ".pptx":
        return _pptx_parts(target)
    if ext == ".odt":
        return _odt_parts(target)
    if ext == ".ods":
        return _ods_parts(target)
    raise DocumentError(f"不支持的 office 格式: {ext}")


# ── 旧版 Office ─────────────────────────────────────────────────────────────

def _run(cmd: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        return (int(proc.returncode),
                proc.stdout.decode("utf-8", errors="replace"),
                proc.stderr.decode("utf-8", errors="replace"))
    except Exception as e:  # pragma: no cover - 命令不存在 / 超时
        return 1, "", f"{type(e).__name__}: {e}"


def _binary_strings(path: Path) -> str:
    """从旧版二进制文档里捞可读字符串（最后兜底）。"""
    data = path.read_bytes()[:MAX_DOC_BYTES]
    out: List[str] = []
    seen: set = set()

    def _add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) < 4 or value in seen:
            return
        if sum(ch.isprintable() for ch in value) / len(value) < 0.9:
            return
        seen.add(value)
        out.append(value)

    for m in re.finditer(rb"[\x20-\x7e]{4,}", data):
        _add(m.group(0).decode("latin-1", errors="ignore"))
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", data):  # UTF-16LE
        _add(m.group(0).decode("utf-16le", errors="ignore"))
    return "\n".join(out)


def _legacy_office_text(path: Path, ext: str) -> str:
    errors: List[str] = []
    attempts: List[List[str]] = []
    if shutil.which("textutil"):
        attempts.append(["textutil", "-convert", "txt", "-stdout", str(path)])
    if shutil.which("antiword"):
        attempts.append(["antiword", str(path)])
    if shutil.which("catdoc"):
        attempts.append(["catdoc", str(path)])
    for cmd in attempts:
        code, out, err = _run(cmd)
        if code == 0 and out.strip():
            return out
        if err.strip():
            errors.append(err.strip()[:200])

    if shutil.which("soffice"):
        with tempfile.TemporaryDirectory(prefix="az-office-") as td:
            target_fmt = "csv" if ext == ".xls" else "txt:Text"
            code, _out, err = _run([
                "soffice", "--headless", "--nologo", "--nodefault", "--norestore",
                "--nolockcheck", f"-env:UserInstallation=file://{Path(td) / 'profile'}",
                "--convert-to", target_fmt, "--outdir", td, str(path),
            ], timeout=90)
            converted = Path(td) / f"{path.stem}.{'csv' if ext == '.xls' else 'txt'}"
            if code == 0 and converted.is_file():
                text = converted.read_text("utf-8", errors="replace")
                if text.strip():
                    return text
            if err.strip():
                errors.append(err.strip()[:200])

    text = _binary_strings(path)
    if text.strip():
        logger.info("旧版 Office 文档按可读字符串兜底提取: %s (%s)", path.name, ext)
        return text
    raise DocumentError(
        "未能从文档中提取文本"
        + (f"：{errors[-1]}" if errors else "（可安装 antiword / catdoc 或 LibreOffice）")
    )


# ── PDF ─────────────────────────────────────────────────────────────────────

def _pdf_parts(path: Path) -> List[Dict[str, Any]]:
    # 1) pypdf（可选依赖）——按页拆，最准
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        parts: List[Dict[str, Any]] = []
        for i, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
            text = (page.extract_text() or "").strip()
            if text:
                parts.append({"name": f"Page {i}", "text": text})
        if parts:
            if len(reader.pages) > MAX_PDF_PAGES:
                parts.append({"name": "note",
                              "text": f"（共 {len(reader.pages)} 页，只抽取前 {MAX_PDF_PAGES} 页）"})
            return parts
    except Exception:
        logger.debug("pypdf 解析失败，尝试命令行", exc_info=True)

    # 2) poppler 的 pdftotext
    if shutil.which("pdftotext"):
        code, out, err = _run(["pdftotext", "-layout", str(path), "-"])
        if code == 0 and out.strip():
            return [{"name": "text", "text": out.strip()}]
        if err.strip():
            logger.debug("pdftotext 解析失败: %s", err[:200])

    # 3) 标准库兜底：解压 FlateDecode 内容流，抽文本操作符里的字符串
    data = path.read_bytes()[:MAX_DOC_BYTES]
    pieces: List[str] = []

    def _unescape(value: bytes) -> str:
        value = re.sub(
            rb"\\([nrtbf()\\])",
            lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                       b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}[m.group(1)],
            value,
        )
        value = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8)]), value)
        return value.decode("latin-1", errors="ignore")

    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        for s in re.finditer(rb"\((.*?)(?<!\\)\)\s*(?:Tj|TJ|')", raw, re.S):
            text = _unescape(s.group(1)).strip()
            if text:
                pieces.append(text)
        for s in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*Tj", raw):
            try:
                pieces.append(bytes.fromhex(re.sub(rb"\s+", b"", s.group(1)).decode("ascii"))
                              .decode("utf-16-be", errors="ignore"))
            except Exception:
                continue

    text = "\n".join(p for p in pieces if p.strip())
    if text.strip():
        return [{"name": "text", "text": text}]
    raise DocumentError(
        "未能从 PDF 中提取文本（可安装 pypdf 或 poppler 的 pdftotext 以提高质量）"
    )

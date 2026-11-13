"""附件预览：会话区按类型渲染 Markdown / HTML / 文本等。"""
from __future__ import annotations

import base64


def _upload(monkeypatch, tmp_path, conv_id, name, mime, content):
    from services.agent_zero.services import attachment_service

    monkeypatch.setattr(attachment_service, "_uploads_root", lambda: tmp_path / "uploads")
    raw = content.encode("utf-8") if isinstance(content, str) else content
    data = base64.b64encode(raw).decode("ascii")
    return attachment_service.process_uploads(
        conv_id, [{"name": name, "mime": mime, "data": data}], allow_images=True,
    )[0]


def test_text_and_markdown_preview(client, monkeypatch, tmp_path):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]
    att = _upload(monkeypatch, tmp_path, conv["id"], "notes.md", "text/markdown", "# 标题\n\n- 一\n- 二")
    res = client.get(
        f"/api/agent-zero/conversations/{conv['id']}/attachments/{att['id']}/preview"
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["kind"] == "text"
    assert "# 标题" in data["text"]
    assert data["url"].endswith(f"/attachments/{att['id']}")
    assert data["name"] == "notes.md"


def test_html_preview_keeps_source_safe(client, monkeypatch, tmp_path):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]
    html = "<h1>Hello</h1><script>alert(1)</script>"
    att = _upload(monkeypatch, tmp_path, conv["id"], "page.html", "text/html", html)
    data = client.get(
        f"/api/agent-zero/conversations/{conv['id']}/attachments/{att['id']}/preview"
    ).json()["data"]
    # 后端只返回文本；前端用 sandbox="" 的 srcdoc 渲染，脚本不会执行。
    assert "<h1>Hello</h1>" in data["text"]
    assert data["kind"] == "text"
    assert data["html"] == html


def test_svg_preview_is_available_but_raw_attachment_downloads(client, monkeypatch, tmp_path):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>SVG</text></svg>'
    att = _upload(monkeypatch, tmp_path, conv["id"], "graphic.svg", "image/svg+xml", svg)
    url = f"/api/agent-zero/conversations/{conv['id']}/attachments/{att['id']}"
    assert client.get(url + "/preview").json()["data"]["html"] == svg
    assert client.get(url).headers["content-disposition"].startswith("attachment")


def test_standard_pptx_attachment_displays_slide_text(client, monkeypatch, tmp_path):
    from io import BytesIO
    from zipfile import ZipFile

    archive = BytesIO()
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("ppt/slides/slide1.xml", """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Slide text</a:t></a:r></a:p>
              </p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
        """)
    cid = client.post("/api/agent-zero/conversations", json={}).json()["data"]["id"]
    att = _upload(monkeypatch, tmp_path, cid, "slides.pptx", "", archive.getvalue())
    url = f"/api/agent-zero/conversations/{cid}/attachments/{att['id']}/preview"
    data = client.get(url).json()["data"]
    assert att["text_chars"] > 0
    assert data["kind"] == "office"
    assert "Slide text" in data["html"]


def test_preview_missing_attachment(client):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]
    res = client.get(
        f"/api/agent-zero/conversations/{conv['id']}/attachments/att-0000000000000000/preview"
    )
    assert res.status_code == 404

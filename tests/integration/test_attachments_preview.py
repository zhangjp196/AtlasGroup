"""附件预览：会话区按类型渲染 Markdown / HTML / 文本等。"""
from __future__ import annotations

import base64


def _upload(monkeypatch, tmp_path, conv_id, name, mime, text):
    from services.agent_zero.services import attachment_service

    monkeypatch.setattr(attachment_service, "_uploads_root", lambda: tmp_path / "uploads")
    data = base64.b64encode(text.encode("utf-8")).decode("ascii")
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


def test_preview_missing_attachment(client):
    conv = client.post("/api/agent-zero/conversations", json={}).json()["data"]
    res = client.get(
        f"/api/agent-zero/conversations/{conv['id']}/attachments/att-0000000000000000/preview"
    )
    assert res.status_code == 404

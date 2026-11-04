"""头像上传 / 读取（角色、部门、群组共用）。"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from services.agent_zero.services import avatar_service

router = APIRouter()


@router.post("/avatars")
async def upload_avatar(scope: str = Form(...), file: UploadFile = File(...)):
    try:
        data = await file.read()
        out = avatar_service.save_avatar(
            scope, data, filename=file.filename or "",
            content_type=file.content_type or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": out}


@router.get("/avatars/{scope}/{filename}")
def get_avatar(scope: str, filename: str):
    hit = avatar_service.resolve_avatar(scope, filename)
    if not hit:
        raise HTTPException(status_code=404, detail="头像不存在")
    path, mime = hit
    return FileResponse(
        str(path), media_type=mime,
        headers={
            # 文件名带随机串 → 内容不会变，可以长期缓存。
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
            # SVG 也用 <img> 加载；再叠一层 CSP，防止直接导航时执行脚本。
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )

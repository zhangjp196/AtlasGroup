"""Magpie 本机脱敏：设置、状态与预览。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.agent_zero.services import magpie_service

router = APIRouter()


class MagpieSettingsBody(BaseModel):
    enabled: Optional[bool] = None
    secrets: Optional[bool] = None
    pii: Optional[bool] = None
    paths: Optional[bool] = None


class MagpieTestBody(BaseModel):
    text: str = Field(..., max_length=200_000)


@router.get("/app/magpie")
def get_magpie():
    return {"code": 0, "data": magpie_service.status()}


@router.put("/app/magpie/settings")
def put_magpie_settings(body: MagpieSettingsBody):
    magpie_service.save_settings(body.model_dump(exclude_unset=True))
    return {"code": 0, "data": magpie_service.status()}


@router.post("/app/magpie/test")
def test_magpie(body: MagpieTestBody):
    return {"code": 0, "data": magpie_service.test_redact(body.text)}

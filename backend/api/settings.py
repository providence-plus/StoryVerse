"""设置接口。API Key 只进不出：返回给前端时永远是掩码。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from backend.core import settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsPayload(BaseModel):
    """允许前端直接提交扁平结构，也允许包一层 llm。"""

    model_config = ConfigDict(extra="allow")

    llm: dict[str, Any] | None = None


@router.get("")
def get_settings():
    return settings_service.public_settings()


@router.put("")
def update_settings(payload: SettingsPayload):
    data = payload.model_dump(exclude_none=True)
    return settings_service.save_settings(data)


@router.post("/test-connection")
def test_connection():
    return settings_service.test_connection()

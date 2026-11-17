"""存档接口：列表、手动创建、读取旧存档。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from backend.core import save_manager, world_manager

router = APIRouter(prefix="/api/worlds/{world_id}/saves", tags=["saves"])


class CreateSaveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "新存档"


@router.get("")
def list_saves(world_id: str):
    world_manager.validate_world_id(world_id)
    return {"saves": save_manager.list_saves(world_id)}


@router.post("")
def create_save(world_id: str, payload: CreateSaveRequest):
    world_manager.validate_world_id(world_id)
    return save_manager.create_save(world_id, payload.name.strip() or "新存档")


@router.post("/{save_id}/load")
def load_save(world_id: str, save_id: str):
    world_manager.validate_world_id(world_id)
    return save_manager.load_save(world_id, save_id)

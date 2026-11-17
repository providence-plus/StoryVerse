"""世界管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.core import customization, file_utils, panel_service, prompt_builder, world_generator, world_manager
from backend.models.world import CreateWorldRequest, UpdateWorldRequest

router = APIRouter(prefix="/api/worlds", tags=["worlds"])


class GenerateWorldRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str
    genre: str = ""


@router.get("")
def list_worlds():
    world_manager.ensure_demo_world()
    return {"worlds": [w.model_dump() for w in world_manager.list_worlds()]}


@router.post("/generate")
def generate_world(request: GenerateWorldRequest):
    """一句话扩写成世界观预览。只返回数据，不创建世界、不写任何文件。"""
    return world_generator.generate_preview(request.description, request.genre)


@router.post("/confirm")
def confirm_generated_world(payload: dict):
    """一句话生成流程的确认落盘：再次校验用户编辑过的预览，然后才创建目录树。"""
    return world_generator.confirm_world(payload).model_dump()


@router.post("")
def create_world(request: CreateWorldRequest):
    """手动 / 自定义「不调用 AI」创建世界。writer_style 会写进该世界的 system_prompt.md。"""
    return world_manager.create_world(request).model_dump()


@router.post("/customize-preview")
def customize_world_preview(payload: dict):
    """玩家逐条填了世界草稿 -> 一次模型调用补全 -> 只返回可编辑预览，不写文件。"""
    return customization.generate_world_preview(payload)


class WorldSystemPromptPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # 防止把超长文本粘进来，撑爆模型服务的上下文窗口。
    content: str = Field(default="", max_length=20000)


def _checked_world_id(world_id: str) -> str:
    """新接口自己把非法 ID 转成 400，不让 ValueError 变成 500。"""
    try:
        return world_manager.validate_world_id(world_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{world_id}/system-prompt")
def get_world_system_prompt(world_id: str):
    """读世界专属写手风格；文件不存在时返回空串。"""
    _checked_world_id(world_id)
    return {
        "world_id": world_id,
        "content": prompt_builder.read_world_system_prompt(world_id),
        "global_size": len(prompt_builder.read_prompt("system_prompt")),
    }


@router.put("/{world_id}/system-prompt")
def save_world_system_prompt(world_id: str, payload: WorldSystemPromptPayload):
    """原子写入。下一次模型调用会重新读盘，立即生效，不需要重启或退出游戏。"""
    _checked_world_id(world_id)
    path = world_manager.resolve(world_id, "system_prompt.md")
    file_utils.atomic_write_text(path, payload.content)
    return {"saved": world_id, "size": len(payload.content)}


@router.get("/{world_id}")
def get_world(world_id: str):
    world_manager.validate_world_id(world_id)
    meta = world_manager.get_meta(world_id)
    data = meta.model_dump()
    # 旧世界没有 story_started 字段，但 turn > 0 说明已经玩过，前端同样按「已开局」处理。
    if not data.get("story_started"):
        current = file_utils.read_json(world_manager.resolve(world_id, "state", "current.json"), {}) or {}
        data["story_started"] = int(current.get("turn") or 0) > 0
    return {**data, "panels": panel_service.panels_for_frontend(world_id)}


@router.put("/{world_id}")
def update_world(world_id: str, request: UpdateWorldRequest):
    return world_manager.update_world(world_id, request).model_dump()


@router.post("/{world_id}/start")
def start_world(world_id: str):
    """玩家点「开始该世界的故事」：只切状态、不调模型，幂等。"""
    _checked_world_id(world_id)
    return world_manager.set_story_started(world_id, True).model_dump()


@router.delete("/{world_id}")
def delete_world(world_id: str):
    world_manager.delete_world(world_id)
    return {"deleted": world_id}

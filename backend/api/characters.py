"""角色接口：预览、确认保存、手动改值、字段回填与校准。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.core import (
    bible_service,
    character_generator,
    customization,
    panel_service,
    state_manager,
    world_manager,
)

router = APIRouter(prefix="/api/worlds/{world_id}", tags=["characters"])


def _checked_world_id(world_id: str) -> str:
    """非法 world_id 转成 400，不让 ValueError 变成 500。"""
    try:
        return world_manager.validate_world_id(world_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _ensure_not_started(world_id: str) -> None:
    """开局后人物上锁：编辑 / 物理删除不再允许，只留「退场」。"""
    if state_manager.is_story_started(world_id):
        raise HTTPException(
            status_code=409,
            detail="故事已经开始，人物设定已锁定。如需让角色不再出现，请用「退场」。",
        )


class GenerateCharacterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str
    count: int = 1
    is_protagonist: bool = False
    auto_relations: bool = False


class ConfirmCharactersRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    characters: list[dict[str, Any]]
    is_protagonist: bool = False


class FieldValueRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field_id: str
    value: Any = None


class CustomizeCharacterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    inputs: dict[str, Any]
    is_protagonist: bool = False


class PlannedEventRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    done: bool = True


class EditCharacterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    core_description: str = ""
    bible: dict[str, Any] = Field(default_factory=dict)


class RetireCharacterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    retired: bool = True


@router.get("/characters")
def list_characters(world_id: str):
    _checked_world_id(world_id)
    return {
        "characters": [c.model_dump() for c in state_manager.list_characters(world_id)],
        "panels": panel_service.panels_for_frontend(world_id),
    }


@router.get("/planned-events")
def list_planned_events(world_id: str):
    """给人物列表显示「预定事件 + 是否已标记完成」。完成态来自 current.json。"""
    _checked_world_id(world_id)
    state = state_manager.get_current_state(world_id)
    completed = [str(x) for x in (state.completed_planned_events or [])]
    return {
        # 列表页要显示玩家原文，不能用注入剧情时的 160 字裁剪版。
        "events": bible_service.collect_planned_events(world_id, None, [], for_prompt=False),
        "completed": completed,
    }


@router.get("/characters/{character_id}")
def get_character(world_id: str, character_id: str):
    _checked_world_id(world_id)
    return state_manager.get_character(world_id, character_id).model_dump()


@router.post("/generate-character")
def generate_character(world_id: str, payload: GenerateCharacterRequest):
    """只产出预览，不写任何正式角色文件。"""
    _checked_world_id(world_id)
    return character_generator.generate_preview(
        world_id=world_id,
        description=payload.description,
        count=payload.count,
        is_protagonist=payload.is_protagonist,
        auto_relations=payload.auto_relations,
    )


@router.post("/customize-character-preview")
def customize_character_preview(world_id: str, payload: CustomizeCharacterRequest):
    """玩家逐条填了人物草稿 -> 一次模型调用补全 -> 只返回可编辑预览，不写文件。"""
    _checked_world_id(world_id)
    return customization.generate_character_preview(
        world_id=world_id,
        inputs=payload.inputs,
        is_protagonist=payload.is_protagonist,
    )


@router.patch("/characters/{character_id}/planned-events/{event_id}")
def set_planned_event_done(world_id: str, character_id: str, event_id: str, payload: PlannedEventRequest):
    """标记预定事件已完成 / 重新开启。完成态存 current.json，随存档分支走。

    character_id 不是装饰：必须属于本世界，且这条事件确实挂在它名下。
    否则可以拿别的角色 id 去改他人事件的完成态，或跨世界误操作。
    """
    _checked_world_id(world_id)
    if character_id not in state_manager.list_character_ids(world_id):
        raise HTTPException(status_code=404, detail=f"角色 {character_id} 不属于世界 {world_id}")
    owned = {
        evt["id"] for evt in bible_service.collect_planned_events(world_id, None, [])
        if evt.get("owner_id") == character_id
    }
    if event_id not in owned:
        raise HTTPException(status_code=404, detail=f"预定事件 {event_id} 不属于角色 {character_id}")
    try:
        state = state_manager.set_planned_event_done(world_id, event_id, payload.done)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"event_id": event_id, "done": payload.done, "completed": state.completed_planned_events}


@router.post("/characters")
def confirm_characters(world_id: str, payload: ConfirmCharactersRequest):
    """用户在预览里编辑并点确认之后才会落盘。"""
    _checked_world_id(world_id)
    saved = character_generator.confirm_characters(world_id, payload.characters, payload.is_protagonist)
    return {"saved": saved, "panels": panel_service.panels_for_frontend(world_id)}


@router.put("/characters/{character_id}")
def edit_character(world_id: str, character_id: str, payload: EditCharacterRequest):
    """编辑已有角色的固定设定（name / core_description / bible）。只在未开局时允许。"""
    _checked_world_id(world_id)
    _ensure_not_started(world_id)
    if character_id not in state_manager.list_character_ids(world_id):
        raise HTTPException(status_code=404, detail=f"角色 {character_id} 不属于世界 {world_id}")
    return state_manager.edit_character(world_id, character_id, payload.model_dump()).model_dump()


@router.delete("/characters/{character_id}")
def delete_character(world_id: str, character_id: str):
    """物理删除角色并清理所有指向它的引用。只在未开局时允许。"""
    _checked_world_id(world_id)
    _ensure_not_started(world_id)
    if character_id not in state_manager.list_character_ids(world_id):
        raise HTTPException(status_code=404, detail=f"角色 {character_id} 不属于世界 {world_id}")
    state_manager.delete_character(world_id, character_id)
    return {"deleted": character_id}


@router.post("/characters/{character_id}/retire")
def retire_character(world_id: str, character_id: str, payload: RetireCharacterRequest):
    """开局后让角色退场 / 恢复。写 state.json，随存档走，历史与文件保留。"""
    _checked_world_id(world_id)
    if character_id not in state_manager.list_character_ids(world_id):
        raise HTTPException(status_code=404, detail=f"角色 {character_id} 不属于世界 {world_id}")
    return state_manager.set_retired(world_id, character_id, payload.retired).model_dump()


@router.patch("/characters/{character_id}/fields")
def set_field_value(world_id: str, character_id: str, payload: FieldValueRequest):
    _checked_world_id(world_id)
    return state_manager.set_field_value(world_id, character_id, payload.field_id, payload.value).model_dump()


@router.post("/characters/{character_id}/fields/backfill")
def backfill_fields(world_id: str, character_id: str):
    """新增字段后按历史补齐，任意轮次都可以调用，不依赖固定轮数。"""
    _checked_world_id(world_id)
    return character_generator.backfill_fields(world_id, character_id)


@router.post("/characters/{character_id}/fields/reconcile")
def reconcile_fields(world_id: str, character_id: str):
    _checked_world_id(world_id)
    return character_generator.reconcile_fields(world_id, character_id)


@router.get("/ui/character-raw")
def raw_character_files(world_id: str):
    """给调试用：返回人物文件在磁盘上的位置，方便用户手动编辑。"""
    _checked_world_id(world_id)
    root = world_manager.world_dir(world_id)
    return {"root": str(root), "files": [str(p.relative_to(root)) for p in (root / "characters").rglob("*.json")]}

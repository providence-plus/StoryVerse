"""Panel Designer 与字段建议接口。

panels.yaml 的写权限只在这里开放，且请求必须来自用户在前端的编辑操作；
LLM 的输出永远走 story_engine -> Validator，不经过此模块。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.core import (
    character_generator,
    field_suggestions,
    panel_service,
    save_manager,
    state_manager,
    world_manager,
)

router = APIRouter(prefix="/api/worlds/{world_id}", tags=["panels"])


class PanelPayload(BaseModel):
    # 外部请求体仍用 schema 这个键，内部改名避免与 BaseModel.schema 冲突。
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    panels: dict[str, Any] = Field(alias="schema")


@router.get("/ui/panels")
def get_panels(world_id: str):
    world_manager.validate_world_id(world_id)
    return panel_service.panels_for_frontend(world_id)


@router.put("/ui/panels")
def save_panels(world_id: str, payload: PanelPayload):
    """保存字段定义；若本次新增了字段，再尽力按已有剧情补齐初始值。

    关键顺序：schema 先落盘，再尝试回填。**回填永远是可选增强**，模型没接通、
    返回坏 JSON、超时都不允许让「用户刚保存的字段」看起来失败——这也是
    「新增字段后刷新看不到」最容易踩的坑：请求报错，前端就不会刷新本地状态。
    """
    world_manager.validate_world_id(world_id)
    old = panel_service.load_schema(world_id)
    old_ids = set(old.all_field_ids("__proto__", True)) | set(old.all_field_ids("__npc__", False))
    warnings: list[str] = []
    new_schema = panel_service.save_schema(world_id, payload.panels, warnings)
    new_ids = set(new_schema.all_field_ids("__proto__", True)) | set(new_schema.all_field_ids("__npc__", False))
    added = sorted(new_ids - old_ids)

    backfilled: dict[str, Any] = {}
    if added:
        # 事件触发：新增字段不需要等到某个固定轮数，立刻按历史回填。
        try:
            character_generator._mark_reconcile_due(world_id)
        except Exception as exc:  # noqa: BLE001 - 校准标记失败不影响字段保存
            warnings.append(f"字段校准状态更新失败（不影响保存）：{type(exc).__name__}")
        for cid in state_manager.list_character_ids(world_id):
            try:
                outcome = character_generator.backfill_fields(world_id, cid)
            except Exception as exc:  # noqa: BLE001 - 单个角色回填失败不拖垮保存
                outcome = {"applied": {}, "error": f"{type(exc).__name__}: {exc}"}
                warnings.append(f"角色 {cid} 的新字段自动补齐失败，可在人物页手动点「补齐」：{type(exc).__name__}")
            else:
                if outcome.get("error"):
                    warnings.append(f"角色 {cid} 的新字段自动补齐未完成，可在人物页手动点「补齐」：{outcome['error']}")
            backfilled[cid] = outcome
    try:
        save_manager.auto_save(world_id)
    except Exception as exc:  # noqa: BLE001 - 自动存档失败不影响字段定义已落盘
        warnings.append(f"自动存档失败（不影响保存）：{type(exc).__name__}")

    if added:
        warnings.insert(0, f"已写入 {len(added)} 个新字段：{', '.join(added)}")
    return {
        "panels": panel_service.panels_for_frontend(world_id),
        "added_fields": added,
        "backfilled": backfilled,
        "warnings": warnings,
    }


@router.get("/field-suggestions")
def get_suggestions(world_id: str):
    world_manager.validate_world_id(world_id)
    return {"items": [i.model_dump() for i in field_suggestions.load_items(world_id)]}


@router.post("/field-suggestions/{field_id}/adopt")
def adopt_suggestion(world_id: str, field_id: str):
    world_manager.validate_world_id(world_id)
    return character_generator.adopt_suggestion(world_id, field_id)


@router.post("/field-suggestions/{field_id}/ignore")
def ignore_suggestion(world_id: str, field_id: str):
    world_manager.validate_world_id(world_id)
    if not field_suggestions.set_status(world_id, field_id, "ignored"):
        raise ValueError(f"没有待处理的字段建议: {field_id}")
    return {"ignored": field_id}

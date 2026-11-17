"""字段建议：LLM 想新增字段时「记录并攒批提示」，但绝不自动改 schema。

设计约束（用户明确要求）：可以告诉我，但不要每轮都烦我。
因此这里只做记录与节流判断，前端只在角标上显示，不弹窗打断阅读。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core import file_utils, world_manager
from backend.models.field import FIELD_ID_PATTERN, FieldDefinition, FieldSuggestion, FieldType, PanelSchema
from backend.models.story import SchemaSuggestion


def _path(world_id: str) -> Path:
    return world_manager.resolve(world_id, "state", "field_suggestions.json")


def load_items(world_id: str) -> list[FieldSuggestion]:
    data = file_utils.read_json(_path(world_id), default={}) or {}
    items: list[FieldSuggestion] = []
    for raw in data.get("items", []) or []:
        try:
            items.append(FieldSuggestion(**raw))
        except (TypeError, ValueError):
            continue
    return items


def _save_items(world_id: str, items: list[FieldSuggestion]) -> None:
    file_utils.atomic_write_json(
        _path(world_id),
        {"items": [item.model_dump(mode="json") for item in items]},
    )


def _upsert(items: list[FieldSuggestion], **kwargs: Any) -> None:
    """同一字段键只累加出现次数，不重复堆条目。"""
    field_id = kwargs["field_id"]
    for item in items:
        if item.field_id == field_id and item.status == "pending":
            item.last_turn = kwargs.get("turn", item.last_turn)
            item.occurrences += 1
            if kwargs.get("example") not in (None, ""):
                item.example = kwargs["example"]
            return
    items.append(FieldSuggestion(
        field_id=field_id,
        label=kwargs.get("label", ""),
        type=kwargs.get("type", "text"),
        reason=kwargs.get("reason", ""),
        example=kwargs.get("example"),
        source_character_id=kwargs.get("character_id", ""),
        first_turn=kwargs.get("turn", 0),
        last_turn=kwargs.get("turn", 0),
    ))


def record(
    world_id: str,
    explicit: list[SchemaSuggestion],
    unknown_keys: list[tuple[str, str, Any]],
    turn: int,
    schema: PanelSchema,
    character_kinds: dict[str, bool],
) -> None:
    """记录两类意图：模型显式给的 schema_suggestions，以及越界的 custom_fields 键。

    只有确实不在任何角色 schema 里的键才会被记录，避免把笔误也当成建议。
    """
    all_known: set[str] = set()
    for cid, is_proto in character_kinds.items():
        all_known |= schema.all_field_ids(cid, is_proto)

    items = load_items(world_id)
    changed = False
    for sug in explicit:
        fid = sug.field_id.strip()
        if not FIELD_ID_PATTERN.match(fid) or fid in all_known:
            continue
        _upsert(items, field_id=fid, label=sug.label or fid, type=sug.type,
                reason=sug.reason, example=sug.example, turn=turn)
        changed = True
    for cid, fid, value in unknown_keys:
        if not FIELD_ID_PATTERN.match(fid) or fid in all_known:
            continue
        _upsert(items, field_id=fid, label=fid, reason="模型在角色更新里提交了未定义的字段",
                example=value, character_id=cid, turn=turn)
        changed = True
    if changed:
        _save_items(world_id, items)


def pending_items(world_id: str) -> list[FieldSuggestion]:
    return [i for i in load_items(world_id) if i.status == "pending"]


def should_notify(world_id: str, current_turn: int, config: dict[str, Any]) -> bool:
    """攒够阈值 + 全局节流，两个条件同时满足才亮角标。"""
    threshold = int(config.get("suggestion_notify_threshold", 3))
    every = int(config.get("suggestion_notify_every_turns", 10))
    if len(pending_items(world_id)) < threshold:
        return False
    state = file_utils.read_json(world_manager.resolve(world_id, "state", "current.json"), {}) or {}
    last = int(state.get("last_suggestion_notify_turn", 0) or 0)
    return current_turn - last >= every


def mark_notified(world_id: str, turn: int) -> None:
    path = world_manager.resolve(world_id, "state", "current.json")
    state = file_utils.read_json(path, {}) or {}
    state["last_suggestion_notify_turn"] = turn
    file_utils.atomic_write_json(path, state)


def set_status(world_id: str, field_id: str, status: str) -> bool:
    """status: ignored | adopted。忽略过的键永不再提示。"""
    items = load_items(world_id)
    for item in items:
        if item.field_id == field_id:
            item.status = status
            _save_items(world_id, items)
            return True
    return False


def build_definition(item: FieldSuggestion) -> FieldDefinition:
    """把建议转成可写进 panels.yaml 的字段定义。只有用户点「采纳」才会用到。"""
    try:
        field_type = FieldType(item.type)
    except ValueError:
        field_type = FieldType.TEXT
    return FieldDefinition(
        id=item.field_id,
        label=item.label or item.field_id,
        description=item.reason or "由字段建议采纳生成",
        type=field_type,
        visible=True,
        update_policy="story_driven",
    )

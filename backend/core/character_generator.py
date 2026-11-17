"""角色生成与字段补齐。

关键流程：用户输入一句话 -> LLM 生成 JSON -> Backend 按当前世界 Schema 校验 -> 返回预览
-> 前端展示并允许编辑 -> 用户点确认 -> 才写入 profile.yaml / state.json。
LLM 生成阶段绝不碰正式角色文件。
"""

from __future__ import annotations

from typing import Any

from backend.core import (
    field_suggestions,
    memory_manager,
    panel_service,
    prompt_builder,
    save_manager,
    state_manager,
    world_manager,
)
from backend.core.field_registry import validate_value
from backend.core.llm_provider import generate
from backend.core.story_parser import parse_json_object
from backend.core.validator import validate_generated_character
from backend.models.character import GeneratedCharacter
from backend.models.field import FieldDefinition, PanelSchema
from backend.models.story import CharacterUpdate


def _definitions_for_preview(world_id: str, is_protagonist: bool) -> tuple[PanelSchema, list[Any], list[dict[str, Any]]]:
    schema = panel_service.load_schema(world_id)
    # 预览阶段还没有角色 ID，用一个占位 ID 取得该类别的基础字段定义。
    placeholder = "protagonist" if is_protagonist else "__preview_npc__"
    definitions = schema.fields_for(placeholder, is_protagonist)
    return schema, definitions, [f.to_prompt_dict() for f in definitions]


def generate_preview(
    world_id: str,
    description: str,
    count: int = 1,
    is_protagonist: bool = False,
    auto_relations: bool = False,
) -> dict[str, Any]:
    """只返回预览数据，不写任何角色文件。"""
    world_manager.validate_world_id(world_id)
    if not description.strip():
        raise ValueError("请给出一句人物核心描述")

    _, definitions, prompt_defs = _definitions_for_preview(world_id, is_protagonist)
    protagonist_json = None
    if not is_protagonist:
        kinds = state_manager.character_kinds(world_id)
        proto_ids = [cid for cid, is_proto in kinds.items() if is_proto]
        if proto_ids:
            view = state_manager.get_character(world_id, proto_ids[0])
            protagonist_json = {"name": view.profile.name, "core_description": view.profile.core_description,
                                "values": view.state.custom_fields}

    request = prompt_builder.build_character_request(
        world_id=world_id,
        user_description=description,
        field_definitions=prompt_defs,
        is_protagonist=is_protagonist,
        count=max(1, min(8, count)),
        protagonist_json=protagonist_json,
        auto_relations=auto_relations,
    )
    result = generate(request)
    if result.error:
        # 模型未接通时明确报错，绝不返回"看起来成功"的空人物。
        raise RuntimeError(result.error)
    data, reason = parse_json_object(result.text)
    warnings: list[str] = []
    if reason:
        # 例如「字符串内换行已修复」「输出被截断」——必须让玩家看见，不能假装格式完美。
        warnings.append(reason)
    if data is None:
        # 连修复都救不回来时，附上原文开头，方便在 Prompt Studio 里对症调整。
        snippet = (result.text or "").strip()[:400]
        raise RuntimeError(f"人物生成结果无法解析：{reason}。模型原文开头：{snippet}")

    raw_list = _extract_character_list(data, is_protagonist)
    if not raw_list:
        raise RuntimeError("模型没有返回任何人物数据（找不到 characters / character 字段）")

    previews: list[dict[str, Any]] = []
    for raw in raw_list[: (1 if is_protagonist else 8)]:
        if not isinstance(raw, dict):
            continue
        values, issues = validate_generated_character(raw.get("custom_fields") or {}, definitions)
        warnings.extend(issues)
        hooks = raw.get("plot_hooks") or []
        if isinstance(hooks, str):
            hooks = [hooks]
        item = GeneratedCharacter(
            name=str(raw.get("name") or "未命名角色"),
            core_description=str(raw.get("core_description") or raw.get("description") or description),
            custom_fields=values,
            plot_hooks=[str(h) for h in hooks if h],
            opening_situation=str(raw.get("opening_situation") or ""),
            is_protagonist=is_protagonist,
        )
        previews.append(item.model_dump())

    if not previews:
        raise RuntimeError("模型返回的人物数据结构不符合预期，请调整人物生成 Prompt 后重试")
    return {
        "characters": previews,
        "warnings": warnings,
        "field_definitions": [f.model_dump(mode="json") for f in definitions],
        "provider": result.provider,
        "is_protagonist": is_protagonist,
        "raw_text": result.text if warnings else "",
    }


def _extract_character_list(data: dict[str, Any], is_protagonist: bool) -> list[Any]:
    """容忍模型对「返回几个人」这件事的各种自由发挥。

    本地模型常把单个人物直接返回成顶层对象、把数组塞进 character / npcs，
    或干脆把主角也包进 characters。这里统一收敛成列表；宁可多认，也不要让
    玩家看到一句「没有返回任何人物数据」却不知道模型到底写了什么。
    """
    if is_protagonist:
        for key in ("character", "protagonist", "characters"):
            value = data.get(key)
            if isinstance(value, dict):
                return [value]
            if isinstance(value, list) and value:
                return value[:1]
        if any(k in data for k in ("name", "custom_fields", "core_description")):
            return [data]
        return []

    for key in ("characters", "npcs", "character", "people"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    if any(k in data for k in ("name", "custom_fields", "core_description")):
        return [data]
    return []


def confirm_characters(world_id: str, characters: list[dict[str, Any]], is_protagonist: bool) -> list[dict[str, Any]]:
    """用户点确认后才落盘。主角只允许一个，重复确认会覆盖主角文件。"""
    saved = []
    for payload in characters[: (1 if is_protagonist else 8)]:
        view = state_manager.save_generated_character(world_id, payload, is_protagonist=is_protagonist)
        saved.append(view.model_dump())
    if saved:
        # 新增/改动人物属于事件触发，立刻允许做一次字段校准。
        _mark_reconcile_due(world_id)
        save_manager.auto_save(world_id)
    return saved


def _mark_reconcile_due(world_id: str) -> None:
    state = state_manager.get_current_state(world_id)
    cfg = _field_config()
    every = int(cfg.get("field_reconcile_every", 8))
    state.turns_since_reconcile = every if every > 0 else 0
    state_manager.write_current_state(world_id, state)


def _field_config() -> dict[str, Any]:
    from backend.core import settings_service
    return settings_service.load_settings().get("field_system") or {}


def backfill_fields(world_id: str, character_id: str) -> dict[str, Any]:
    """为「新出现但还没有值」的字段按历史补一个合理的当前值。任意轮次都可以调用。"""
    view = state_manager.get_character(world_id, character_id)
    definitions = [FieldDefinition(**f) for f in view.fields]
    missing = [f for f in definitions if f.id not in view.state.custom_fields]
    if not missing:
        return {"applied": {}, "message": "没有新增字段需要补齐"}

    state = state_manager.get_current_state(world_id)
    request = prompt_builder.build_backfill_request(
        world_id=world_id,
        profile=view.profile.model_dump(),
        existing_fields=view.state.custom_fields,
        new_fields=[f.to_prompt_dict() for f in missing],
        summary=memory_manager.load_summary(world_id),
        timeline_events=memory_manager.load_timeline(world_id, limit=30),
        recent_history=memory_manager.load_history(world_id, limit=10),
        current_context=state.current_location,
        turn=state.turn,
    )
    result = generate(request)
    if result.error:
        return {"applied": {}, "error": result.error}
    data, reason = parse_json_object(result.text)
    if data is None:
        return {"applied": {}, "error": reason}

    field_map = {f.id: f for f in missing}
    accepted: dict[str, Any] = {}
    issues: list[str] = []
    for key, value in (data.get("values") or {}).items():
        definition = field_map.get(key)
        if definition is None:
            issues.append(f"回填返回了非新增字段 {key}，已忽略")
            continue
        ok, normalized = validate_value(definition, value)
        if ok:
            accepted[key] = normalized
        else:
            issues.append(f"字段 {key} 回填值类型不符，已忽略")
    if accepted:
        # 复用 apply_updates，保证与正常回合走同一套校验与写入路径。
        state_manager.apply_updates(world_id, [CharacterUpdate(
            character_id=character_id, custom_fields=accepted,
        )])
    return {"applied": accepted, "issues": issues}


def reconcile_fields(world_id: str, character_id: str) -> dict[str, Any]:
    """定期校准：只修正确实需要改的字段，未提及的字段一律不动。"""
    view = state_manager.get_character(world_id, character_id)
    definitions = [FieldDefinition(**f) for f in view.fields]
    field_map = {f.id: f for f in definitions}
    state = state_manager.get_current_state(world_id)
    request = prompt_builder.build_reconcile_request(
        world_id=world_id,
        profile=view.profile.model_dump(),
        field_definitions=[f.to_prompt_dict() for f in definitions],
        current_fields=view.state.custom_fields,
        summary=memory_manager.load_summary(world_id),
        recent_history=memory_manager.load_history(world_id, limit=10),
        current_context=state.current_location,
    )
    result = generate(request)
    if result.error:
        return {"corrections": 0, "error": result.error}
    data, reason = parse_json_object(result.text)
    if data is None:
        return {"corrections": 0, "error": reason}

    accepted: dict[str, Any] = {}
    for item in data.get("corrections") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("field_id")
        definition = field_map.get(key)
        if definition is None:
            continue  # 不允许借校准新增字段
        ok, normalized = validate_value(definition, item.get("value"))
        if ok:
            accepted[key] = normalized
    if accepted:
        state_manager.apply_updates(world_id, [CharacterUpdate(
            character_id=character_id, custom_fields=accepted,
        )])
    return {"corrections": len(accepted), "fields": list(accepted)}


def reconcile_all(world_id: str) -> dict[str, Any]:
    """对当前世界所有角色做一次校准，通常在事件触发或每 N 轮兜底时调用。"""
    results = {}
    for cid in state_manager.list_character_ids(world_id):
        results[cid] = reconcile_fields(world_id, cid)
    return results


def adopt_suggestion(world_id: str, field_id: str) -> dict[str, Any]:
    """用户点「采纳」：把建议变成真正的字段定义，然后按历史回填所有角色。

    这是唯一由建议触发的 schema 写入路径，仍然走 PanelService，LLM 本身无权改 schema。
    """
    items = field_suggestions.load_items(world_id)
    target = next((i for i in items if i.field_id == field_id and i.status == "pending"), None)
    if target is None:
        raise ValueError(f"没有待处理的字段建议: {field_id}")

    from backend.models.field import CharacterOverride
    schema = panel_service.load_schema(world_id)
    definition = field_suggestions.build_definition(target)
    if definition.id not in {f.id for f in schema.npc.fields}:
        schema.npc.fields.append(definition)
    if definition.id not in {f.id for f in schema.protagonist.fields}:
        schema.protagonist.fields.append(definition)
    raw = {
        "version": schema.version,
        "same_fields_for_all_npcs": schema.same_fields_for_all_npcs,
        "protagonist": {"fields": [f.model_dump(mode="json") for f in schema.protagonist.fields]},
        "npc": {"fields": [f.model_dump(mode="json") for f in schema.npc.fields]},
        "overrides": {
            cid: {"add_fields": [f.model_dump(mode="json") for f in ov.add_fields],
                  "override_fields": [f.model_dump(mode="json") for f in ov.override_fields]}
            for cid, ov in schema.overrides.items()
        },
    }
    panel_service.save_schema(world_id, raw)
    field_suggestions.set_status(world_id, field_id, "adopted")

    backfilled = {}
    for cid in state_manager.list_character_ids(world_id):
        backfilled[cid] = backfill_fields(world_id, cid)
    save_manager.auto_save(world_id)
    return {"field": definition.model_dump(mode="json"), "backfilled": backfilled}

"""人物状态服务：固定设定与动态状态分开读写。

profile.yaml 在普通剧情回合里只读；state.json 的 custom_fields 才是 LLM 能改的部分。
角色 ID 一律先经本模块确认属于当前世界，杜绝跨世界引用。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from backend.core import bible_service, file_utils, panel_service, world_manager
from backend.core.field_registry import validate_value
from backend.models.character import CharacterProfile, CharacterState, CharacterView
from backend.models.field import UpdatePolicy


def _character_dir(world_id: str, character_id: str, is_protagonist: bool) -> Path:
    if is_protagonist:
        return world_manager.resolve(world_id, "characters", "protagonist")
    if not character_id or "/" in character_id or "\\" in character_id or character_id.startswith(".."):
        raise ValueError(f"非法角色 ID: {character_id!r}")
    return world_manager.resolve(world_id, "characters", "npcs", character_id)


def list_character_ids(world_id: str) -> list[str]:
    ids: list[str] = []
    proto = world_manager.resolve(world_id, "characters", "protagonist", "profile.yaml")
    if proto.is_file():
        data = file_utils.read_yaml(proto, {}) or {}
        if data.get("character_id"):
            ids.append(str(data["character_id"]))
    npc_root = world_manager.resolve(world_id, "characters", "npcs")
    if npc_root.is_dir():
        for folder in sorted(npc_root.iterdir()):
            profile = folder / "profile.yaml"
            if folder.is_dir() and profile.is_file():
                data = file_utils.read_yaml(profile, {}) or {}
                ids.append(str(data.get("character_id") or folder.name))
    return ids


def character_kinds(world_id: str) -> dict[str, bool]:
    """返回 {character_id: 是否主角}，只含当前世界的角色。"""
    result: dict[str, bool] = {}
    proto = world_manager.resolve(world_id, "characters", "protagonist", "profile.yaml")
    if proto.is_file():
        data = file_utils.read_yaml(proto, {}) or {}
        result[str(data.get("character_id", "protagonist"))] = True
    npc_root = world_manager.resolve(world_id, "characters", "npcs")
    if npc_root.is_dir():
        for folder in sorted(npc_root.iterdir()):
            profile = folder / "profile.yaml"
            if folder.is_dir() and profile.is_file():
                data = file_utils.read_yaml(profile, {}) or {}
                result[str(data.get("character_id") or folder.name)] = False
    return result


def _locate(world_id: str, character_id: str) -> tuple[Path, bool]:
    kinds = character_kinds(world_id)
    if character_id not in kinds:
        raise ValueError(f"角色 {character_id} 不属于世界 {world_id}")
    is_proto = kinds[character_id]
    return _character_dir(world_id, character_id, is_proto), is_proto


def get_character(world_id: str, character_id: str) -> CharacterView:
    folder, is_proto = _locate(world_id, character_id)
    profile = CharacterProfile(**(file_utils.read_yaml(folder / "profile.yaml", {}) or {}))
    state = CharacterState(**(file_utils.read_json(folder / "state.json", {}) or {}))
    schema = panel_service.load_schema(world_id)
    return CharacterView(
        profile=profile,
        state=state,
        fields=[f.model_dump(mode="json") for f in schema.fields_for(character_id, is_proto)],
    )


def list_characters(world_id: str) -> list[CharacterView]:
    return [get_character(world_id, cid) for cid in list_character_ids(world_id)]


def characters_for_prompt(world_id: str, active_ids: list[str] | None = None) -> dict[str, Any]:
    """给 PromptBuilder 的角色快照：只有当前世界、只有本轮在场角色。"""
    kinds = character_kinds(world_id)
    chosen = [cid for cid in (active_ids or []) if cid in kinds]
    if not chosen:
        chosen = list(kinds)
    result: dict[str, Any] = {}
    for cid in chosen:
        view = get_character(world_id, cid)
        if view.state.retired:
            continue
        result[cid] = {
            "name": view.profile.name,
            "is_protagonist": view.profile.is_protagonist,
            "core_description": bible_service.clip(view.profile.core_description, 300),
            "values": view.state.custom_fields,
            # 玩家确认过的设定基线，注入剧情前已在 bible_service 里限额裁剪。
            "bible_text": bible_service.bible_for_prompt(view.profile.bible),
        }
    return result


def _next_npc_id(world_id: str) -> str:
    npc_root = world_manager.resolve(world_id, "characters", "npcs")
    used = {p.name for p in npc_root.iterdir() if p.is_dir()} if npc_root.is_dir() else set()
    index = 1
    while f"npc_{index:03d}" in used:
        index += 1
    return f"npc_{index:03d}"


def save_generated_character(
    world_id: str,
    payload: dict[str, Any],
    is_protagonist: bool = False,
    character_id: str | None = None,
) -> CharacterView:
    """把用户确认过的角色预览写成 profile.yaml + state.json。此函数只由 API 层在确认后调用。"""
    schema = panel_service.load_schema(world_id)
    cid = character_id or ("protagonist" if is_protagonist else _next_npc_id(world_id))
    folder = _character_dir(world_id, cid, is_protagonist)
    is_new = not (folder / "profile.yaml").is_file()
    folder.mkdir(parents=True, exist_ok=True)

    # 覆盖已有角色（尤其是主角，cid 固定 "protagonist"）时不能把之前确认过的
    # 设定基线 / 动态字段进度清空：一句话生成的预览不带 bible，直接写空字典会静默丢数据。
    old_profile: dict[str, Any] = {}
    old_state: dict[str, Any] = {}
    if (folder / "profile.yaml").is_file():
        old_profile = file_utils.read_yaml(folder / "profile.yaml", {}) or {}
        old_state = file_utils.read_json(folder / "state.json", {}) or {}

    definitions = schema.fields_for(cid, is_protagonist)
    # 只保留 schema 里定义且类型合法的键，缺的给保守默认值。
    from backend.core.field_registry import default_value
    accepted: dict[str, Any] = {}
    raw_values = dict(old_state.get("custom_fields") or {})
    raw_values.update(payload.get("custom_fields") or {})
    for definition in definitions:
        value = raw_values.get(definition.id)
        if value is None:
            accepted[definition.id] = default_value(definition)
            continue
        ok, normalized = validate_value(definition, value)
        accepted[definition.id] = normalized if ok else default_value(definition)

    profile = CharacterProfile(
        character_id=cid,
        name=str(payload.get("name") or "未命名角色").strip(),
        is_protagonist=is_protagonist,
        core_description=str(payload.get("core_description") or ""),
        # 预览经 confirm_characters 原样透传 dict，不会经过 GeneratedCharacter 模型，
        # 这里必须显式接住，否则玩家逐条精修的设定基线会被静默丢掉。
        bible=bible_service.normalize_bible(
            payload["bible"] if "bible" in payload else old_profile.get("bible"), cid
        ),
    )
    state = CharacterState(
        character_id=cid, custom_fields=accepted,
        orphaned=old_state.get("_orphaned") or {},
        retired=bool(old_state.get("retired")),
        # 开局后新建的角色，下一轮作为「首次登场」自然引入，不突兀地出现在画面里。
        pending_introduction=is_new and is_story_started(world_id),
    )
    file_utils.atomic_write_yaml(folder / "profile.yaml", profile.model_dump())
    file_utils.atomic_write_json(folder / "state.json", state.dump_file())
    return get_character(world_id, cid)


def set_planned_event_done(world_id: str, event_id: str, done: bool = True):
    """标记一条预定事件是否已完成。

    完成态属于运行态，写进 state/current.json（在 save_manager 快照白名单内），
    因此读档回到更早的分支时它会跟着回到「未完成」；profile.yaml 保持只读。
    """
    world_manager.validate_world_id(world_id)
    if not event_id or len(event_id) > 128:
        raise ValueError("非法事件 ID")
    if event_id not in bible_service.all_event_ids(world_id):
        raise ValueError(f"事件 {event_id} 不属于世界 {world_id}")
    state = get_current_state(world_id)
    ids = [str(x) for x in (state.completed_planned_events or [])]
    if done and event_id not in ids:
        ids.append(event_id)
    elif not done and event_id in ids:
        ids.remove(event_id)
    state.completed_planned_events = ids
    write_current_state(world_id, state)
    return state


def apply_updates(world_id: str, updates: list[Any]) -> list[str]:
    """写入已通过 Validator 的字段更新。这里再做一次防御性校验。"""
    schema = panel_service.load_schema(world_id)
    kinds = character_kinds(world_id)
    applied: list[str] = []
    for update in updates:
        cid = update.character_id
        if cid not in kinds:
            continue
        folder, is_proto = _locate(world_id, cid)
        state = CharacterState(**(file_utils.read_json(folder / "state.json", {}) or {}))
        field_map = schema.field_map(cid, is_proto)
        changed = False
        for key, value in update.custom_fields.items():
            definition = field_map.get(key)
            if definition is None or definition.update_policy == UpdatePolicy.STATIC:
                continue
            ok, normalized = validate_value(definition, value)
            if not ok:
                continue
            state.custom_fields[key] = normalized
            changed = True
        if changed:
            file_utils.atomic_write_json(folder / "state.json", state.dump_file())
            applied.append(cid)
    return applied


def set_field_value(world_id: str, character_id: str, field_id: str, value: Any) -> CharacterView:
    """玩家在面板上手动改值。手动修改不受 update_policy 限制，但仍要过类型校验。"""
    folder, is_proto = _locate(world_id, character_id)
    schema = panel_service.load_schema(world_id)
    definition = schema.field_map(character_id, is_proto).get(field_id)
    if definition is None:
        raise ValueError(f"字段 {field_id} 不存在于当前世界 Schema")
    ok, normalized = validate_value(definition, value)
    if not ok:
        raise ValueError(f"字段 {field_id} 的值不符合类型 {definition.type.value}")
    state = CharacterState(**(file_utils.read_json(folder / "state.json", {}) or {}))
    state.custom_fields[field_id] = normalized
    file_utils.atomic_write_json(folder / "state.json", state.dump_file())
    return get_character(world_id, character_id)


def load_relationships(world_id: str) -> dict[str, Any]:
    return file_utils.read_json(world_manager.resolve(world_id, "state", "relationships.json"), {}) or {}


def save_relationships(world_id: str, data: dict[str, Any]) -> None:
    file_utils.atomic_write_json(world_manager.resolve(world_id, "state", "relationships.json"), data)


def get_current_state(world_id: str):
    from backend.models.world import CurrentState
    data = file_utils.read_json(world_manager.resolve(world_id, "state", "current.json"), {}) or {}
    data.setdefault("world_id", world_id)
    return CurrentState(**data)


def write_current_state(world_id: str, state) -> None:
    file_utils.atomic_write_json(world_manager.resolve(world_id, "state", "current.json"), state.model_dump())


def edit_character(world_id: str, character_id: str, payload: dict[str, Any]) -> CharacterView:
    """编辑已有角色的固定设定（name / core_description / bible），不动 state.json。"""
    folder, is_proto = _locate(world_id, character_id)
    old_profile = file_utils.read_yaml(folder / "profile.yaml", {}) or {}
    profile = CharacterProfile(
        character_id=character_id,
        name=str(payload.get("name") or old_profile.get("name") or "未命名角色").strip(),
        is_protagonist=is_proto,
        core_description=str(payload.get("core_description")
                             if "core_description" in payload
                             else old_profile.get("core_description") or ""),
        bible=bible_service.normalize_bible(
            payload["bible"] if "bible" in payload else old_profile.get("bible"), character_id
        ),
    )
    file_utils.atomic_write_yaml(folder / "profile.yaml", profile.model_dump())
    return get_character(world_id, character_id)


def delete_character(world_id: str, character_id: str) -> None:
    """物理删除角色目录，并清理所有指向它的引用。只应由 API 层在未开局时放行。"""
    folder, _is_proto = _locate(world_id, character_id)

    # 先收集该角色名下的事件 id（目录删掉后就收集不到了）。
    owned_events = {
        evt["id"] for evt in bible_service.collect_planned_events(world_id, None, [], for_prompt=False)
        if evt.get("owner_id") == character_id
    }

    shutil.rmtree(folder)

    # 清理其他角色 bible 里指向它的关系卡。
    for other_id in list_character_ids(world_id):
        of, _ = _locate(world_id, other_id)
        oprofile = file_utils.read_yaml(of / "profile.yaml", {}) or {}
        bible = bible_service.normalize_bible(oprofile.get("bible"), other_id)
        bible["relations"] = [
            c for c in bible["relations"] if c.get("target_character_id") != character_id
        ]
        oprofile["bible"] = bible
        file_utils.atomic_write_yaml(of / "profile.yaml", oprofile)

    # 清理 panels.yaml 里该角色的字段覆盖。
    panels_path = world_manager.resolve(world_id, "ui", "panels.yaml")
    panels = file_utils.read_yaml(panels_path, {}) or {}
    overrides = panels.get("overrides") if isinstance(panels.get("overrides"), dict) else {}
    if character_id in overrides:
        del overrides[character_id]
        panels["overrides"] = overrides
        file_utils.atomic_write_yaml(panels_path, panels)

    # 清理字段建议里以该角色为来源的条目。
    sugg_path = world_manager.resolve(world_id, "state", "field_suggestions.json")
    sugg = file_utils.read_json(sugg_path, {}) or {}
    items = [i for i in (sugg.get("items") or [])
             if not (isinstance(i, dict) and i.get("source_character_id") == character_id)]
    sugg["items"] = items
    file_utils.atomic_write_json(sugg_path, sugg)

    # 清理 current.json 的在场名单与已完成事件。
    state = get_current_state(world_id)
    state.active_character_ids = [c for c in (state.active_character_ids or []) if c != character_id]
    state.completed_planned_events = [e for e in (state.completed_planned_events or []) if e not in owned_events]
    write_current_state(world_id, state)


def set_retired(world_id: str, character_id: str, retired: bool) -> CharacterView:
    """开局后让角色「退场」/恢复。写 state.json（随存档走），历史与文件保留。"""
    folder, _ = _locate(world_id, character_id)
    state = CharacterState(**(file_utils.read_json(folder / "state.json", {}) or {}))
    state.retired = bool(retired)
    file_utils.atomic_write_json(folder / "state.json", state.dump_file())
    return get_character(world_id, character_id)


def pending_introductions(world_id: str) -> list[str]:
    """本轮需要「首次登场」的角色 id。"""
    result: list[str] = []
    for cid in list_character_ids(world_id):
        folder, _ = _locate(world_id, cid)
        state = CharacterState(**(file_utils.read_json(folder / "state.json", {}) or {}))
        if state.pending_introduction and not state.retired:
            result.append(cid)
    return result


def clear_pending_introductions(world_id: str, cids: list[str]) -> None:
    for cid in cids:
        folder, _ = _locate(world_id, cid)
        state = CharacterState(**(file_utils.read_json(folder / "state.json", {}) or {}))
        if state.pending_introduction:
            state.pending_introduction = False
            file_utils.atomic_write_json(folder / "state.json", state.dump_file())


def is_retired(world_id: str, character_id: str) -> bool:
    folder, _ = _locate(world_id, character_id)
    state = CharacterState(**(file_utils.read_json(folder / "state.json", {}) or {}))
    return bool(state.retired)


def is_story_started(world_id: str) -> bool:
    """是否已开局：显式点过「开始故事」，或已经产生过剧情（兼容旧世界）。

    旧世界没有 story_started 字段，但 turn > 0 说明已经玩过，同样要上锁。
    """
    try:
        if bool(world_manager.get_meta(world_id).story_started):
            return True
    except (OSError, ValueError, TypeError, FileNotFoundError):
        pass
    try:
        return get_current_state(world_id).turn > 0
    except Exception:
        return False

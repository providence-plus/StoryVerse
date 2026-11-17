"""UI Schema 服务：唯一有权修改 panels.yaml 的模块。

LLM 永远不经过这里；只有 Panel Designer 的请求（经 API 层鉴权后）会调用 save_schema。
字段被删除时旧值不销毁，搬进 state.json 的 _orphaned，随时可以在设计器里恢复。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from backend.core import file_utils, world_manager
from backend.models.character import CharacterState
from backend.models.field import FieldDefinition, PanelGroup, PanelSchema


def _clean_field(raw: dict[str, Any]) -> dict[str, Any]:
    """去掉前端可能带上的多余键，并补齐默认值。"""
    allowed = {"id", "label", "description", "type", "visible", "update_policy", "options", "min", "max"}
    return {k: v for k, v in raw.items() if k in allowed}


def _auto_field_id(label: str, used: set[str], index: int) -> str:
    """玩家只写字段名，ID 由系统生成：英文名转 snake_case，中文名用稳定短哈希。

    同一个字段名总会得到同一个基础 ID（可预期），但 ID 只在首次保存时确定；
    之后玩家改字段名不会牵动 ID，历史字段值也就不会丢。
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").strip().lower()).strip("_")[:40]
    if not slug:
        digest = hashlib.sha1((label or f"field_{index}").encode("utf-8")).hexdigest()[:8]
        slug = f"field_{digest}"
    elif not slug[0].isalpha():
        slug = f"field_{slug}"
    candidate, suffix = slug, 2
    while candidate in used:
        candidate = f"{slug[:50]}_{suffix}"
        suffix += 1
    return candidate


def _safe_field_id(raw: Any, used: set[str], index: int) -> str:
    """把旧数据里手填的 ID 收敛成合法标识符；中文、空格、路径符都不会让字段消失。"""
    text = re.sub(r"[^a-z0-9_]", "_", str(raw or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")[:64]
    if not text or not text[0].isalpha():
        text = f"custom_{index}"
    candidate, suffix = text, 2
    while candidate in used:
        candidate = f"{text[:58]}_{suffix}"
        suffix += 1
    return candidate


def parse_schema(data: Any, warnings: list[str] | None = None) -> PanelSchema:
    """把 YAML 字典解析成 PanelSchema，容忍缺项与旧版本文件。

    早期版本遇到非法字段会静默 continue：用户在设计器里填了中文 ID，点保存也提示成功，
    刷新后字段却不见了。现在一律保留字段，自动修正 ID 并通过 warnings 明确告知。
    """
    if not isinstance(data, dict):
        data = {}
    notes: list[str] = warnings if warnings is not None else []

    def group(raw: Any, group_name: str) -> PanelGroup:
        fields: list[FieldDefinition] = []
        used: set[str] = set()
        if isinstance(raw, dict):
            for index, item in enumerate(raw.get("fields", []) or []):
                if not isinstance(item, dict):
                    notes.append(f"{group_name} 第 {index + 1} 项不是有效字段，已跳过。")
                    continue
                label = str(item.get("label") or "").strip() or "未命名字段"
                original_id = item.get("id")
                if original_id is None or not str(original_id).strip():
                    # 新流程：玩家不填 ID，只写字段名，由系统生成一次并固定下来。
                    fixed_id = _auto_field_id(label, used, index + 1)
                    notes.append(f"{group_name} 新字段「{label}」已自动生成 ID「{fixed_id}」。")
                else:
                    fixed_id = _safe_field_id(original_id, used, index + 1)
                    if str(original_id) != fixed_id:
                        notes.append(
                            f"{group_name} 字段「{label}」的 ID「{original_id}」不合法，"
                            f"已自动改为「{fixed_id}」（名称不变，历史值请以新 ID 为准）。"
                        )
                used.add(fixed_id)
                payload = {**_clean_field(item), "id": fixed_id, "label": label}
                try:
                    fields.append(FieldDefinition(**payload))
                except (ValueError, TypeError) as exc:
                    # 兜底：宁可退化成纯文本字段，也不让用户的字段凭空消失。
                    notes.append(f"{group_name} 字段「{label}」的配置有问题（{type(exc).__name__}），已保留为单行文本。")
                    fields.append(FieldDefinition(id=fixed_id, label=label))
        return PanelGroup(fields=fields)

    overrides: dict[str, Any] = {}
    raw_overrides = data.get("overrides") or {}
    if isinstance(raw_overrides, dict):
        for cid, ov in raw_overrides.items():
            if not isinstance(ov, dict):
                continue
            from backend.models.field import CharacterOverride
            overrides[cid] = CharacterOverride(
                add_fields=group({"fields": ov.get("add_fields", [])}, f"{cid}.add").fields,
                override_fields=group({"fields": ov.get("override_fields", [])}, f"{cid}.override").fields,
            )

    return PanelSchema(
        version=int(data.get("version", 1) or 1),
        same_fields_for_all_npcs=bool(data.get("same_fields_for_all_npcs", True)),
        protagonist=group(data.get("protagonist"), "protagonist"),
        npc=group(data.get("npc"), "npc"),
        overrides=overrides,
    )


def load_schema(world_id: str) -> PanelSchema:
    raw = file_utils.read_yaml(world_manager.resolve(world_id, "ui", "panels.yaml"), default={})
    return parse_schema(raw)


def _to_yaml_dict(schema: PanelSchema) -> dict[str, Any]:
    def dump_group(group: PanelGroup) -> dict[str, Any]:
        return {"fields": [f.model_dump(mode="json") for f in group.fields]}

    return {
        "version": schema.version,
        "same_fields_for_all_npcs": schema.same_fields_for_all_npcs,
        "protagonist": dump_group(schema.protagonist),
        "npc": dump_group(schema.npc),
        "overrides": {
            cid: {
                "add_fields": [f.model_dump(mode="json") for f in ov.add_fields],
                "override_fields": [f.model_dump(mode="json") for f in ov.override_fields],
            }
            for cid, ov in schema.overrides.items()
        },
    }


def _character_state_files(world_id: str) -> list[tuple[str, Path, bool]]:
    """列出 (character_id, state.json 路径, 是否主角)。"""
    root = world_manager.world_dir(world_id)
    result: list[tuple[str, Path, bool]] = []
    p = root / "characters" / "protagonist" / "state.json"
    if p.is_file():
        proto = file_utils.read_yaml(root / "characters" / "protagonist" / "profile.yaml", {}) or {}
        result.append((str(proto.get("character_id", "protagonist")), p, True))
    npc_root = root / "characters" / "npcs"
    if npc_root.is_dir():
        for folder in sorted(npc_root.iterdir()):
            state = folder / "state.json"
            if folder.is_dir() and state.is_file():
                profile = file_utils.read_yaml(folder / "profile.yaml", {}) or {}
                result.append((str(profile.get("character_id", folder.name)), state, False))
    return result


def _migrate_orphans(world_id: str, old: PanelSchema, new: PanelSchema) -> None:
    """字段增删后整理旧值：删除 -> _orphaned；重新加回 -> 从 _orphaned 恢复。"""
    for cid, path, is_proto in _character_state_files(world_id):
        state = CharacterState(**(file_utils.read_json(path, {}) or {}))
        old_ids = old.all_field_ids(cid, is_proto)
        new_ids = new.all_field_ids(cid, is_proto)

        for removed in old_ids - new_ids:
            if removed in state.custom_fields:
                state.orphaned[removed] = state.custom_fields.pop(removed)
        for restored in new_ids - old_ids:
            if restored in state.orphaned:
                state.custom_fields[restored] = state.orphaned.pop(restored)

        file_utils.atomic_write_json(path, state.dump_file())


def save_schema(world_id: str, raw: Any, warnings: list[str] | None = None) -> PanelSchema:
    """保存用户编辑后的 Schema，并同步处理旧数据。warnings 会回传给前端展示。"""
    new_schema = parse_schema(raw, warnings)
    old_schema = load_schema(world_id)
    world_manager.resolve(world_id, "ui", "panels.yaml")  # 校验路径合法性
    file_utils.atomic_write_yaml(world_manager.resolve(world_id, "ui", "panels.yaml"), _to_yaml_dict(new_schema))
    _migrate_orphans(world_id, old_schema, new_schema)
    return new_schema


def panels_for_frontend(world_id: str) -> dict[str, Any]:
    """给前端 DynamicPanel 的完整数据：字段定义 + 每个角色当前值。"""
    schema = load_schema(world_id)
    characters: dict[str, Any] = {}
    for cid, path, is_proto in _character_state_files(world_id):
        state = CharacterState(**(file_utils.read_json(path, {}) or {}))
        characters[cid] = {
            "is_protagonist": is_proto,
            "fields": [f.model_dump(mode="json") for f in schema.fields_for(cid, is_proto)],
            "values": state.custom_fields,
            "orphaned": state.orphaned,
        }
    return {
        "schema": _to_yaml_dict(schema),
        "characters": characters,
        "available_types": [
            "text", "long_text", "number", "boolean", "enum", "list", "key_value"
        ],
    }

"""世界管理：唯一负责解析世界路径、创建/删除世界的模块。

隔离原则：所有文件操作都必须先经过 resolve(world_id, ...)，
并且 PromptBuilder 只接受 world_id 单入口，从函数签名上杜绝跨世界拼接数据。
"""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.models.field import FieldDefinition, PanelGroup, PanelSchema
from backend.models.world import (
    CreateWorldRequest,
    CurrentState,
    UpdateWorldRequest,
    WorldMeta,
    WorldSummary,
    normalize_opening_choices,
)
from backend.core import file_utils

WORLD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORLDS_ROOT = PROJECT_ROOT / "worlds"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_world_id(world_id: str) -> str:
    if not WORLD_ID_PATTERN.match(world_id):
        raise ValueError(f"非法 world_id: {world_id!r}")
    return world_id


def world_dir(world_id: str) -> Path:
    return file_utils.safe_child(WORLDS_ROOT, validate_world_id(world_id))


def resolve(world_id: str, *parts: str) -> Path:
    """返回世界内某个文件的安全绝对路径。任何越界都会抛错而不是被悄悄纠正。"""
    return file_utils.safe_child(world_dir(world_id), *parts)


def _default_schema() -> PanelSchema:
    """新建世界时给一份能直接体验动态字段的默认 Schema（用户可全改）。"""
    return PanelSchema(
        protagonist=PanelGroup(fields=[
            FieldDefinition(id="outfit", label="当前服饰", description="主角此刻穿着的服装及细节",
                            type="text", update_policy="story_driven"),
            FieldDefinition(id="social_status", label="社会地位", description="主角当前在社会中的身份与处境",
                            type="text", update_policy="mostly_static"),
            FieldDefinition(id="current_mood", label="此刻情绪", description="主角当前外显的情绪状态，只写可观察到的表现",
                            type="text", update_policy="story_driven"),
        ]),
        npc=PanelGroup(fields=[
            FieldDefinition(id="appearance", label="外貌", description="NPC 的外貌特征",
                            type="text", update_policy="static"),
            FieldDefinition(id="outfit", label="当前服饰", description="NPC 此刻的穿着",
                            type="text", update_policy="story_driven"),
            FieldDefinition(id="hidden_thoughts", label="内心真实想法", description="NPC 没有说出口的真实想法",
                            type="long_text", update_policy="story_driven"),
            FieldDefinition(id="relationship_stage", label="关系阶段", description="与主角当前所处的关系阶段",
                            type="enum", options=["陌生", "戒备", "熟络", "信任", "亲密"],
                            update_policy="story_driven"),
            FieldDefinition(id="inventory", label="随身物品", description="此刻携带的物品",
                            type="list", update_policy="story_driven"),
            FieldDefinition(id="known_facts", label="已知情报", description="掌握的关键信息，键为事项、值为内容",
                            type="key_value", visible=False, update_policy="story_driven"),
            FieldDefinition(id="is_present", label="是否在场", description="该角色此刻是否在场",
                            type="boolean", update_policy="story_driven"),
        ]),
    )


def _schema_to_yaml_dict(schema: PanelSchema) -> dict[str, Any]:
    """把 Pydantic 模型转成干净的 YAML 字典，避免把枚举对象写进文件。"""
    def dump_group(group: PanelGroup) -> dict[str, Any]:
        return {"fields": [f.model_dump(mode="json") for f in group.fields]}

    overrides = {
        cid: {
            "add_fields": [f.model_dump(mode="json") for f in ov.add_fields],
            "override_fields": [f.model_dump(mode="json") for f in ov.override_fields],
        }
        for cid, ov in schema.overrides.items()
    }
    return {
        "version": schema.version,
        "same_fields_for_all_npcs": schema.same_fields_for_all_npcs,
        "protagonist": dump_group(schema.protagonist),
        "npc": dump_group(schema.npc),
        "overrides": overrides,
    }


def create_world(request: CreateWorldRequest) -> WorldMeta:
    """创建一个拥有完整独立目录树的新世界。"""
    world_id = request.world_id or f"world_{uuid.uuid4().hex[:8]}"
    validate_world_id(world_id)
    root = world_dir(world_id)
    if root.exists():
        raise FileExistsError(f"世界已存在: {world_id}")

    for sub in (
        "characters/protagonist", "characters/npcs", "locations", "lore",
        "state", "memory", "ui", "saves",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)

    now = _now()
    meta = WorldMeta(
        world_id=world_id,
        name=request.name.strip() or "未命名世界",
        description=request.description,
        genre=request.genre,
        created_at=now,
        updated_at=now,
        opening_scene=request.opening_scene,
        opening_location=request.opening_location,
        # 「不调用 AI，直接用我填的创建」可能只填了一两个选项；补齐到四项 A/B/C/D，
        # 否则游戏页只显示一两个按钮。空数组保持为空（完全自己写的入口）。
        opening_choices=normalize_opening_choices(request.opening_choices),
    )
    file_utils.atomic_write_yaml(resolve(world_id, "world.yaml"), meta.model_dump())
    # 一句话生成世界时写入模型扩写后的正文；手动创建时给中性的可编辑模板。
    setting = request.setting.strip() or "（尚未填写世界观背景。可在 Prompt Studio 之外直接编辑本文件，或删除世界后重新用「一句话生成」创建。）"
    rules = request.rules.strip() or "（尚未填写世界规则。直接编辑本文件即可，下一轮生成会重新读取。）"
    file_utils.atomic_write_text(resolve(world_id, "setting.md"), f"# 世界观背景\n\n{setting}\n")
    file_utils.atomic_write_text(resolve(world_id, "rules.md"), f"# 世界规则\n\n{rules}\n")
    if request.opening_location.strip():
        # 开场地点也写进 locations/，Prompt Builder 会把它一起带进上下文。
        file_utils.atomic_write_text(
            resolve(world_id, "locations", "opening.md"),
            f"# {request.opening_location.strip()}\n\n{request.opening_scene.strip()}\n",
        )
    file_utils.atomic_write_text(resolve(world_id, "locations", "README.md"), "# 地点设定\n\n每个地点一个 Markdown 文件，由 Prompt Builder 按需读取。\n")
    file_utils.atomic_write_text(resolve(world_id, "lore", "README.md"), "# 补充设定\n")
    # 世界专属写手风格。刻意写 0 字节空文件而不是「带注释的模板」：
    # system_prompt_for() 用 strip() 判空，注释文字会被误当成风格拼进每一轮。
    # 用法说明只放在 README 与 Prompt Studio 里。
    file_utils.atomic_write_text(resolve(world_id, "system_prompt.md"), request.writer_style.strip())
    file_utils.atomic_write_yaml(resolve(world_id, "ui", "panels.yaml"), _schema_to_yaml_dict(_default_schema()))
    file_utils.atomic_write_json(
        resolve(world_id, "state", "current.json"),
        CurrentState(
            world_id=world_id,
            current_location=request.opening_location.strip(),
            updated_at=now,
        ).model_dump(),
    )
    file_utils.atomic_write_text(resolve(world_id, "memory", "summary.md"), "故事尚未开始。\n")
    file_utils.atomic_write_text(resolve(world_id, "memory", "timeline.jsonl"), "")
    file_utils.atomic_write_json(resolve(world_id, "state", "field_suggestions.json"), {"items": []})
    return meta


def get_meta(world_id: str) -> WorldMeta:
    data = file_utils.read_yaml(resolve(world_id, "world.yaml"))
    if not data:
        raise FileNotFoundError(f"世界不存在: {world_id}")
    return WorldMeta(**data)


def update_world(world_id: str, request: UpdateWorldRequest) -> WorldMeta:
    meta = get_meta(world_id)
    for key in ("name", "description", "genre"):
        value = getattr(request, key)
        if value is not None:
            setattr(meta, key, value)
    meta.updated_at = _now()
    file_utils.atomic_write_yaml(resolve(world_id, "world.yaml"), meta.model_dump())
    return meta


def set_story_started(world_id: str, started: bool = True) -> WorldMeta:
    """玩家点「开始该世界的故事」：只切状态、不调模型，幂等。

    story_started 写在 world.yaml，不在存档快照白名单内，因此读旧档也不会回滚。
    开局后人物编辑 / 物理删除以此为界上锁。
    """
    meta = get_meta(world_id)
    meta.story_started = bool(started)
    meta.updated_at = _now()
    file_utils.atomic_write_yaml(resolve(world_id, "world.yaml"), meta.model_dump())
    return meta


def delete_world(world_id: str) -> None:
    """删除整个世界目录。前端必须先做二次确认。"""
    root = world_dir(world_id)
    if not root.exists():
        raise FileNotFoundError(f"世界不存在: {world_id}")
    shutil.rmtree(root)


def list_worlds() -> list[WorldSummary]:
    """列出全部世界，并读取各自 state/current.json 生成最近进度。"""
    if not WORLDS_ROOT.is_dir():
        return []
    summaries: list[WorldSummary] = []
    for root in sorted(WORLDS_ROOT.iterdir()):
        if not root.is_dir():
            continue
        try:
            meta = get_meta(root.name)
        except (ValueError, TypeError, FileNotFoundError):
            continue
        current = file_utils.read_json(root / "state" / "current.json", default={}) or {}
        npc_dir = root / "characters" / "npcs"
        npc_count = len([p for p in npc_dir.glob("*/profile.yaml")]) if npc_dir.is_dir() else 0
        has_protagonist = (root / "characters" / "protagonist" / "profile.yaml").is_file()
        save_dir = root / "saves"
        save_count = len([p for p in save_dir.iterdir() if p.is_dir()]) if save_dir.is_dir() else 0
        summaries.append(WorldSummary(
            **meta.model_dump(),
            character_count=npc_count + (1 if has_protagonist else 0),
            save_count=save_count,
            last_played_at=current.get("updated_at") or None,
            current_location=current.get("current_location", ""),
            has_protagonist=has_protagonist,
        ))
    return summaries


def ensure_demo_world() -> None:
    """Demo 世界的静态文件随项目提供；这里只补齐运行时可能缺失的文件。"""
    root = WORLDS_ROOT / "world_demo"
    if not (root / "world.yaml").is_file():
        create_world(CreateWorldRequest(world_id="world_demo", name="雾港旧事", description="一座终年落雨的港口城市", genre="近现代 · 悬疑"))
        return
    for name, default in (
        ("ui/panels.yaml", _schema_to_yaml_dict(_default_schema())),
        ("state/current.json", CurrentState(world_id="world_demo", updated_at=_now()).model_dump()),
        ("state/field_suggestions.json", {"items": []}),
    ):
        path = resolve("world_demo", *name.split("/"))
        if not path.is_file():
            if path.suffix in (".yaml", ".yml"):
                file_utils.atomic_write_yaml(path, default)
            else:
                file_utils.atomic_write_json(path, default)
    for name, text in (("memory/summary.md", ""), ("memory/timeline.jsonl", ""), ("system_prompt.md", "")):
        path = resolve("world_demo", *name.split("/"))
        if not path.is_file():
            file_utils.atomic_write_text(path, text)

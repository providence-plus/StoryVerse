"""动态字段系统：新增 10 条验收中最关键的部分。"""

from __future__ import annotations

from pathlib import Path

from backend.core import (
    character_generator,
    panel_service,
    state_manager,
    story_engine,
    world_manager,
)
from backend.models.field import FieldDefinition
from backend.models.story import TurnRequest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _schema_dict(schema) -> dict:
    return {
        "version": schema.version,
        "same_fields_for_all_npcs": schema.same_fields_for_all_npcs,
        "protagonist": {"fields": [f.model_dump(mode="json") for f in schema.protagonist.fields]},
        "npc": {"fields": [f.model_dump(mode="json") for f in schema.npc.fields]},
        "overrides": {},
    }


def test_user_defined_field_works_without_code_change(seeded_world):
    """用户随手造一个全新字段，读写与校验全部生效，不需要改 Python 或 React。"""
    schema = panel_service.load_schema(seeded_world)
    schema.npc.fields.append(FieldDefinition(
        id="magic_level", label="魔力等级", description="完全由用户新增的字段",
        type="number", min=0, max=100,
    ))
    panel_service.save_schema(seeded_world, _schema_dict(schema))

    ids = panel_service.load_schema(seeded_world).all_field_ids("npc_001", False)
    assert "magic_level" in ids

    view = state_manager.set_field_value(seeded_world, "npc_001", "magic_level", 37)
    assert view.state.custom_fields["magic_level"] == 37
    with __import__("pytest").raises(ValueError):
        state_manager.set_field_value(seeded_world, "npc_001", "magic_level", 999)


def test_no_fixed_field_ids_in_core_business_logic():
    """核心逻辑里不得出现原需求写死的那些人物字段名。"""
    banned = ["physical_condition", "expression", "hidden_thoughts",
              "relationship_to_player", "speaking_style", "emotion"]
    core = PROJECT_ROOT / "backend" / "core"
    for name in ("validator.py", "story_engine.py", "state_manager.py",
                 "prompt_builder.py", "field_registry.py", "character_generator.py"):
        text = (core / name).read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{name} 里出现了硬编码字段 {word}"


def test_deleting_field_preserves_old_value_in_orphaned(seeded_world):
    view = state_manager.get_character(seeded_world, "npc_001")
    assert "outfit" in view.state.custom_fields

    schema = panel_service.load_schema(seeded_world)
    schema.npc.fields = [f for f in schema.npc.fields if f.id != "outfit"]
    panel_service.save_schema(seeded_world, _schema_dict(schema))

    after = state_manager.get_character(seeded_world, "npc_001")
    assert "outfit" not in after.state.custom_fields
    assert "outfit" in after.state.orphaned


def test_restoring_field_recovers_orphaned_value(seeded_world):
    schema = panel_service.load_schema(seeded_world)
    schema.npc.fields = [f for f in schema.npc.fields if f.id != "outfit"]
    panel_service.save_schema(seeded_world, _schema_dict(schema))
    schema = panel_service.load_schema(seeded_world)
    schema.npc.fields.append(FieldDefinition(id="outfit", label="当前服饰", type="text"))
    panel_service.save_schema(seeded_world, _schema_dict(schema))

    after = state_manager.get_character(seeded_world, "npc_001")
    assert "outfit" in after.state.custom_fields
    assert "outfit" not in after.state.orphaned


def test_different_worlds_can_have_completely_different_panels(world_a, world_b):
    schema = panel_service.load_schema(world_b)
    schema.protagonist.fields = [FieldDefinition(id="ship_name", label="船名", type="text")]
    schema.npc.fields = [FieldDefinition(id="rank", label="军阶", type="enum", options=["水手", "船长"])]
    panel_service.save_schema(world_b, _schema_dict(schema))

    ids_a = panel_service.load_schema(world_a).all_field_ids("protagonist", True)
    ids_b = panel_service.load_schema(world_b).all_field_ids("protagonist", True)
    assert "ship_name" in ids_b and "ship_name" not in ids_a
    assert "outfit" in ids_a and "outfit" not in ids_b


def test_frontend_panel_payload_reflects_schema_change(seeded_world):
    schema = panel_service.load_schema(seeded_world)
    schema.npc.fields.append(FieldDefinition(id="reputation", label="名声", type="number"))
    panel_service.save_schema(seeded_world, _schema_dict(schema))
    payload = panel_service.panels_for_frontend(seeded_world)
    labels = [f["label"] for f in payload["characters"]["npc_001"]["fields"]]
    assert "名声" in labels
    assert "key_value" in payload["available_types"]


def test_new_field_backfills_from_history_at_any_turn(seeded_world):
    """不是第 30 轮：随便玩几轮之后新增字段，也能按已有历史补出值。"""
    for action in ("我先放下伞", "我问起那封信", "我提起三年前"):
        story_engine.turn(seeded_world, TurnRequest(action=action))

    schema = panel_service.load_schema(seeded_world)
    schema.npc.fields.append(FieldDefinition(
        id="real_opinion", label="对主角的真实评价", description="按已有剧情推断", type="text",
    ))
    panel_service.save_schema(seeded_world, _schema_dict(schema))

    result = character_generator.backfill_fields(seeded_world, "npc_001")
    assert "real_opinion" in result["applied"]
    assert "real_opinion" in state_manager.get_character(seeded_world, "npc_001").state.custom_fields


def test_unrelated_story_does_not_rewrite_fixed_profile(seeded_world):
    before = state_manager.get_character(seeded_world, "npc_001").profile.model_dump()
    story_engine.turn(seeded_world, TurnRequest(action="我只是看了一眼窗外的雨"))
    after = state_manager.get_character(seeded_world, "npc_001").profile.model_dump()
    assert before == after

"""世界隔离：不同世界的数据绝不互相串。"""

from __future__ import annotations

import pytest

from backend.core import file_utils, panel_service, prompt_builder, state_manager, world_manager


def test_two_worlds_have_independent_character_files(world_a, world_b):
    state_manager.save_generated_character(
        world_a, {"name": "只在 A 出现的人", "core_description": "隔离测试", "custom_fields": {}},
    )
    assert "npc_001" in state_manager.list_character_ids(world_a)
    assert state_manager.list_character_ids(world_b) == []
    assert not (world_manager.world_dir(world_b) / "characters" / "npcs" / "npc_001").exists()


def test_each_world_has_its_own_panel_schema(world_a, world_b):
    schema_a = panel_service.load_schema(world_a)
    schema_a.npc.fields.append(
        __import__("backend.models.field", fromlist=["FieldDefinition"]).FieldDefinition(
            id="only_in_a", label="A 世界专属", type="text",
        )
    )
    panel_service.save_schema(world_a, {
        "version": 1,
        "same_fields_for_all_npcs": True,
        "protagonist": {"fields": [f.model_dump(mode="json") for f in schema_a.protagonist.fields]},
        "npc": {"fields": [f.model_dump(mode="json") for f in schema_a.npc.fields]},
        "overrides": {},
    })
    ids_a = panel_service.load_schema(world_a).all_field_ids("npc_001", False)
    ids_b = panel_service.load_schema(world_b).all_field_ids("npc_001", False)
    assert "only_in_a" in ids_a
    assert "only_in_a" not in ids_b


def test_prompt_context_only_contains_current_world(world_a, world_b):
    file_utils.atomic_write_text(world_manager.resolve(world_a, "setting.md"), "世界A独有标记_红伞")
    file_utils.atomic_write_text(world_manager.resolve(world_b, "setting.md"), "世界B独有标记_蓝灯")

    schema = panel_service.load_schema(world_a)
    request_a = prompt_builder.build_story_request(
        world_a, {}, schema, [], "摘要", "玩家行动", "地点", {}, 1,
    )
    assert "世界A独有标记_红伞" in request_a.task_prompt
    assert "世界B独有标记_蓝灯" not in request_a.task_prompt

    request_b = prompt_builder.build_story_request(
        world_b, {}, panel_service.load_schema(world_b), [], "摘要", "玩家行动", "地点", {}, 1,
    )
    assert "世界B独有标记_蓝灯" in request_b.task_prompt
    assert "世界A独有标记_红伞" not in request_b.task_prompt


def test_path_traversal_is_rejected(world_a):
    with pytest.raises(ValueError):
        world_manager.resolve("world_a", "..", "world_b", "world.yaml")
    with pytest.raises(ValueError):
        world_manager.validate_world_id("../etc")
    with pytest.raises(ValueError):
        world_manager.resolve("world_a", "..", "..", "config", "system_prompt.md")


def test_foreign_character_id_cannot_be_updated(world_a, world_b):
    from backend.core import validator
    from backend.models.story import CharacterUpdate, TurnOutput

    output = TurnOutput(
        narration="测试",
        character_updates=[CharacterUpdate(character_id="npc_999", custom_fields={"outfit": "入侵"})],
    )
    result = validator.validate_turn_output(
        output, panel_service.load_schema(world_a), {"protagonist": True},
        ["protagonist"], ["后备选项"],
    )
    assert result.output.character_updates == []
    assert any("不属于当前世界" in r for r in result.rejected)

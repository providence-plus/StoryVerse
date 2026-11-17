"""角色生成校验与「预览 -> 确认」流程。"""

from __future__ import annotations

import pytest

from backend.core import character_generator, state_manager, world_manager
from backend.core.field_registry import validate_value
from backend.core.validator import validate_generated_character
from backend.models.field import FieldDefinition


def _def(field_id: str, kind: str, **kwargs) -> FieldDefinition:
    return FieldDefinition(id=field_id, label=field_id, type=kind, **kwargs)


@pytest.mark.parametrize("kind,value,ok", [
    ("text", "一段文字", True),
    ("text", 123, False),
    ("long_text", "多行\n内容", True),
    ("number", 42, True),
    ("number", "7", True),
    ("number", True, False),
    ("boolean", True, True),
    ("boolean", "true", False),
    ("enum", "信任", True),
    ("enum", "不存在", False),
    ("list", ["钥匙", "信"], True),
    ("list", "钥匙", False),
    ("key_value", {"旧宅": "封存"}, True),
    ("key_value", ["旧宅"], False),
])
def test_all_seven_field_types_validate(kind, value, ok):
    kwargs = {"options": ["陌生", "信任"]} if kind == "enum" else {}
    valid, _ = validate_value(_def("f", kind, **kwargs), value)
    assert valid is ok


def test_number_range_is_enforced():
    valid, _ = validate_value(_def("lv", "number", min=0, max=10), 99)
    assert not valid


def test_generated_character_fills_every_defined_field(world_a):
    definitions = [_def("alpha", "text"), _def("beta", "number")]
    values, issues = validate_generated_character({"alpha": "有值", "unknown": "越界"}, definitions)
    assert values["alpha"] == "有值"
    assert "beta" in values           # 缺的字段自动补保守默认值
    assert "unknown" not in values    # 未定义字段被拒收
    assert any("未定义" in i for i in issues)


def test_preview_never_writes_character_files(world_a):
    preview = character_generator.generate_preview(
        world_a, "一个傲娇但内心善良的成年大小姐", count=2, is_protagonist=False,
    )
    assert len(preview["characters"]) == 2
    npc_root = world_manager.resolve(world_a, "characters", "npcs")
    assert list(npc_root.glob("*/profile.yaml")) == []


def test_main_character_preview_and_confirm(world_a):
    preview = character_generator.generate_preview(
        world_a, "被一封信召回故乡的调查者", is_protagonist=True,
    )
    assert preview["is_protagonist"] is True
    payload = preview["characters"][0]
    saved = character_generator.confirm_characters(world_a, [payload], is_protagonist=True)
    assert len(saved) == 1
    assert saved[0]["profile"]["is_protagonist"] is True
    assert (world_manager.resolve(world_a, "characters", "protagonist") / "profile.yaml").is_file()


def test_confirm_saves_every_schema_field(world_a):
    preview = character_generator.generate_preview(world_a, "一个话不多的成年人")
    payload = preview["characters"][0]
    character_generator.confirm_characters(world_a, [payload], is_protagonist=False)
    view = state_manager.get_character(world_a, "npc_001")
    expected = {f["id"] for f in view.fields}
    assert expected.issubset(set(view.state.custom_fields))

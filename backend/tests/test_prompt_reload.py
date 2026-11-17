"""Prompt 文件热生效与路径白名单。"""

from __future__ import annotations

import pytest

from backend.core import panel_service, prompt_builder


def test_saved_prompt_takes_effect_on_next_call(world_a):
    path = prompt_builder.PROMPT_FILES["system_prompt"]
    original = path.read_text(encoding="utf-8")
    try:
        prompt_builder.save_prompt("system_prompt", "测试标记_下一轮立即生效_XYZ")
        assert "测试标记_下一轮立即生效_XYZ" in prompt_builder.read_prompt("system_prompt")
        request = prompt_builder.build_story_request(
            world_a, {}, panel_service.load_schema(world_a), [], "摘要", "行动", "地点", {}, 1,
        )
        assert "测试标记_下一轮立即生效_XYZ" in request.system_prompt
    finally:
        # 测试不能留下对真实项目 Prompt 的修改。
        prompt_builder.save_prompt("system_prompt", original)


def test_all_prompt_files_are_readable_and_contain_no_fixed_field_ids():
    for name in prompt_builder.PROMPT_FILES:
        content = prompt_builder.read_prompt(name)
        assert content.strip(), f"{name} 不应为空"
    task_prompt = prompt_builder.read_prompt("story_generation")
    assert "physical_condition" not in task_prompt
    assert "hidden_thoughts" not in task_prompt
    assert "{{panel_schema_json}}" in task_prompt


def test_unknown_prompt_name_is_rejected():
    with pytest.raises(ValueError):
        prompt_builder.read_prompt("../../secret")
    with pytest.raises(ValueError):
        prompt_builder.save_prompt("arbitrary_file", "x")

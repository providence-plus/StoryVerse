"""一句话生成世界：预览不落盘、确认才写文件、扩写内容真的进 setting.md / rules.md。"""

from __future__ import annotations

import pytest

from backend.core import world_generator, world_manager
from backend.models.world import CreateWorldRequest


def test_preview_expands_description_and_does_not_touch_disk(worlds_root):
    """生成预览必须看到实质扩写，且 worlds/ 下一个目录都不许出现。"""
    result = world_generator.generate_preview("现代校园，一个大三男生和几位成年女性的故事", "甜向")
    preview = result["preview"]
    assert result["provider"] == "mock"
    assert len(preview["setting"]) > 200, "世界观背景必须是扩写后的长文，不是回显原话"
    assert "现代校园" in preview["setting"] or "大三" in preview["setting"]
    assert len(preview["rules"]) > 100
    assert len(preview["opening_choices"]) == 4
    assert [c["id"] for c in preview["opening_choices"]] == ["A", "B", "C", "D"]
    assert len(preview["opening_scene"]) > 50
    assert list(worlds_root.iterdir()) == [], "预览阶段绝不能创建世界目录"


def test_confirm_writes_expanded_setting_into_its_own_world(worlds_root):
    preview = world_generator.generate_preview("海边小镇的旧书店", "")["preview"]
    preview["name"] = "潮汐书屋"
    preview["opening_location"] = "旧书店二楼"
    meta = world_generator.confirm_world(preview)

    assert (worlds_root / meta.world_id / "setting.md").is_file()
    setting = (worlds_root / meta.world_id / "setting.md").read_text(encoding="utf-8")
    rules = (worlds_root / meta.world_id / "rules.md").read_text(encoding="utf-8")
    assert preview["setting"] in setting
    assert preview["rules"] in rules
    assert meta.opening_scene == preview["opening_scene"]
    assert meta.opening_choices == preview["opening_choices"]

    # 开场地点与当前地点同步，游戏页不会退回写死的默认地名。
    current = world_manager.resolve(meta.world_id, "state", "current.json")
    assert "旧书店二楼" in current.read_text(encoding="utf-8")


def test_two_generated_worlds_stay_isolated(worlds_root):
    a = world_generator.confirm_world(world_generator.generate_preview("现代校园", "")["preview"])
    b_preview = world_generator.generate_preview("深海科考站", "")["preview"]
    b_preview["name"] = "深潜"
    b = world_generator.confirm_world(b_preview)
    assert a.world_id != b.world_id
    assert "现代校园" in (worlds_root / a.world_id / "setting.md").read_text(encoding="utf-8")
    assert "深海科考站" in (worlds_root / b.world_id / "setting.md").read_text(encoding="utf-8")
    assert "深海科考站" not in (worlds_root / a.world_id / "setting.md").read_text(encoding="utf-8")


def test_bad_choices_are_repaired_and_empty_setting_rejected(worlds_root):
    preview = world_generator.generate_preview("山间旅馆", "")["preview"]
    preview["opening_choices"] = [{"id": "X", "text": "只有一个选项"}]
    meta = world_generator.confirm_world(preview)
    assert len(meta.opening_choices) == 4
    assert [c["id"] for c in meta.opening_choices] == ["A", "B", "C", "D"]

    preview["setting"] = "   "
    with pytest.raises(ValueError):
        world_generator.confirm_world(preview)


def test_manual_create_still_works_without_setting(worlds_root):
    meta = world_manager.create_world(CreateWorldRequest(name="手填世界"))
    assert (worlds_root / meta.world_id / "setting.md").is_file()
    assert meta.opening_choices == []
    assert meta.opening_scene == ""

"""回归：新增字段绝不能被静默丢弃，且前端读得到。"""

from __future__ import annotations

import re

from backend.core import panel_service


def test_chinese_field_id_is_normalized_and_field_survives(world_a):
    raw = panel_service._to_yaml_dict(panel_service.load_schema(world_a))
    raw["npc"]["fields"].append({
        "id": "我的新字段 001!", "label": "对主角的真实评价", "description": "心里想但没说出口的话",
        "type": "text", "visible": True, "update_policy": "story_driven",
    })
    warnings: list[str] = []
    saved = panel_service.save_schema(world_a, raw, warnings)

    labels = [f.label for f in saved.npc.fields]
    assert "对主角的真实评价" in labels, "字段不能因为 ID 不合法就消失"
    assert warnings, "自动修正必须明确告诉用户"
    assert "对主角的真实评价" in warnings[0]

    # 落盘后再读一次，模拟用户刷新页面。
    reloaded = panel_service.load_schema(world_a)
    assert "对主角的真实评价" in [f.label for f in reloaded.npc.fields]
    frontend = panel_service.panels_for_frontend(world_a)
    assert "对主角的真实评价" in [f["label"] for f in frontend["schema"]["npc"]["fields"]]


def test_bad_type_does_not_drop_field(world_a):
    raw = panel_service._to_yaml_dict(panel_service.load_schema(world_a))
    raw["protagonist"]["fields"].append({
        "id": "custom_ok", "label": "自定义项", "description": "测试",
        "type": "不存在的类型", "visible": True, "update_policy": "story_driven",
    })
    warnings: list[str] = []
    saved = panel_service.save_schema(world_a, raw, warnings)
    field = next(f for f in saved.protagonist.fields if f.id == "custom_ok")
    assert field.label == "自定义项"
    assert field.type.value == "text"
    assert any("自定义项" in w for w in warnings)


def test_duplicate_ids_get_unique_slug(world_a):
    raw = panel_service._to_yaml_dict(panel_service.load_schema(world_a))
    raw["npc"]["fields"].append({"id": "自定义", "label": "字段一", "type": "text"})
    raw["npc"]["fields"].append({"id": "自定义", "label": "字段二", "type": "text"})
    saved = panel_service.save_schema(world_a, raw)
    ids = [f.id for f in saved.npc.fields]
    assert len(ids) == len(set(ids)), "字段 ID 不能重复，否则前端渲染会串值"


def test_player_only_writes_label_and_id_is_generated(world_a):
    """玩家不填 ID 时由系统按字段名生成：合法、唯一、稳定，且明确告知。"""
    raw = panel_service._to_yaml_dict(panel_service.load_schema(world_a))
    raw["npc"]["fields"].append({"id": "", "label": "对主角的信任度", "type": "number"})
    raw["npc"]["fields"].append({"id": "", "label": "对主角的信任度", "type": "number"})
    warnings: list[str] = []
    saved = panel_service.save_schema(world_a, raw, warnings)

    added = [f for f in saved.npc.fields if f.label == "对主角的信任度"]
    assert len(added) == 2, "字段不能因为没填 ID 就消失"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", f.id) for f in added)
    assert added[0].id != added[1].id
    assert sum("自动生成 ID" in w for w in warnings) == 2

    # 之后玩家改字段名，ID 必须保持不变，历史字段值才不会丢。
    renamed = panel_service._to_yaml_dict(panel_service.load_schema(world_a))
    for f in renamed["npc"]["fields"]:
        if f["id"] == added[0].id:
            f["label"] = "信任程度"
    saved_again = panel_service.save_schema(world_a, renamed)
    assert any(f.id == added[0].id and f.label == "信任程度" for f in saved_again.npc.fields)

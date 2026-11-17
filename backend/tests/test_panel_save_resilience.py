"""回归：新增字段必须落盘可见，且不能被模型故障拖累。

用户报过「panel 新增一个字段，刷新后看不到」。根因之一是保存接口在写完
schema 之后同步调用 LLM 回填；模型没接通 / 超时 / 抛异常时，整个请求失败，
前端 catch 掉错误后不会刷新本地状态，看起来就像字段没保存。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.core import character_generator, panel_service
from backend.main import app


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_new_field_persists_after_refresh(world_a):
    """完整模拟：打开设计器 -> 新增字段 -> 保存 -> 刷新页面重新拉取。"""
    from backend.core import state_manager

    # 放一个 NPC，才能验证前端 DynamicPanel 真正渲染的字段列表。
    state_manager.save_generated_character(
        world_a, {"name": "测试配角", "core_description": "x", "custom_fields": {}},
    )
    client = _client()
    schema = client.get(f"/api/worlds/{world_a}/ui/panels").json()["schema"]
    schema["npc"]["fields"].append({
        "id": "trust_in_player", "label": "对主角的信任度", "description": "当前信任程度",
        "type": "number", "visible": True, "update_policy": "story_driven",
    })
    saved = client.put(f"/api/worlds/{world_a}/ui/panels", json={"schema": schema})
    assert saved.status_code == 200, saved.text
    assert "trust_in_player" in saved.json()["added_fields"]

    # 保存接口直接回传完整面板，前端拿它刷新，不必等下一次 GET。
    assert "trust_in_player" in [
        f["id"] for f in saved.json()["panels"]["schema"]["npc"]["fields"]
    ]

    # 模拟刷新页面：重新 GET，字段仍在，且进入前端实际渲染的列表。
    after = client.get(f"/api/worlds/{world_a}/ui/panels").json()
    assert "trust_in_player" in [f["id"] for f in after["schema"]["npc"]["fields"]]
    npc_panels = [c for c in after["characters"].values() if not c["is_protagonist"]]
    assert npc_panels, "临时世界应有一个 NPC"
    for panel in npc_panels:
        assert "trust_in_player" in [f["id"] for f in panel["fields"]]

    # 落盘后重新解析出来的是 Pydantic 模型，用属性访问而不是下标。
    loaded = panel_service.load_schema(world_a).npc.fields
    field = next(f for f in loaded if f.id == "trust_in_player")
    assert field.label == "对主角的信任度"
    assert field.type == "number"


def test_backfill_failure_does_not_break_save(world_a, monkeypatch):
    """回填模型抛异常，字段定义仍必须保存成功，并把原因作为 warning 回传。"""
    from backend.core import state_manager

    state_manager.save_generated_character(
        world_a, {"name": "测试主角", "core_description": "x", "custom_fields": {}},
        is_protagonist=True,
    )

    def boom(world_id, character_id):
        raise RuntimeError("模型服务不可用")

    monkeypatch.setattr(character_generator, "backfill_fields", boom)
    client = _client()
    schema = client.get(f"/api/worlds/{world_a}/ui/panels").json()["schema"]
    schema["protagonist"]["fields"].append({
        "id": "new_after_failure", "label": "模型挂了也要保存", "description": "x",
        "type": "text", "visible": True, "update_policy": "story_driven",
    })
    resp = client.put(f"/api/worlds/{world_a}/ui/panels", json={"schema": schema})
    assert resp.status_code == 200, resp.text
    assert "new_after_failure" in resp.json()["added_fields"]
    assert any("补齐" in w for w in resp.json()["warnings"])

    after = client.get(f"/api/worlds/{world_a}/ui/panels").json()
    assert "new_after_failure" in [f["id"] for f in after["schema"]["protagonist"]["fields"]]


def test_chinese_id_is_normalized_not_dropped(world_a):
    """用户手填中文 ID 时自动修正，绝不能静默丢字段。"""
    client = _client()
    schema = client.get(f"/api/worlds/{world_a}/ui/panels").json()["schema"]
    schema["npc"]["fields"].append({
        "id": "我的新字段 01", "label": "内心真实想法", "description": "没说出口的话",
        "type": "long_text", "visible": True, "update_policy": "story_driven",
    })
    resp = client.put(f"/api/worlds/{world_a}/ui/panels", json={"schema": schema})
    assert resp.status_code == 200
    after = client.get(f"/api/worlds/{world_a}/ui/panels").json()
    assert "内心真实想法" in [f["label"] for f in after["schema"]["npc"]["fields"]]
    assert any("自动改为" in w for w in resp.json()["warnings"])

"""HTTP 层端到端验收：从首页到完整互动，全部走 Mock Mode。

覆盖原验收 1–15 中可由接口自动验证的部分，以及动态字段的两条追加验收。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core import prompt_builder, world_manager
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "worlds"
    root.mkdir()
    # 所有世界操作都指向临时目录，绝不碰项目自带的 Demo 数据。
    monkeypatch.setattr(world_manager, "WORLDS_ROOT", root)
    return TestClient(app)


def test_full_mock_flow(client):
    assert client.get("/api/health").json()["status"] == "ok"

    # 首页能看到 Demo 世界。
    worlds = client.get("/api/worlds").json()["worlds"]
    assert any(w["world_id"] == "world_demo" for w in worlds)

    # 创建第二个世界。
    created = client.post("/api/worlds", json={
        "name": "第二世界", "description": "完全不同的设定", "genre": "奇幻",
    })
    assert created.status_code == 200
    world_id = created.json()["world_id"]
    assert len(client.get("/api/worlds").json()["worlds"]) == 2

    # 一句话生成主角预览 -> 确认保存。
    preview = client.post(f"/api/worlds/{world_id}/generate-character", json={
        "description": "被一封信召回故乡的调查者", "is_protagonist": True,
    })
    assert preview.status_code == 200
    assert preview.json()["characters"]
    saved = client.post(f"/api/worlds/{world_id}/characters", json={
        "characters": preview.json()["characters"], "is_protagonist": True,
    })
    assert saved.status_code == 200

    # 批量生成两个 NPC 预览 -> 确认保存。
    npc_preview = client.post(f"/api/worlds/{world_id}/generate-character", json={
        "description": "一群各怀心事的港口商人", "count": 2, "auto_relations": True,
    })
    assert npc_preview.status_code == 200
    assert len(npc_preview.json()["characters"]) == 2
    confirm = client.post(f"/api/worlds/{world_id}/characters", json={
        "characters": npc_preview.json()["characters"], "is_protagonist": False,
    })
    assert confirm.status_code == 200
    assert len(client.get(f"/api/worlds/{world_id}/characters").json()["characters"]) == 3

    # 完整回合：小说正文 + 四个选项。
    turn = client.post(f"/api/worlds/{world_id}/story/turn", json={"action": "我推门走进屋内"})
    assert turn.status_code == 200
    body = turn.json()
    assert body["output"]["narration"]
    assert len(body["output"]["choices"]) == 4
    assert [c["id"] for c in body["output"]["choices"]] == ["A", "B", "C", "D"]

    # 点选项 + 自定义输入。
    pick = client.post(f"/api/worlds/{world_id}/story/turn", json={
        "action": body["output"]["choices"][0]["text"], "choice_id": "A",
    })
    assert pick.status_code == 200
    custom = client.post(f"/api/worlds/{world_id}/story/turn", json={
        "action": "我忽然问起三年前那把钥匙",
    })
    assert custom.status_code == 200
    history = client.get(f"/api/worlds/{world_id}/history").json()["history"]
    assert history[-1]["player_action"] == "我忽然问起三年前那把钥匙"

    # 两个世界完全隔离：在 Demo 世界跑一轮，第二世界的历史不受影响。
    client.post("/api/worlds/world_demo/story/turn", json={"action": "雾港独有的行动标记"})
    other = client.get(f"/api/worlds/{world_id}/history").json()["history"]
    assert all("雾港独有的行动标记" not in h["player_action"] for h in other)
    assert client.get("/api/worlds/world_demo/history").json()["history"][-1]["player_action"] == "雾港独有的行动标记"

    # 存档分支：新建、继续、读取旧存档。
    branch = client.post(f"/api/worlds/{world_id}/saves", json={"name": "分支一"})
    assert branch.status_code == 200
    client.post(f"/api/worlds/{world_id}/story/turn", json={"action": "只在分支一出现的行动"})
    client.post(f"/api/worlds/world_demo/saves/auto/load")
    loaded = client.post(f"/api/worlds/{world_id}/saves/{branch.json()['save_id']}/load")
    assert loaded.status_code == 200
    restored = client.get(f"/api/worlds/{world_id}/history").json()["history"]
    assert "只在分支一出现的行动" in [h["player_action"] for h in restored]

    # 模型未连接时给出明确错误，而不是伪装成功。
    probe = client.post("/api/settings/test-connection").json()
    assert probe["ok"] is False
    assert "Mock" in probe["message"] or "模拟" in probe["message"]


def test_panel_designer_adds_custom_field_and_backfills(client):
    world = client.post("/api/worlds", json={"name": "字段测试世界"}).json()["world_id"]
    preview = client.post(f"/api/worlds/{world}/generate-character", json={
        "description": "一个话不多的成年人", "is_protagonist": False,
    }).json()["characters"]
    client.post(f"/api/worlds/{world}/characters", json={
        "characters": preview, "is_protagonist": False,
    })
    client.post(f"/api/worlds/{world}/story/turn", json={"action": "先走一轮剧情"})

    panels = client.get(f"/api/worlds/{world}/ui/panels").json()["schema"]
    panels["npc"]["fields"].append({
        "id": "totally_custom_field", "label": "我随便加的字段",
        "description": "由用户在游戏途中新增，代码里完全不存在", "type": "number",
        "visible": True, "update_policy": "story_driven", "min": 0, "max": 100,
    })
    saved = client.put(f"/api/worlds/{world}/ui/panels", json={"schema": panels})
    assert saved.status_code == 200
    assert "totally_custom_field" in saved.json()["added_fields"]

    chars = client.get(f"/api/worlds/{world}/characters").json()["panels"]["characters"]
    npc = next(data for cid, data in chars.items() if not data["is_protagonist"])
    assert "totally_custom_field" in [f["id"] for f in npc["fields"]]
    assert "totally_custom_field" in npc["values"]  # 已按历史补齐


def test_prompt_edit_takes_effect_on_next_call(client):
    path = prompt_builder.PROMPT_FILES["story_generation"]
    original = path.read_text(encoding="utf-8")
    try:
        response = client.put("/api/prompts/story_generation", json={"content": "即时生效标记_ABC\n{{panel_schema_json}}"})
        assert response.status_code == 200
        assert "即时生效标记_ABC" in prompt_builder.read_prompt("story_generation")
    finally:
        prompt_builder.save_prompt("story_generation", original)


def test_settings_never_leak_api_key(client, tmp_path, monkeypatch):
    from backend.core import settings_service

    monkeypatch.setattr(settings_service, "SETTINGS_PATH", tmp_path / "settings.yaml")
    client.put("/api/settings", json={"llm": {"api_key": "super-secret-key", "model": "test-model"}})
    public = client.get("/api/settings").json()
    assert "super-secret-key" not in str(public)
    assert public["api_key"] == ""

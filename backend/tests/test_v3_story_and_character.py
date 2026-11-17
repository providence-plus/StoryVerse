"""v3：开始故事、场景表单、编辑/删除/退场、开场画面注入、首次登场。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.core import bible_service, panel_service, prompt_builder, state_manager, world_manager


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _npc_id(world_id: str) -> str:
    return next(c for c in state_manager.list_character_ids(world_id) if c != "protagonist")


def test_start_world_sets_flag_and_is_idempotent(world_a):
    client = _client()
    assert world_manager.get_meta(world_a).story_started is False
    r = client.post(f"/api/worlds/{world_a}/start")
    assert r.status_code == 200
    assert r.json()["story_started"] is True
    r2 = client.post(f"/api/worlds/{world_a}/start")
    assert r2.status_code == 200
    assert r2.json()["story_started"] is True


def test_edit_character_locked_after_start(seeded_world):
    client = _client()
    chars = client.get(f"/api/worlds/{seeded_world}/characters").json()["characters"]
    proto = next(c for c in chars if c["profile"]["is_protagonist"])
    cid = proto["profile"]["character_id"]
    r = client.put(f"/api/worlds/{seeded_world}/characters/{cid}",
                   json={"name": "改名主角", "core_description": "改过", "bible": {}})
    assert r.status_code == 200
    assert r.json()["profile"]["name"] == "改名主角"
    client.post(f"/api/worlds/{seeded_world}/start")
    r2 = client.put(f"/api/worlds/{seeded_world}/characters/{cid}",
                    json={"name": "再改", "core_description": "", "bible": {}})
    assert r2.status_code == 409


def test_delete_character_cleans_references(seeded_world):
    client = _client()
    npc = _npc_id(seeded_world)
    bible = bible_service.normalize_bible({
        "relations": [{
            "target_character_id": npc,
            "target_name": "测试配角",
            "relation": {"player_input": "朋友", "text": "朋友"},
            "past_events": [],
            "planned_events": [{"id": "", "text": "一起吃饭"}],
        }]
    }, "protagonist")
    state_manager.edit_character(seeded_world, "protagonist",
                                 {"name": "测试主角", "core_description": "", "bible": bible})
    r = client.delete(f"/api/worlds/{seeded_world}/characters/{npc}")
    assert r.status_code == 200
    assert npc not in state_manager.list_character_ids(seeded_world)
    proto = state_manager.get_character(seeded_world, "protagonist")
    assert proto.profile.bible["relations"] == []


def test_delete_character_locked_after_start(seeded_world):
    client = _client()
    npc = _npc_id(seeded_world)
    client.post(f"/api/worlds/{seeded_world}/start")
    r = client.delete(f"/api/worlds/{seeded_world}/characters/{npc}")
    assert r.status_code == 409


def test_retire_character_filters_from_prompt(seeded_world):
    client = _client()
    npc = _npc_id(seeded_world)
    r = client.post(f"/api/worlds/{seeded_world}/characters/{npc}/retire", json={"retired": True})
    assert r.status_code == 200
    assert r.json()["state"]["retired"] is True
    chars = state_manager.characters_for_prompt(seeded_world)
    assert npc not in chars
    assert "protagonist" in chars
    client.post(f"/api/worlds/{seeded_world}/characters/{npc}/retire", json={"retired": False})
    assert npc in state_manager.characters_for_prompt(seeded_world)


def test_end_scene_clears_choices(seeded_world):
    client = _client()
    client.post(f"/api/worlds/{seeded_world}/start")
    r = client.post(f"/api/worlds/{seeded_world}/story/turn",
                    json={"action": "决定结束眼前这一幕", "end_scene": True})
    assert r.status_code == 200
    body = r.json()
    assert body["turn"] == 1
    assert body["output"]["choices"] == []


def test_scene_description_injected_into_prompt(seeded_world):
    chars = state_manager.characters_for_prompt(seeded_world)
    schema = panel_service.load_schema(seeded_world)
    req = prompt_builder.build_story_request(
        world_id=seeded_world, characters=chars, schema=schema,
        recent_history=[], summary="", player_action="开始", current_location="",
        relationships={}, turn=1, scene_description="入夜后的旧码头",
    )
    assert "入夜后的旧码头" in req.task_prompt


def test_scene_location_and_characters_flow_through_turn(seeded_world):
    client = _client()
    client.post(f"/api/worlds/{seeded_world}/start")
    r = client.post(f"/api/worlds/{seeded_world}/story/turn", json={
        "action": "走进码头", "scene_description": "入夜后的旧码头", "location": "旧码头",
        "active_character_ids": ["protagonist"],
    })
    assert r.status_code == 200
    assert r.json()["current_location"] == "旧码头"


def test_pending_introduction_set_and_cleared(seeded_world):
    client = _client()
    client.post(f"/api/worlds/{seeded_world}/start")
    r = client.post(f"/api/worlds/{seeded_world}/characters",
                    json={"characters": [{"name": "新角色", "core_description": "", "custom_fields": {}}],
                          "is_protagonist": False})
    assert r.status_code == 200
    saved = r.json()["saved"]
    new_cid = saved[0]["profile"]["character_id"]
    assert saved[0]["state"]["pending_introduction"] is True
    client.post(f"/api/worlds/{seeded_world}/story/turn",
                json={"action": "开始", "active_character_ids": [new_cid]})
    assert state_manager.get_character(seeded_world, new_cid).state.pending_introduction is False


def test_is_story_started_compat_with_old_world(world_a):
    # 旧世界没有 story_started，但 turn>0 就应视为已开局。
    assert state_manager.is_story_started(world_a) is False
    state = state_manager.get_current_state(world_a)
    state.turn = 3
    state_manager.write_current_state(world_a, state)
    assert state_manager.is_story_started(world_a) is True

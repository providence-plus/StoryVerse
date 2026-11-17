"""设定基线落盘 / 向后兼容 / 预定事件注入与存档分支 / 上下文长度保护。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core import (
    bible_service, file_utils, panel_service, prompt_builder, save_manager,
    state_manager, world_manager,
)
from backend.main import app


def _payload(name: str, bible: dict | None = None) -> dict:
    return {"name": name, "core_description": f"{name}的定位", "custom_fields": {}, "bible": bible or {}}


def _bible(event_text: str = "在旧宅重逢并把钥匙还回去") -> dict:
    return {
        "items": {
            "appearance": {"player_input": "红发", "text": "红发，常穿洗旧的风衣。"},
            "personality": {"player_input": "", "text": "戒备心重。"},
            "background": {"player_input": "", "text": "在旧宅长大。"},
            "major_events": {"player_input": "", "text": "三年前那场事故。"},
        },
        "relations": [{
            "target_character_id": "protagonist",
            "target_name": "主角",
            "relation": {"player_input": "大学同学", "text": "大学同学，三年没联系。"},
            "past_events": [{"player_input": "毕业那天的争执", "text": "毕业那天为钥匙争执过。"}],
            "planned_events": [{"id": "", "text": event_text}],
        }],
    }


def _story_request(world_id: str):
    characters = state_manager.characters_for_prompt(world_id, None)
    schema = panel_service.load_schema(world_id)
    return prompt_builder.build_story_request(
        world_id=world_id, characters=characters, schema=schema,
        recent_history=[], summary="摘要", player_action="推门进去",
        current_location="门口", relationships={}, turn=1,
    )


def test_bible_roundtrip_to_profile_yaml(world_a):
    view = state_manager.save_generated_character(world_a, _payload("沈知微", _bible()))
    assert view.profile.bible["items"]["appearance"]["text"] == "红发，常穿洗旧的风衣。"
    assert view.profile.bible["relations"][0]["planned_events"][0]["id"].startswith("evt_")
    path = world_manager.resolve(world_a, "characters", "npcs", view.profile.character_id, "profile.yaml")
    raw = file_utils.read_yaml(path, {})
    assert raw["bible"]["items"]["appearance"]["player_input"] == "红发"
    assert raw["bible"]["relations"][0]["target_character_id"] == "protagonist"


def test_bible_survives_api_confirm_roundtrip(world_a):
    """审核 M6 链路：前端 dict -> confirm_characters -> profile.yaml -> GET 可读。"""
    client = TestClient(app, raise_server_exceptions=False)
    payload = _payload("顾晚棠", _bible())
    payload.update({"plot_hooks": [], "opening_situation": "", "is_protagonist": False})
    response = client.post(f"/api/worlds/{world_a}/characters", json={
        "characters": [payload], "is_protagonist": False,
    })
    assert response.status_code == 200, response.text
    saved = response.json()["saved"][0]
    assert saved["profile"]["name"] == "顾晚棠"
    assert saved["profile"]["bible"]["relations"][0]["planned_events"][0]["text"] == "在旧宅重逢并把钥匙还回去"
    listed = client.get(f"/api/worlds/{world_a}/characters").json()["characters"]
    assert listed[0]["profile"]["bible"]["items"]["appearance"]["player_input"] == "红发"


def test_old_character_without_bible_is_compatible(world_a):
    folder = world_manager.resolve(world_a, "characters", "npcs", "npc_old")
    folder.mkdir(parents=True, exist_ok=True)
    file_utils.atomic_write_yaml(folder / "profile.yaml", {
        "character_id": "npc_old", "name": "老角色", "is_protagonist": False,
        "core_description": "旧版数据，没有 bible 字段",
    })
    file_utils.atomic_write_json(folder / "state.json", {
        "character_id": "npc_old", "custom_fields": {}, "_orphaned": {},
    })
    view = state_manager.get_character(world_a, "npc_old")
    assert view.profile.bible == {}
    assert "旧版数据" in view.profile.core_description


def test_planned_events_injected_into_story_prompt(world_a):
    state_manager.save_generated_character(world_a, _payload("主角"), is_protagonist=True)
    state_manager.save_generated_character(world_a, _payload("沈知微", _bible()))
    events = bible_service.collect_planned_events(world_a)
    assert len(events) == 1
    assert "在旧宅重逢" in events[0]["text"]

    request = _story_request(world_a)
    assert "在旧宅重逢并把钥匙还回去" in request.task_prompt
    assert "预定事件怎么推进" in request.task_prompt
    # 玩家原话也作为事实锚句进了人物卡。
    assert "红发" in request.task_prompt

    state_manager.set_planned_event_done(world_a, events[0]["id"], True)
    assert "在旧宅重逢并把钥匙还回去" not in _story_request(world_a).task_prompt
    state_manager.set_planned_event_done(world_a, events[0]["id"], False)
    assert "在旧宅重逢并把钥匙还回去" in _story_request(world_a).task_prompt


def test_completed_events_follow_save_branch(world_a):
    """审核 M5：完成态存 current.json，读档回到过去时跟着回到未完成。"""
    state_manager.save_generated_character(world_a, _payload("主角"), is_protagonist=True)
    state_manager.save_generated_character(world_a, _payload("沈知微", _bible()))
    event_id = bible_service.collect_planned_events(world_a)[0]["id"]
    save_manager.auto_save(world_a)
    state_manager.set_planned_event_done(world_a, event_id, True)
    assert event_id in state_manager.get_current_state(world_a).completed_planned_events
    save_manager.load_save(world_a, "auto")
    assert event_id not in state_manager.get_current_state(world_a).completed_planned_events


def test_event_ids_are_unique_across_relation_cards():
    raw = {"items": {}, "relations": [
        {"target_character_id": "a", "planned_events": [{"text": "同一句话"}]},
        {"target_character_id": "b", "planned_events": [{"text": "同一句话"}]},
    ]}
    normalized = bible_service.normalize_bible(raw, "npc_001")
    ids = [evt["id"] for rel in normalized["relations"] for evt in rel["planned_events"]]
    assert len(ids) == 2 and len(set(ids)) == 2
    # 同一句话重复保存，id 不变，完成标记不会失效。
    again = bible_service.normalize_bible(normalized, "npc_001")
    assert [evt["id"] for rel in again["relations"] for evt in rel["planned_events"]] == ids


def test_patch_rejects_unknown_event(world_a):
    with pytest.raises(ValueError):
        state_manager.set_planned_event_done(world_a, "evt_not_exist", True)


def test_prompt_length_stays_bounded_with_many_full_bibles(world_a):
    """审核 M3：8 个角色各带超长 bible，也不能把 prompt 撑过上下文窗口。"""
    long_text = "具体细节与来龙去脉。" * 1200
    heavy = {
        "items": {key: {"player_input": long_text, "text": long_text} for key in bible_service.ITEM_KEYS},
        "relations": [],
    }
    for index in range(8):
        state_manager.save_generated_character(world_a, _payload(f"配角{index}", heavy))
    request = _story_request(world_a)
    total = len(request.system_prompt) + len(request.task_prompt) + len(request.context)
    # 字符数近似：留足余量给 tokenizer 误差、max_tokens 与世界设定。
    assert total < 45000, f"剧情 prompt 过长：{total} 字符"
    assert "（后略）" in request.task_prompt or "其余设定略" in request.task_prompt

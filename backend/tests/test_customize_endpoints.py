"""扫描 M4：四个新 HTTP 端点的覆盖、截断修复分支、预览→落盘端到端、真实窗口预算。

这里的「真实窗口预算」不再只算 task_prompt 的长度，而是用
llm_provider.prompt_chars() 量出**真正发给模型的全部文本**（system + user + history
+ user_action + 拼装开销），再加上 max_tokens，一起和 -c 32768 比对。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.core import (
    bible_service, file_utils, llm_provider, panel_service, prompt_builder,
    state_manager, world_manager,
)
from backend.core.llm_provider import (
    CONTEXT_WINDOW_TOKENS, GenerationResult, LLMRequest, effective_max_tokens, prompt_chars,
)
from backend.main import app


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _world_form() -> dict:
    return {
        "name": "雨季来信", "genre": "现代校园 · 甜向",
        "description": "一个关于迟到的告白的故事",
        "setting": "现代校园，红砖教学楼，常年下雨",
        "rules": "所有角色都是成年人",
        "opening_location": "礼堂后台走廊", "opening_scene": "",
        "opening_choices": [{"id": "A", "text": "先开口问一句"}],
        "protagonist_hint": "", "npc_hints": [],
        "writer_style": "克制慢节奏",
    }


def _character_form(target_id: str = "protagonist") -> dict:
    return {
        "name": "沈知微",
        "core_description": "把旧宅钥匙攥了三年的人",
        "items": {
            "appearance": {"player_input": "红发，个子很高", "text": ""},
            "personality": {"player_input": "戒备心很重", "text": ""},
            "background": {"player_input": "", "text": ""},
            "major_events": {"player_input": "", "text": ""},
        },
        "relations": [{
            "target_character_id": target_id,
            "target_name": "主角",
            "relation": {"player_input": "大学同学，三年没联系", "text": ""},
            "past_events": [{"player_input": "毕业那天的争执", "text": ""}],
            "planned_events": [
                {"id": "", "text": "在旧宅重逢并把钥匙还回去"},
                {"id": "", "text": "一起查出当年那封信是谁写的"},
            ],
        }],
    }


def _seed_owner_with_events(world_id: str, event_text: str = "在旧宅重逢并把钥匙还回去"):
    """建一个主角（作为关系目标）和一个带预定事件的 NPC，返回 (owner_id, event_id)。"""
    state_manager.save_generated_character(
        world_id, {"name": "主角", "core_description": "", "custom_fields": {}},
        is_protagonist=True,
    )
    long_text = event_text + "，并且把当年没说完的话当面说完。" * 12
    bible = {
        "items": {key: {"player_input": "", "text": ""} for key in bible_service.ITEM_KEYS},
        "relations": [{
            "target_character_id": "protagonist", "target_name": "主角",
            "relation": {"player_input": "", "text": "旧识"},
            "past_events": [],
            "planned_events": [{"id": "", "text": long_text}],
        }],
    }
    view = state_manager.save_generated_character(
        world_id,
        {"name": "沈知微", "core_description": "", "custom_fields": {}, "bible": bible},
    )
    owner_id = view.profile.character_id
    event_id = bible_service.collect_planned_events(world_id)[0]["id"]
    return owner_id, event_id, long_text


# ---------------------------------------------------------------------------
# 四个新端点
# ---------------------------------------------------------------------------

def test_post_world_customize_preview_writes_nothing(worlds_root):
    client = _client()
    before = sorted(str(p) for p in worlds_root.rglob("*"))
    response = client.post("/api/worlds/customize-preview", json=_world_form())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "mock"
    assert body["degraded"] is False
    assert "现代校园，红砖教学楼，常年下雨" in body["preview"]["setting"]
    assert len(body["preview"]["opening_choices"]) == 4
    assert body["preview"]["writer_style"] == "克制慢节奏"
    assert sorted(str(p) for p in worlds_root.rglob("*")) == before, "预览绝不落盘"


def test_post_character_customize_preview_preserves_player_facts(world_a):
    client = _client()
    response = client.post(f"/api/worlds/{world_a}/customize-character-preview", json={
        "inputs": _character_form(), "is_protagonist": False,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "沈知微"
    assert "红发，个子很高" in body["items"]["appearance"]["text"]
    rel = body["relations"][0]
    assert rel["target_character_id"] == "protagonist"
    assert [e["text"] for e in rel["planned_events"]] == [
        "在旧宅重逢并把钥匙还回去", "一起查出当年那封信是谁写的",
    ]
    # 预览阶段不生成正式 id（正式 id 在确认保存时按角色 ID 重算）。
    assert all(e["id"] == "" for e in rel["planned_events"])
    # 预览不写任何人物文件。
    assert not list(world_manager.resolve(world_a, "characters", "npcs").glob("*/profile.yaml"))


def test_character_customize_preview_rejects_illegal_world_id():
    client = _client()
    assert client.post("/api/worlds/..%2Fevil/customize-character-preview",
                       json={"inputs": {}}).status_code in (400, 404, 422)


def test_get_planned_events_returns_full_text(world_a):
    """L7：列表页要显示玩家原文，不能拿注入剧情时的 160 字裁剪版。"""
    owner_id, event_id, long_text = _seed_owner_with_events(world_a)
    client = _client()
    body = client.get(f"/api/worlds/{world_a}/planned-events").json()
    assert body["completed"] == []
    assert len(body["events"]) == 1
    evt = body["events"][0]
    assert evt["id"] == event_id
    assert evt["owner_id"] == owner_id
    assert evt["text"] == long_text, "列表页必须是完整文本"
    assert "（后略）" not in evt["text"]
    # 注入剧情的那份仍然被裁短。
    prompt_version = bible_service.collect_planned_events(world_a)[0]["text"]
    assert len(prompt_version) < len(long_text)
    assert "（后略）" in prompt_version


def test_patch_planned_event_done_roundtrip(world_a):
    owner_id, event_id, _ = _seed_owner_with_events(world_a)
    client = _client()
    marked = client.patch(
        f"/api/worlds/{world_a}/characters/{owner_id}/planned-events/{event_id}",
        json={"done": True},
    )
    assert marked.status_code == 200, marked.text
    assert event_id in marked.json()["completed"]
    assert event_id in state_manager.get_current_state(world_a).completed_planned_events
    assert client.get(f"/api/worlds/{world_a}/planned-events").json()["completed"] == [event_id]

    reopened = client.patch(
        f"/api/worlds/{world_a}/characters/{owner_id}/planned-events/{event_id}",
        json={"done": False},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["completed"] == []


def test_patch_rejects_wrong_character_and_unknown_event(world_a):
    """M6：character_id 不能只是路径装饰，必须真的属于本世界且拥有这条事件。"""
    owner_id, event_id, _ = _seed_owner_with_events(world_a)
    client = _client()
    assert client.patch(
        f"/api/worlds/{world_a}/characters/not_a_character/planned-events/{event_id}",
        json={"done": True},
    ).status_code == 404
    assert client.patch(
        f"/api/worlds/{world_a}/characters/protagonist/planned-events/{event_id}",
        json={"done": True},
    ).status_code == 404, "事件挂在 NPC 名下，不能用主角 id 去改"
    assert client.patch(
        f"/api/worlds/{world_a}/characters/{owner_id}/planned-events/evt_not_exist",
        json={"done": True},
    ).status_code == 404
    assert state_manager.get_current_state(world_a).completed_planned_events == []


def test_all_new_endpoints_reject_illegal_world_id():
    client = _client()
    bad = "/api/worlds/..%2Fevil"
    assert client.get(f"{bad}/planned-events").status_code in (400, 404, 422)
    assert client.patch(f"{bad}/characters/x/planned-events/y",
                        json={"done": True}).status_code in (400, 404, 422)
    assert client.post(f"{bad}/customize-character-preview",
                       json={"inputs": {}}).status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# 截断修复分支
# ---------------------------------------------------------------------------

TRUNCATED_WORLD_JSON = '''{
  "name": "雨季来信",
  "genre": "现代校园",
  "setting": "红砖教学楼常年下雨，故事从"'''


def test_truncated_json_repair_branch_feeds_preview(worlds_root, monkeypatch):
    """长 JSON 被 max_tokens 砍在字符串中间：必须修复、标降级、补齐缺失键、留原文。"""
    from backend.core import customization
    from backend.core.story_parser import parse_json_object

    data, reason = parse_json_object(TRUNCATED_WORLD_JSON)
    assert data is not None
    assert "截断" in reason

    monkeypatch.setattr(
        customization, "generate",
        lambda request: GenerationResult(text=TRUNCATED_WORLD_JSON, provider="fake", model="fake"),
    )
    result = customization.generate_world_preview(_world_form())
    assert result["degraded"] is True
    assert result["raw_text"] == TRUNCATED_WORLD_JSON, "降级时要把模型原文交给前端"
    assert any("缺失" in w or "修复" in w for w in result["warnings"])
    preview = result["preview"]
    assert "红砖教学楼" in preview["setting"]
    assert preview["rules"], "被截掉的键要用玩家原话/安全占位补齐"
    assert len(preview["opening_choices"]) == 4
    assert preview["writer_style"] == "克制慢节奏"


def test_truncated_character_json_marks_degraded(world_a, monkeypatch):
    from backend.core import customization

    truncated = '{"name": "沈知微", "items": {"appearance": {"text": "红发，写到一半被切'
    monkeypatch.setattr(
        customization, "generate",
        lambda request: GenerationResult(text=truncated, provider="fake", model="fake"),
    )
    result = customization.generate_character_preview(world_a, _character_form())
    assert result["degraded"] is True
    assert result["raw_text"] == truncated
    assert result["name"] == "沈知微"
    assert result["relations"][0]["planned_events"][0]["text"] == "在旧宅重逢并把钥匙还回去"
    assert all(result["items"][key]["text"] for key in bible_service.ITEM_KEYS)


# ---------------------------------------------------------------------------
# 预览 → 确认 → 落盘 端到端
# ---------------------------------------------------------------------------

def test_preview_confirm_persists_bible_and_events(world_a):
    """玩家事实锚句一路走到 profile.yaml，事件拿到正式 id，PATCH 能标完成。"""
    client = _client()
    preview = client.post(f"/api/worlds/{world_a}/customize-character-preview", json={
        "inputs": _character_form(), "is_protagonist": False,
    }).json()

    payload = {
        "name": preview["name"],
        "core_description": preview["core_description"],
        "custom_fields": {},
        "plot_hooks": [],
        "opening_situation": "",
        "is_protagonist": False,
        "bible": {"items": preview["items"], "relations": preview["relations"]},
    }
    saved = client.post(f"/api/worlds/{world_a}/characters", json={
        "characters": [payload], "is_protagonist": False,
    })
    assert saved.status_code == 200, saved.text
    view = saved.json()["saved"][0]
    cid = view["profile"]["character_id"]

    # 落盘后重读：原话与定稿都在，正式 id 由后端生成。
    profile = file_utils.read_yaml(
        world_manager.resolve(world_a, "characters", "npcs", cid, "profile.yaml"), {},
    )
    assert profile["bible"]["items"]["appearance"]["player_input"] == "红发，个子很高"
    assert profile["bible"]["relations"][0]["planned_events"][0]["id"].startswith("evt_")

    events = client.get(f"/api/worlds/{world_a}/planned-events").json()["events"]
    assert len(events) == 2
    assert all(e["id"].startswith("evt_") for e in events)
    assert events[0]["owner_id"] == cid

    marked = client.patch(
        f"/api/worlds/{world_a}/characters/{cid}/planned-events/{events[0]['id']}",
        json={"done": True},
    )
    assert marked.status_code == 200, marked.text
    assert events[0]["id"] in state_manager.get_current_state(world_a).completed_planned_events


def test_direct_create_pads_opening_choices(world_a):
    """L1：不调用 AI 直接创建，只填一两个选项也要补齐成四项 A/B/C/D。"""
    client = _client()
    response = client.post("/api/worlds", json={
        "name": "手填世界", "description": "", "genre": "",
        "opening_choices": [{"id": "B", "text": "只写了一个选项"}],
    })
    assert response.status_code == 200, response.text
    assert len(response.json()["opening_choices"]) == 4
    assert [c["id"] for c in response.json()["opening_choices"]] == ["A", "B", "C", "D"]
    # 完全自己写的入口不传选项时保持空，游戏页会退回中性选项。
    empty = client.post("/api/worlds", json={"name": "空白世界"})
    assert empty.json()["opening_choices"] == []


def test_settings_clamps_max_tokens_to_window_half():
    """M6：max_tokens 不能等于 -c，否则输出把窗口占满、prompt 被左截断。"""
    from backend.core import settings_service

    capped = settings_service.save_settings({"max_tokens": 999999})
    assert capped["max_tokens"] == settings_service.MAX_TOKENS_CEILING
    assert capped["max_tokens"] < CONTEXT_WINDOW_TOKENS
    # 直接读盘（手改配置文件）也要被夹住。
    assert settings_service.llm_settings()["max_tokens"] <= settings_service.MAX_TOKENS_CEILING


# ---------------------------------------------------------------------------
# 真实上下文窗口预算
# ---------------------------------------------------------------------------

def _fits_window(request: LLMRequest) -> bool:
    # 用「真正发给模型的」输出预算，而不是可能被直接构造出来的超大原值。
    return prompt_chars(request) + effective_max_tokens(request) <= CONTEXT_WINDOW_TOKENS


def _heavy_world(world_a, monkeypatch, tmp_path):
    """把所有可膨胀来源同时写满：模板、全局/世界 Prompt、设定文件、角色、历史、关系。"""
    long_text = "具体细节、来龙去脉与可写进正文的抓手。" * 900

    # 全局铁律被玩家写到超限。
    fake_global = tmp_path / "global.md"
    fake_global.write_text("全局基础写作铁律（必须永远在最前面）。\n" + long_text, encoding="utf-8")
    monkeypatch.setitem(prompt_builder.PROMPT_FILES, "system_prompt", fake_global)
    # 任务模板也写满，但仍保留格式红线。
    fake_template = tmp_path / "story.md"
    fake_template.write_text(
        prompt_builder.read_prompt("story_generation") + "\n" + long_text
        + "\n{{world_context}}{{characters_json}}{{panel_schema_json}}{{character_fields_json}}"
        + "{{relationships_json}}{{summary}}{{recent_history}}{{planned_events}}{{player_action}}",
        encoding="utf-8",
    )
    monkeypatch.setitem(prompt_builder.PROMPT_FILES, "story_generation", fake_template)

    file_utils.atomic_write_text(world_manager.resolve(world_a, "setting.md"), long_text)
    file_utils.atomic_write_text(world_manager.resolve(world_a, "rules.md"), long_text)
    file_utils.atomic_write_text(world_manager.resolve(world_a, "system_prompt.md"), long_text)
    for index in range(4):
        file_utils.atomic_write_text(
            world_manager.resolve(world_a, "locations", f"place{index}.md"), long_text,
        )

    heavy_bible = {
        "items": {key: {"player_input": long_text, "text": long_text}
                  for key in bible_service.ITEM_KEYS},
        "relations": [{
            "target_character_id": "protagonist", "target_name": "主角",
            "relation": {"player_input": long_text, "text": long_text},
            "past_events": [{"player_input": long_text, "text": long_text}],
            "planned_events": [{"id": "", "text": "把钥匙还回去"}],
        }],
    }
    state_manager.save_generated_character(
        world_a, {"name": "主角", "core_description": long_text, "custom_fields": {}},
        is_protagonist=True,
    )
    for index in range(8):
        state_manager.save_generated_character(world_a, {
            "name": f"配角{index}", "core_description": long_text,
            "custom_fields": {"appearance": long_text, "hidden_thoughts": long_text},
            "bible": heavy_bible,
        })
    return long_text


def test_every_builder_fits_context_window_with_all_sources_inflated(world_a, monkeypatch, tmp_path):
    """M4：覆盖 LLMRequest 全输入 + max_tokens，且模板/风格/关系/历史/字段全部膨胀。"""
    long_text = _heavy_world(world_a, monkeypatch, tmp_path)
    characters = state_manager.characters_for_prompt(world_a, None)
    schema = panel_service.load_schema(world_a)
    relationships = {cid: {"亲密度": long_text} for cid in characters}
    history = [{"narration": long_text, "player_action": long_text,
                "dialogues": [], "choices": []} for _ in range(20)]

    requests = {
        "story": prompt_builder.build_story_request(
            world_a, characters, schema, history, long_text, long_text,
            "门口", relationships, 20,
        ),
        "world": prompt_builder.build_world_request(long_text, long_text),
        "world_customize": prompt_builder.build_world_customize_request(
            {"setting": long_text, "rules": long_text, "description": long_text}, long_text,
        ),
        "character_customize": prompt_builder.build_character_customize_request(
            world_a, {"name": "沈知微", "items": {"appearance": {"player_input": long_text}},
                      "relations": []},
            [f.to_prompt_dict() for f in schema.fields_for("npc_001", False)],
            {cid: {"name": cid} for cid in characters},
        ),
        "character": prompt_builder.build_character_request(
            world_a, long_text,
            [f.to_prompt_dict() for f in schema.fields_for("npc_001", False)],
            False, 8, {"name": "主角"}, True,
        ),
        "memory": prompt_builder.build_memory_request(
            world_a, long_text, history, long_text, long_text, [long_text],
        ),
        "backfill": prompt_builder.build_backfill_request(
            world_a, {"name": "沈知微"}, {}, [], long_text, history, history, long_text, 20,
        ),
        "reconcile": prompt_builder.build_reconcile_request(
            world_a, {"name": "沈知微"}, [], {}, long_text, history, long_text,
        ),
    }

    # 每个模板里的占位符都必须被替换成实际内容，不能被预算挤成空串或标记。
    expected_keys = {
        "story": ["player_action", "planned_events", "panel_schema_json", "characters_json",
                  "world_context", "summary", "recent_history", "relationships_json",
                  "character_fields_json"],
        "world": ["user_description", "genre_hint"],
        "world_customize": ["world_items_json", "writer_style"],
        "character_customize": ["character_inputs_json", "panel_schema_json",
                                "existing_characters_json", "world_context"],
        "character": ["user_description", "panel_schema_json", "world_context",
                      "protagonist_json", "generation_request"],
        "memory": ["memory_candidates", "turn_narration", "player_action", "summary",
                   "timeline_tail", "summary_max_chars"],
        "backfill": ["new_fields_json", "existing_fields_json", "character_profile_json",
                     "current_context", "summary", "timeline_events", "recent_history",
                     "world_context"],
        "reconcile": ["panel_schema_json", "current_fields_json", "character_profile_json",
                      "current_context", "summary", "recent_history", "world_context"],
    }
    for name, request in requests.items():
        assert effective_max_tokens(request) <= llm_provider.MAX_OUTPUT_TOKENS, name
        assert _fits_window(request), (
            f"{name} 超出上下文窗口：prompt={prompt_chars(request)} "
            f"+ max_tokens={effective_max_tokens(request)} > {CONTEXT_WINDOW_TOKENS}"
        )
        messages = llm_provider.build_messages(request)
        assert messages[0]["content"].startswith("全局基础写作铁律"), (
            f"{name} 把最前面的全局铁律截掉了"
        )
        # 模板正文里本来就有解释用的 `{{...}}` 字样，所以只检查已知 key 是否都被替换。
        for key in expected_keys[name]:
            assert "{{" + key + "}}" not in request.task_prompt, f"{name} 的 {key} 未被替换"
        assert len(request.task_prompt) > 3000, f"{name} 的任务正文被压得太空"


def test_bounded_render_guarantees_every_placeholder_gets_content(monkeypatch, tmp_path):
    """默认 max_tokens=16384 时预算很紧；先到先得会让后面的字段被挤空，这里必须都留内容。"""
    template = tmp_path / "tpl.md"
    template.write_text(
        "开头铁律。\n" + "{{alpha}}\n{{beta}}\n{{gamma}}\n{{delta}}\n" + "尾声：必须输出 JSON。",
        encoding="utf-8",
    )
    monkeypatch.setitem(prompt_builder.PROMPT_FILES, "world_customize", template)
    values = {
        "alpha": "甲内容_" + "甲" * 9000,
        "beta": "乙内容_" + "乙" * 20,
        "gamma": "丙内容_" + "丙" * 20,
        "delta": "丁内容_" + "丁" * 20,
    }
    task = prompt_builder.bounded_render("world_customize", values, 16384)
    for key, value in values.items():
        assert "{{" + key + "}}" not in task
        assert value[:6] in task, f"{key} 的真实内容没有进入 Prompt"
    assert "尾声：必须输出 JSON。" in task


def test_bounded_render_restores_placeholders_cut_off_by_long_template(monkeypatch, tmp_path):
    """玩家把模板写到几万字、占位符全在末尾：裁完模板也要把占位符补回来。"""
    template = tmp_path / "tpl.md"
    template.write_text("开头铁律。\n" + "很长的模板说明。" * 3000
                        + "\n{{world_items_json}}\n{{writer_style}}\n尾声：必须输出 JSON。",
                        encoding="utf-8")
    monkeypatch.setitem(prompt_builder.PROMPT_FILES, "world_customize", template)
    task = prompt_builder.bounded_render("world_customize", {
        "world_items_json": '{"setting":"红砖教学楼"}',
        "writer_style": "克制慢节奏",
    }, 16384)
    assert "红砖教学楼" in task
    assert "克制慢节奏" in task
    assert "{{world_items_json}}" not in task
    assert "{{writer_style}}" not in task


def test_story_request_keeps_player_action_and_planned_events_under_pressure(world_a, monkeypatch, tmp_path):
    """角色 JSON 再大，玩家这一轮的输入和预定事件也不能被预算挤掉。"""
    long_text = _heavy_world(world_a, monkeypatch, tmp_path)
    characters = state_manager.characters_for_prompt(world_a, None)
    schema = panel_service.load_schema(world_a)
    marker = "玩家本轮指定的独特行动_不要被截掉"
    request = prompt_builder.build_story_request(
        world_a, characters, schema, [], "摘要", marker, "门口", {}, 1,
    )
    assert marker in request.task_prompt
    assert "把钥匙还回去" in request.task_prompt
    assert _fits_window(request)


def test_wire_guard_keeps_both_head_and_tail_when_task_is_huge():
    """超额时裁中段，全局铁律（头）与 JSON 格式红线（尾）都要留下。"""
    request = LLMRequest(
        system_prompt="【全局铁律】只输出 JSON，不得违背玩家设定。",
        task_prompt="开头格式说明。" + ("任务正文很长。" * 6000) + "【尾声格式红线】必须闭合 JSON。",
        context="通用字段类型说明",
        user_action="推门进去",
        max_tokens=8192,
    )
    messages = llm_provider.build_messages(request)
    system = messages[0]["content"]
    assert system.startswith("【全局铁律】")
    assert "【尾声格式红线】" in system
    assert "上下文预算不足" in system
    assert _fits_window(request)


def test_wire_guard_keeps_player_action_and_json_instruction_in_user_message():
    """窗口不够时要裁 context / 旧历史，不能把本轮输入和「必须输出 JSON」裁掉。"""
    request = LLMRequest(
        system_prompt="【全局铁律】只输出 JSON。",
        task_prompt="任务正文。" * 3000,
        context="通用字段类型说明。" * 4000,
        history=[{"role": "story", "content": f"第 {i} 轮的旧叙述。" * 200} for i in range(12)],
        user_action="推门进去并问一句_本轮独特输入",
        max_tokens=16384,
    )
    messages = llm_provider.build_messages(request)
    assert messages[0]["content"].startswith("【全局铁律】")
    assert "推门进去并问一句_本轮独特输入" in messages[1]["content"]
    assert "请严格按任务要求输出 JSON" in messages[1]["content"]
    assert _fits_window(request)


def test_max_tokens_above_window_is_clamped_everywhere():
    """绕过 settings 直接构造超大 max_tokens，也不能破坏窗口保证。"""
    request = LLMRequest(
        system_prompt="【全局铁律】", task_prompt="任务。" * 9000,
        max_tokens=999999,
    )
    assert effective_max_tokens(request) == llm_provider.MAX_OUTPUT_TOKENS
    assert _fits_window(request)
    provider = llm_provider.OpenAICompatProvider({"base_url": "http://127.0.0.1:8080/v1"})
    captured = {}
    monkeypatched = type("R", (), {
        "json": lambda self: {"choices": [{"message": {"content": "{}"}}]},
        "raise_for_status": lambda self: None,
    })()
    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json, headers):  # noqa: A002 - 与 httpx 签名一致
            captured.update(json)
            return monkeypatched
    import backend.core.llm_provider as mod
    original = mod.httpx.Client
    mod.httpx.Client = FakeClient
    try:
        provider.generate(request)
    finally:
        mod.httpx.Client = original
    assert captured["max_tokens"] == llm_provider.MAX_OUTPUT_TOKENS


def test_full_bible_does_not_blow_past_window_even_with_many_characters(world_a):
    long_text = "具体细节与来龙去脉。" * 1200
    heavy = {
        "items": {key: {"player_input": long_text, "text": long_text}
                  for key in bible_service.ITEM_KEYS},
        "relations": [],
    }
    for index in range(8):
        state_manager.save_generated_character(world_a, {
            "name": f"配角{index}", "core_description": long_text,
            "custom_fields": {}, "bible": heavy,
        })
    request = prompt_builder.build_story_request(
        world_a, state_manager.characters_for_prompt(world_a, None),
        panel_service.load_schema(world_a), [], "摘要", "推门进去", "门口", {}, 1,
    )
    assert _fits_window(request)
    assert prompt_chars(request) <= llm_provider.max_prompt_chars(request.max_tokens)

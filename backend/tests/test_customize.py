"""自定义 + AI 补全：锚句保真、键缺失降级、一次调用、预览不落盘。"""

from __future__ import annotations

import json

import pytest

from backend.core import customization, world_manager
from backend.core.llm_provider import GenerationResult, LLMRequest, MockProvider


def _fake_result(payload: dict) -> GenerationResult:
    return GenerationResult(
        text=json.dumps(payload, ensure_ascii=False),
        provider="fake", model="fake-model",
    )


@pytest.fixture
def fake_generate(monkeypatch):
    """替换模块内的 generate，精确控制「模型写了什么」。"""
    holder: dict = {"payload": {}, "error": None, "text": None}

    def _generate(request: LLMRequest) -> GenerationResult:
        if holder["error"]:
            return GenerationResult(error=holder["error"], provider="fake")
        if holder["text"] is not None:
            return GenerationResult(text=holder["text"], provider="fake")
        return _fake_result(holder["payload"])

    monkeypatch.setattr(customization, "generate", _generate)
    return holder


def _world_form() -> dict:
    return {
        "name": "雨季来信", "genre": "现代校园 · 甜向",
        "description": "一个关于迟到的告白的故事",
        "setting": "现代校园，红砖教学楼，常年下雨",   # 玩家事实锚句
        "rules": "所有角色都是成年人",
        "opening_location": "礼堂后台走廊", "opening_scene": "",
        "opening_choices": [{"id": "A", "text": "先开口问一句"}],
        "protagonist_hint": "", "npc_hints": [],
        "writer_style": "克制慢节奏",
    }


def _character_form() -> dict:
    return {
        "name": "沈知微", "core_description": "把钥匙攥了三年的人",
        "items": {
            "appearance": {"player_input": "红发，个子很高", "text": ""},
            "personality": {"player_input": "戒备心很重", "text": ""},
            "background": {"player_input": "", "text": ""},
            "major_events": {"player_input": "", "text": ""},
        },
        "relations": [{
            "target_character_id": "npc_001",
            "target_name": "陆砚",
            "relation": {"player_input": "大学同学，三年没联系", "text": ""},
            "past_events": [{"player_input": "毕业那天的争执", "text": ""}],
            "planned_events": [
                {"id": "", "text": "在旧宅重逢并把钥匙还回去"},
                {"id": "", "text": "一起查出当年那封信是谁写的"},
            ],
        }],
    }


def test_world_preview_keeps_player_anchor(fake_generate):
    """模型改写/漏掉玩家原话时，整段原话被作为事实锚句置顶，AI 扩写不丢。"""
    fake_generate["payload"] = {
        "name": "雨季来信", "genre": "现代校园 · 甜向",
        "description": "一个关于迟到的告白的故事",
        "setting": "故事发生在一栋白色实验楼里，四季干燥。",  # 与玩家原话矛盾 / 不含原话
        "rules": "所有角色都是成年人，以及后补的规矩。",
        "opening_location": "礼堂后台走廊", "opening_scene": "门半掩着。",
        "opening_choices": [{"id": "A", "text": "先开口问一句"}],
        "protagonist_hint": "", "npc_hints": [],
    }
    result = customization.generate_world_preview(_world_form())
    setting = result["preview"]["setting"]
    assert setting.startswith(customization.ANCHOR_PREFIX)
    assert "现代校园，红砖教学楼，常年下雨" in setting
    assert "白色实验楼" in setting  # AI 扩写保留，不覆盖
    assert any("事实锚句" in w for w in result["warnings"])
    # 写手风格不由模型改写。
    assert result["preview"]["writer_style"] == "克制慢节奏"


def test_missing_keys_are_backfilled_and_marked_degraded(fake_generate):
    """长 JSON 被从中间截断时，后面的键会整个消失；必须补齐并标降级。"""
    fake_generate["payload"] = {"name": "雨季来信", "setting": "只有这一段"}
    result = customization.generate_world_preview(_world_form())
    assert result["degraded"] is True
    assert any("缺失" in w for w in result["warnings"])
    assert result["preview"]["rules"]  # 不能是空
    assert result["preview"]["opening_choices"]  # 四项仍在


def test_unparseable_output_is_degraded_not_crash(fake_generate):
    fake_generate["text"] = "这不是 JSON，只是一段小说正文……"
    result = customization.generate_world_preview(_world_form())
    assert result["degraded"] is True
    assert result["preview"]["setting"]
    assert any("无法解析" in w for w in result["warnings"])


def test_model_error_falls_back_to_player_text(fake_generate):
    fake_generate["error"] = "模型服务未启动"
    result = customization.generate_world_preview(_world_form())
    assert result["degraded"] is True
    assert "现代校园，红砖教学楼，常年下雨" in result["preview"]["setting"]
    assert any("模型补全失败" in w for w in result["warnings"])


def test_character_preview_preserves_inputs_and_planned_events(world_a, fake_generate):
    """玩家定的目标角色与预定事件清单，AI 无权改写或新增。"""
    fake_generate["payload"] = {
        "name": "被模型改掉的名字", "core_description": "被模型改掉的定位",
        "items": {
            "appearance": {"text": "一头黑发，身量娇小。"},   # 与玩家原话冲突
            "personality": {"text": "外向热情，见谁都熟。"},
            "background": {"text": "在一座海边小城长大，家里开书店。"},
            "major_events": {"text": "十八岁那年离开家。"},
        },
        "relations": [{
            "target_character_id": "npc_999",       # 试图换人
            "target_name": "别人",
            "relation": {"text": "其实是失散多年的兄妹。"},
            "past_events": [{"text": "一次无关紧要的偶遇。"}],
            "planned_events": [{"text": "模型自己加的事件"}],  # 试图加任务
        }],
    }
    result = customization.generate_character_preview(world_a, _character_form())
    assert result["name"] == "沈知微"          # 玩家姓名优先（不能落成「未命名角色」）
    assert result["core_description"] == "把钥匙攥了三年的人"
    rel = result["relations"][0]
    assert rel["target_character_id"] == "npc_001"
    assert len(rel["planned_events"]) == 2
    assert "模型自己加的事件" not in json.dumps(rel, ensure_ascii=False)
    assert "在旧宅重逢并把钥匙还回去" in rel["planned_events"][0]["text"]
    # 玩家原话整段不在 AI 输出里 -> 置顶锚句，而不是碎片级误报或静默丢失。
    assert result["items"]["appearance"]["text"].startswith(customization.ANCHOR_PREFIX)
    assert "红发，个子很高" in result["items"]["appearance"]["text"]
    assert "一头黑发" in result["items"]["appearance"]["text"]


def test_character_anchor_not_flagged_when_ai_keeps_it(world_a, fake_generate):
    """AI 就地扩写、原话仍在时不应追加锚句（避免每个条目都被误报）。"""
    fake_generate["payload"] = {
        "name": "沈知微", "core_description": "把钥匙攥了三年的人",
        "items": {
            "appearance": {"text": "红发，个子很高。常穿洗得发白的风衣，走路时肩膀微微前倾。"},
            "personality": {"text": "戒备心很重，但熟络之后话会变多。"},
            "background": {"text": "在旧宅长大。"},
            "major_events": {"text": "三年前那场事故。"},
        },
        "relations": [],
    }
    result = customization.generate_character_preview(world_a, _character_form())
    assert not result["items"]["appearance"]["text"].startswith(customization.ANCHOR_PREFIX)
    assert not result["items"]["personality"]["text"].startswith(customization.ANCHOR_PREFIX)


def test_preview_writes_nothing_to_disk(world_a, fake_generate):
    fake_generate["payload"] = {}
    npc_root = world_manager.resolve(world_a, "characters", "npcs")
    before = sorted(str(p) for p in world_manager.world_dir(world_a).rglob("*"))
    customization.generate_world_preview(_world_form())
    customization.generate_character_preview(world_a, _character_form())
    after = sorted(str(p) for p in world_manager.world_dir(world_a).rglob("*"))
    assert before == after
    assert not any((p / "profile.yaml").is_file() for p in npc_root.iterdir())


def test_mock_provider_dispatches_new_purposes():
    """不注册 handler 会静默落到 _story，返回剧情 JSON 让补全解析失败。"""
    provider = MockProvider()
    world = json.loads(provider.generate(LLMRequest(
        system_prompt="s", task_prompt="t", purpose="customize_world",
        mock_context={"items": {"setting": "红砖楼"}},
    )).text)
    assert "setting" in world and "narration" not in world
    assert "红砖楼" in world["setting"]

    character = json.loads(provider.generate(LLMRequest(
        system_prompt="s", task_prompt="t", purpose="customize_character",
        mock_context={"inputs": {"items": {"appearance": {"player_input": "红发"}}, "relations": []}},
    )).text)
    assert "items" in character and "narration" not in character
    assert "红发" in character["items"]["appearance"]["text"]

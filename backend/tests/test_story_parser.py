"""模型输出解析与容错。"""

from __future__ import annotations

import json

from backend.core import memory_manager, panel_service, state_manager, story_engine, validator
from backend.core.story_parser import parse_json_object, parse_turn_output, safe_fallback
from backend.models.story import Choice, TurnOutput, TurnRequest

VALID = json.dumps({
    "narration": "雨还在下。",
    "dialogues": [{"speaker_id": "npc_001", "text": "你来了。"}],
    "choices": [{"id": "A", "text": "一"}, {"id": "B", "text": "二"},
                {"id": "C", "text": "三"}, {"id": "D", "text": "四"}],
    "character_updates": [],
    "memory_candidates": [],
}, ensure_ascii=False)


def test_direct_json():
    data, reason = parse_json_object(VALID)
    assert reason == ""
    assert data["narration"] == "雨还在下。"


def test_markdown_code_block():
    text = "好的，这是结果：\n```json\n" + VALID + "\n```\n希望能帮到你。"
    data, reason = parse_json_object(text)
    assert reason == ""
    assert data["choices"][0]["id"] == "A"


def test_chatty_wrapper_with_braces():
    text = "抱歉，我补充说明一下：" + VALID + "\n以上。"
    data, reason = parse_json_object(text)
    assert reason == ""
    assert data["narration"] == "雨还在下。"


def test_broken_output_falls_back_to_four_safe_choices():
    output, reason = parse_turn_output("完全不是 JSON 的一段话")
    assert output is None
    fallback = safe_fallback("完全不是 JSON 的一段话", reason, ["观察", "询问", "离开", "沉默"])
    assert len(fallback.choices) == 4
    assert [c.id for c in fallback.choices] == ["A", "B", "C", "D"]
    assert fallback.character_updates == []


def test_validator_completes_missing_choices():
    output = TurnOutput(narration="正文", choices=[Choice(id="A", text="只有一个")])
    result = validator.validate_turn_output(
        output, panel_service.PanelSchema(), {}, [], ["后备一", "后备二", "后备三", "后备四"],
    )
    assert len(result.output.choices) == 4
    assert [c.id for c in result.output.choices] == ["A", "B", "C", "D"]


def test_invalid_json_never_loses_player_action(monkeypatch, seeded_world):
    """模型输出彻底坏掉时，玩家这一句行动仍必须写进历史。"""
    from backend.core.llm_provider import GenerationResult

    def broken_generate(request, config=None):
        return GenerationResult(text="这不是 JSON，格式完全坏了", provider="mock")

    monkeypatch.setattr(story_engine, "generate", broken_generate)
    response = story_engine.turn(seeded_world, TurnRequest(action="我放下伞，问她信是谁写的"))
    history = memory_manager.load_history(seeded_world)
    assert history[-1]["player_action"] == "我放下伞，问她信是谁写的"
    assert len(response.output.choices) == 4
    assert response.output.parse_warning


def test_static_field_is_never_overwritten(seeded_world):
    """默认 Schema 里 NPC 的 appearance 是 static，剧情回合不得改动。"""
    before = state_manager.get_character(seeded_world, "npc_001").state.custom_fields.get("appearance")
    response = story_engine.turn(seeded_world, TurnRequest(action="我把那封信推到桌子中间"))
    after = state_manager.get_character(seeded_world, "npc_001").state.custom_fields.get("appearance")
    assert before == after
    assert response.turn == 1

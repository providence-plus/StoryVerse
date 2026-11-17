"""字段建议：告诉但不烦、绝不自动改 schema。"""

from __future__ import annotations

import json

from backend.core import (
    character_generator,
    field_suggestions,
    panel_service,
    state_manager,
    story_engine,
)
from backend.core.llm_provider import GenerationResult
from backend.models.story import SchemaSuggestion, TurnRequest


def test_unknown_field_is_recorded_but_never_written(monkeypatch, seeded_world):
    payload = {
        "narration": "雨停了一瞬。",
        "dialogues": [],
        "choices": [{"id": letter, "text": f"选项{letter}"} for letter in "ABCD"],
        "character_updates": [
            {"character_id": "npc_001", "custom_fields": {"mood_towards_player": "有点好奇"}}
        ],
        "memory_candidates": [],
        "schema_suggestions": [],
    }

    def fake_generate(request, config=None):
        return GenerationResult(text=json.dumps(payload, ensure_ascii=False), provider="mock")

    monkeypatch.setattr(story_engine, "generate", fake_generate)
    story_engine.turn(seeded_world, TurnRequest(action="我把伞靠在门边"))

    schema = panel_service.load_schema(seeded_world)
    assert "mood_towards_player" not in schema.all_field_ids("npc_001", False)
    state = state_manager.get_character(seeded_world, "npc_001").state
    assert "mood_towards_player" not in state.custom_fields
    assert any(i.field_id == "mood_towards_player"
               for i in field_suggestions.pending_items(seeded_world))


def test_suggestions_are_batched_and_ignored_ones_never_return(seeded_world):
    cfg = {"suggestion_notify_threshold": 3, "suggestion_notify_every_turns": 10}
    schema = panel_service.load_schema(seeded_world)
    kinds = state_manager.character_kinds(seeded_world)

    for i in range(3):
        field_suggestions.record(
            seeded_world,
            [SchemaSuggestion(field_id=f"extra_{i}", label=f"额外字段{i}", type="text")],
            [], turn=5, schema=schema, character_kinds=kinds,
        )
    assert field_suggestions.should_notify(seeded_world, current_turn=10, config=cfg) is True
    field_suggestions.mark_notified(seeded_world, 10)
    # 同一批建议不会下一轮又弹一次。
    assert field_suggestions.should_notify(seeded_world, current_turn=11, config=cfg) is False

    field_suggestions.set_status(seeded_world, "extra_0", "ignored")
    assert len(field_suggestions.pending_items(seeded_world)) == 2
    assert field_suggestions.should_notify(seeded_world, current_turn=30, config=cfg) is False


def test_adopting_a_suggestion_creates_field_and_backfills(seeded_world):
    schema = panel_service.load_schema(seeded_world)
    kinds = state_manager.character_kinds(seeded_world)
    field_suggestions.record(
        seeded_world,
        [SchemaSuggestion(field_id="real_opinion", label="对主角的真实评价", type="text",
                          reason="这段关系反复出现口是心非")],
        [], turn=4, schema=schema, character_kinds=kinds,
    )
    result = character_generator.adopt_suggestion(seeded_world, "real_opinion")
    assert result["field"]["id"] == "real_opinion"
    assert "real_opinion" in panel_service.load_schema(seeded_world).all_field_ids("npc_001", False)
    assert "real_opinion" in state_manager.get_character(seeded_world, "npc_001").state.custom_fields
    assert not any(i.field_id == "real_opinion" and i.status == "pending"
                   for i in field_suggestions.load_items(seeded_world))

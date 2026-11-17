"""字段定期校准与任意轮次回填。"""

from __future__ import annotations

import json

from backend.core import (
    character_generator,
    panel_service,
    settings_service,
    state_manager,
    story_engine,
)
from backend.core.llm_provider import GenerationResult
from backend.models.story import TurnRequest


def test_reconcile_fires_on_configured_interval(monkeypatch, seeded_world):
    calls: list[str] = []
    monkeypatch.setattr(character_generator, "reconcile_all", lambda wid: calls.append(wid))

    original = settings_service.load_settings

    def tuned():
        cfg = original()
        cfg.setdefault("field_system", {})["field_reconcile_every"] = 2
        return cfg

    monkeypatch.setattr(settings_service, "load_settings", tuned)
    story_engine.turn(seeded_world, TurnRequest(action="第一轮"))
    assert calls == []
    story_engine.turn(seeded_world, TurnRequest(action="第二轮"))
    assert calls == [seeded_world]


def test_reconcile_can_be_disabled(monkeypatch, seeded_world):
    calls: list[str] = []
    monkeypatch.setattr(character_generator, "reconcile_all", lambda wid: calls.append(wid))
    original = settings_service.load_settings

    def tuned():
        cfg = original()
        cfg.setdefault("field_system", {})["field_reconcile_every"] = 0
        return cfg

    monkeypatch.setattr(settings_service, "load_settings", tuned)
    for i in range(5):
        story_engine.turn(seeded_world, TurnRequest(action=f"第 {i} 轮"))
    assert calls == []


def test_reconcile_only_changes_the_fields_it_lists(monkeypatch, seeded_world):
    before = dict(state_manager.get_character(seeded_world, "npc_001").state.custom_fields)

    def fake_generate(request, config=None):
        return GenerationResult(
            text=json.dumps({"corrections": [
                {"field_id": "outfit", "value": "黑色礼服", "reason": "上一轮明确写了换装"}
            ]}, ensure_ascii=False),
            provider="mock",
        )

    monkeypatch.setattr(character_generator, "generate", fake_generate)
    result = character_generator.reconcile_fields(seeded_world, "npc_001")
    after = state_manager.get_character(seeded_world, "npc_001").state.custom_fields
    assert result["corrections"] == 1
    assert after["outfit"] == "黑色礼服"
    for key, value in before.items():
        if key != "outfit":
            assert after[key] == value, f"无关字段 {key} 被校准改动了"


def test_reconcile_cannot_invent_new_fields(monkeypatch, seeded_world):
    def fake_generate(request, config=None):
        return GenerationResult(
            text=json.dumps({"corrections": [
                {"field_id": "invented_field", "value": "凭空出现"}
            ]}, ensure_ascii=False),
            provider="mock",
        )

    monkeypatch.setattr(character_generator, "generate", fake_generate)
    result = character_generator.reconcile_fields(seeded_world, "npc_001")
    assert result["corrections"] == 0
    assert "invented_field" not in state_manager.get_character(seeded_world, "npc_001").state.custom_fields


def test_backfill_is_a_noop_when_no_new_field_exists(seeded_world):
    result = character_generator.backfill_fields(seeded_world, "npc_001")
    assert result["applied"] == {}
    assert panel_service.load_schema(seeded_world).all_field_ids("npc_001", False)

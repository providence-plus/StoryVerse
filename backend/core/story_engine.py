"""Story Engine：一轮互动的完整编排。

顺序：识别世界与存档 -> 先保住玩家行动 -> 装上下文 -> 模型生成 -> 三层解析容错
-> Validator 净化 -> 写人物状态 -> 写记忆与历史 -> 按需校准字段 -> 自动存档 -> 返回。
任何一步失败都不会丢玩家已经做出的行动，也不会写坏正式文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core import (
    character_generator,
    field_suggestions,
    file_utils,
    memory_manager,
    panel_service,
    prompt_builder,
    save_manager,
    settings_service,
    state_manager,
    world_manager,
)
from backend.core.llm_provider import LLMRequest, generate
from backend.core.story_parser import parse_turn_output, safe_fallback
from backend.core.validator import validate_turn_output
from backend.models.story import Choice, TurnOutput, TurnRecord, TurnRequest, TurnResponse


def _pending_path(world_id: str) -> Path:
    return world_manager.resolve(world_id, "state", "pending_action.json")


def _resolve_player_action(world_id: str, request: TurnRequest) -> str:
    """点选项时把 A/B/C/D 还原成上一轮真实的选项文字；自定义输入原样保留。"""
    action = (request.action or "").strip()
    if request.choice_id:
        history = memory_manager.load_history(world_id, limit=1)
        if history:
            for choice in history[-1].get("choices") or []:
                if isinstance(choice, dict) and choice.get("id") == request.choice_id:
                    return str(choice.get("text") or action)
    if not action:
        raise ValueError("玩家行动不能为空")
    return action


def _repair(raw_text: str, system_prompt: str) -> str | None:
    """格式修复只请求一次，仍失败就走安全兜底，不反复消耗模型。"""
    cfg = settings_service.llm_settings()
    repair_prompt = (
        "上一次模型输出不是合法 JSON。请把下面的内容整理成合法 JSON 对象，"
        "包含 narration、dialogues、choices（四项 A/B/C/D）、character_updates、"
        "memory_candidates、schema_suggestions 这些键。只输出 JSON，不要解释。\n\n"
        "原始输出：\n" + raw_text[:6000]
    )
    request = LLMRequest(
        system_prompt=system_prompt,
        task_prompt=repair_prompt,
        user_action="修复格式",
        purpose="repair",
        temperature=0.0,
        max_tokens=min(int(cfg.get("max_tokens", 8192)), 2048),
        timeout_seconds=float(cfg.get("timeout_seconds", 300)),
    )
    result = generate(request)
    return None if result.error else result.text


def _error_output(message: str, fallbacks: list[str]) -> TurnOutput:
    """模型没接通 / 请求失败时的可读降级：明确告诉玩家「没有生成成功」。"""
    text = (
        "这一轮没有生成剧情。\n\n"
        f"原因：{message}\n\n"
        "你的行动已经保存，可以稍后重试；也可以到设置里开启 Mock Mode 先体验完整流程。"
    )
    return TurnOutput(
        narration=text,
        choices=[Choice(id=letter, text=fallbacks[i % len(fallbacks)] if fallbacks else "重试上一句行动")
                 for i, letter in enumerate("ABCD")],
        parse_warning=message,
    )


def turn(world_id: str, request: TurnRequest) -> TurnResponse:
    """执行一个完整回合。"""
    world_manager.validate_world_id(world_id)
    cfg = settings_service.load_settings()
    story_cfg = cfg.get("story") or {}
    field_cfg = cfg.get("field_system") or {}
    fallbacks: list[str] = list(story_cfg.get("fallback_choices") or ["继续观察，暂不行动"])
    history_turns = int(story_cfg.get("history_turns", 9))

    state = state_manager.get_current_state(world_id)
    if request.save_id and request.save_id != state.save_id:
        save_manager.load_save(world_id, request.save_id)
        state = state_manager.get_current_state(world_id)

    player_action = _resolve_player_action(world_id, request)
    turn_number = state.turn + 1

    # 结构化场景表单：玩家填的地点更新当前地点（开局 / 换场景都走这里）。
    if request.location.strip():
        state.current_location = request.location.strip()

    # 本轮注入的场景描述：玩家填的优先；第一轮且没填时退回世界创建时的开场画面。
    scene_text = request.scene_description.strip()
    if not scene_text and turn_number == 1:
        try:
            scene_text = (world_manager.get_meta(world_id).opening_scene or "").strip()
        except (OSError, ValueError, TypeError, FileNotFoundError):
            scene_text = ""

    # 首次登场角色：开局后新增、还没进过剧情的。
    introducing = state_manager.pending_introductions(world_id)

    # 红线：调模型之前先保住玩家行动。即使进程被中断，这句行动也还在磁盘上。
    file_utils.atomic_write_json(_pending_path(world_id), {
        "turn": turn_number, "player_action": player_action, "world_id": world_id,
    })

    kinds = state_manager.character_kinds(world_id)
    requested = [cid for cid in (request.active_character_ids or []) if cid in kinds]
    active_ids = requested or list(kinds)
    # 退场角色不进本轮在场名单；历史与文件保留，只是不再出现在新剧情里。
    active_ids = [cid for cid in active_ids if not state_manager.is_retired(world_id, cid)]
    schema = panel_service.load_schema(world_id)
    characters = state_manager.characters_for_prompt(world_id, active_ids)
    recent_history = memory_manager.load_history(world_id, limit=history_turns)
    summary = memory_manager.load_summary(world_id)
    relationships = state_manager.load_relationships(world_id)

    llm_request = prompt_builder.build_story_request(
        world_id=world_id,
        characters=characters,
        schema=schema,
        recent_history=recent_history,
        summary=summary,
        player_action=player_action,
        current_location=state.current_location,
        relationships=relationships,
        turn=turn_number,
        scene_description=scene_text,
        introducing=introducing,
        end_scene=request.end_scene,
    )

    result = generate(llm_request)
    if result.error:
        output = _error_output(result.error, fallbacks)
    else:
        parsed, reason = parse_turn_output(result.text)
        if parsed is None and int(story_cfg.get("repair_attempts", 1)) > 0:
            fixed = _repair(result.text, llm_request.system_prompt)
            if fixed:
                parsed, reason = parse_turn_output(fixed)
        if parsed is None:
            output = safe_fallback(result.text, reason, fallbacks)
        else:
            output = parsed
            output.raw_text = result.text

    # G2 关卡：所有 LLM 内容在这里被净化后才能落盘。
    validated = validate_turn_output(
        output=output,
        schema=schema,
        character_kinds=kinds,
        active_character_ids=active_ids,
        fallback_choices=fallbacks,
        max_memory_candidates=int((cfg.get("memory") or {}).get("max_memory_candidates_per_turn", 3)),
    )
    clean = validated.output
    if validated.rejected:
        clean.parse_warning = (clean.parse_warning or "") + "｜已拒收：" + "；".join(validated.rejected[:4])
    # 收尾这一轮不提供选项：前端收到空 choices 后转入下一幕表单。
    if request.end_scene:
        clean.choices = []

    # 越界字段只记录成建议，攒批提示，不打断这一轮阅读。
    field_suggestions.record(
        world_id=world_id,
        explicit=clean.schema_suggestions,
        unknown_keys=validated.unknown_keys,
        turn=turn_number,
        schema=schema,
        character_kinds=kinds,
    )

    state_manager.apply_updates(world_id, clean.character_updates)

    # 历史里只写玩家真实交出的行动，模型正文里多写的动作不会进入玩家历史。
    memory_manager.append_turn(world_id, TurnRecord(
        turn=turn_number,
        player_action=player_action,
        narration=clean.narration,
        dialogues=[d.model_dump() for d in clean.dialogues],
        choices=[c.model_dump() for c in clean.choices],
    ).model_dump())
    memory_manager.process_turn(
        world_id=world_id,
        narration=clean.narration,
        player_action=player_action,
        memory_candidates=clean.memory_candidates,
        turn=turn_number,
    )

    # 字段校准：事件触发时 turns_since_reconcile 已被写成阈值，这里统一处理。
    every = int(field_cfg.get("field_reconcile_every", 8))
    state.turns_since_reconcile += 1
    if every > 0 and state.turns_since_reconcile >= every:
        character_generator.reconcile_all(world_id)
        state.turns_since_reconcile = 0

    state.turn = turn_number
    state.active_character_ids = active_ids
    state_manager.write_current_state(world_id, state)

    # 首次登场角色只在本轮注入一次；模型成功响应后清除标记，下轮不再重复登场。
    if not result.error and introducing:
        state_manager.clear_pending_introductions(world_id, introducing)

    if field_suggestions.should_notify(world_id, turn_number, field_cfg):
        field_suggestions.mark_notified(world_id, turn_number)

    save_manager.auto_save(world_id)
    pending = _pending_path(world_id)
    if pending.is_file():
        pending.unlink()

    character_views = [c.model_dump() for c in state_manager.list_characters(world_id)]
    return TurnResponse(
        turn=turn_number,
        world_id=world_id,
        save_id=state.save_id,
        player_action=player_action,
        output=clean,
        characters=character_views,
        panels=panel_service.panels_for_frontend(world_id),
        current_location=state.current_location,
        suggestions_pending=len(field_suggestions.pending_items(world_id)),
        scene_ended=request.end_scene,
    )


def pending_action(world_id: str) -> dict[str, Any]:
    """返回上次未完成的玩家行动，供前端提示重试。"""
    return file_utils.read_json(_pending_path(world_id), {}) or {}

"""记忆管理：最近历史 + 结构化事件 + 剧情摘要，不引入向量数据库。

- memory/history.jsonl：每轮完整对话（含玩家实际输入，这是不可篡改的事实源）
- memory/timeline.jsonl：只追加的重要事件
- memory/summary.md：长期剧情摘要
"""

from __future__ import annotations

from typing import Any

from backend.core import file_utils, prompt_builder, settings_service, world_manager
from backend.core.llm_provider import LLMRequest, generate
from backend.core.story_parser import parse_json_object


def _history_path(world_id: str):
    return world_manager.resolve(world_id, "memory", "history.jsonl")


def _timeline_path(world_id: str):
    return world_manager.resolve(world_id, "memory", "timeline.jsonl")


def _summary_path(world_id: str):
    return world_manager.resolve(world_id, "memory", "summary.md")


def append_turn(world_id: str, record: dict[str, Any]) -> None:
    file_utils.append_jsonl(_history_path(world_id), record)


def load_history(world_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    return file_utils.read_jsonl(_history_path(world_id), limit)


def load_timeline(world_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    return file_utils.read_jsonl(_timeline_path(world_id), limit)


def append_events(world_id: str, events: list[dict[str, Any]], turn: int) -> None:
    for event in events:
        text = str(event.get("text", "")).strip()
        if not text:
            continue
        file_utils.append_jsonl(_timeline_path(world_id), {
            "turn": turn,
            "text": text,
            "importance": str(event.get("importance", "normal")),
        })


def load_summary(world_id: str) -> str:
    return file_utils.read_text(_summary_path(world_id), default="故事尚未开始。")


def write_summary(world_id: str, text: str) -> None:
    cfg = settings_service.load_settings()
    limit = int((cfg.get("memory") or {}).get("summary_max_chars", 4000))
    # 保守截断：保留末尾（较新的内容），因为摘要按时间顺序追加。
    safe = text.strip()[-limit:] if len(text) > limit else text.strip()
    file_utils.atomic_write_text(_summary_path(world_id), safe + "\n")


def process_turn(
    world_id: str,
    narration: str,
    player_action: str,
    memory_candidates: list[str],
    turn: int,
) -> dict[str, Any]:
    """让模型整理记忆；失败时走保守回退，绝不因为记忆失败影响主流程。"""
    summary = load_summary(world_id)
    timeline_tail = load_timeline(world_id, limit=10)
    request: LLMRequest = prompt_builder.build_memory_request(
        world_id=world_id,
        summary=summary,
        timeline_tail=timeline_tail,
        narration=narration,
        player_action=player_action,
        memory_candidates=memory_candidates,
    )
    result = generate(request)
    events: list[dict[str, Any]] = []
    new_summary = summary

    if result.error:
        events = [{"text": c, "importance": "normal"} for c in memory_candidates]
        new_summary = (summary + f"\n第 {turn} 轮：{player_action}").strip()
    else:
        data, reason = parse_json_object(result.text)
        if data is None:
            events = [{"text": c, "importance": "normal"} for c in memory_candidates]
            new_summary = (summary + f"\n第 {turn} 轮：{player_action}（{reason}）").strip()
        else:
            raw_events = data.get("timeline_events") or []
            events = [e for e in raw_events if isinstance(e, dict) and e.get("text")]
            new_summary = str(data.get("summary") or summary)

    append_events(world_id, events, turn)
    write_summary(world_id, new_summary)
    return {"events": len(events), "summary": new_summary}

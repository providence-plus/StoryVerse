"""剧情回合接口。"""

from __future__ import annotations

from fastapi import APIRouter

from backend.core import memory_manager, panel_service, settings_service, story_engine, world_manager
from backend.models.story import TurnRequest

router = APIRouter(prefix="/api/worlds/{world_id}", tags=["story"])


@router.post("/story/turn")
def story_turn(world_id: str, payload: TurnRequest):
    return story_engine.turn(world_id, payload).model_dump()


@router.get("/history")
def get_history(world_id: str, limit: int = 50):
    world_manager.validate_world_id(world_id)
    return {
        "history": memory_manager.load_history(world_id, limit=max(1, min(500, limit))),
        "timeline": memory_manager.load_timeline(world_id, limit=max(1, min(500, limit))),
        "summary": memory_manager.load_summary(world_id),
    }


@router.get("/story/pending")
def get_pending(world_id: str):
    """上次未完成的玩家行动，用于模型失败后重试。"""
    return {"pending": story_engine.pending_action(world_id)}


@router.get("/story/config")
def get_story_config(world_id: str):
    """前端展示当前校准周期与建议阈值，让「多久更新一次」可见、可解释。"""
    world_manager.validate_world_id(world_id)
    cfg = settings_service.load_settings()
    return {
        "field_system": cfg.get("field_system") or {},
        "history_turns": (cfg.get("story") or {}).get("history_turns", 9),
        "panels": panel_service.panels_for_frontend(world_id),
    }

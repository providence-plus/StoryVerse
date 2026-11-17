"""存档分支隔离、重启恢复、切换世界后继续。"""

from __future__ import annotations

from backend.core import memory_manager, save_manager, state_manager, story_engine
from backend.models.story import TurnRequest


def _actions(world_id: str) -> list[str]:
    return [h["player_action"] for h in memory_manager.load_history(world_id)]


def test_two_saves_can_follow_different_branches(seeded_world):
    story_engine.turn(seeded_world, TurnRequest(action="共同的第一句行动"))
    branch_a = save_manager.create_save(seeded_world, "分支一")
    story_engine.turn(seeded_world, TurnRequest(action="只在分支一出现的行动"))

    # 回到起点，另开一条完全不同的分支。
    save_manager.load_save(seeded_world, "auto")
    branch_b = save_manager.create_save(seeded_world, "分支二")
    story_engine.turn(seeded_world, TurnRequest(action="只在分支二出现的行动"))

    save_manager.load_save(seeded_world, branch_a["save_id"])
    assert "只在分支一出现的行动" in _actions(seeded_world)
    assert "只在分支二出现的行动" not in _actions(seeded_world)

    save_manager.load_save(seeded_world, branch_b["save_id"])
    assert "只在分支二出现的行动" in _actions(seeded_world)
    assert "只在分支一出现的行动" not in _actions(seeded_world)


def test_state_survives_a_simulated_restart(seeded_world):
    """服务本身不持有运行时内存状态，重启 = 重新从磁盘读。"""
    story_engine.turn(seeded_world, TurnRequest(action="重启前写下的行动"))
    state = state_manager.get_current_state(seeded_world)
    assert state.turn == 1
    assert state.save_id == "auto"

    save_manager.load_save(seeded_world, "auto")
    assert "重启前写下的行动" in _actions(seeded_world)
    assert state_manager.get_current_state(seeded_world).turn == 1


def test_switch_world_and_come_back_keeps_progress(world_a, world_b):
    from backend.tests.conftest import seed_characters

    seed_characters(world_a)
    seed_characters(world_b)
    story_engine.turn(world_a, TurnRequest(action="A 世界的行动"))
    story_engine.turn(world_b, TurnRequest(action="B 世界的行动"))

    assert _actions(world_a) == ["A 世界的行动"]
    assert _actions(world_b) == ["B 世界的行动"]

    story_engine.turn(world_a, TurnRequest(action="回到 A 之后继续"))
    assert _actions(world_a) == ["A 世界的行动", "回到 A 之后继续"]
    assert _actions(world_b) == ["B 世界的行动"]


def test_auto_save_does_not_overwrite_other_branch(seeded_world):
    story_engine.turn(seeded_world, TurnRequest(action="自动存档里的行动"))
    branch = save_manager.create_save(seeded_world, "手动分支")
    save_manager.load_save(seeded_world, "auto")
    story_engine.turn(seeded_world, TurnRequest(action="自动分支后续行动"))
    save_manager.load_save(seeded_world, branch["save_id"])
    assert "自动分支后续行动" not in _actions(seeded_world)

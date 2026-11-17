"""本轮修复的回归测试。

覆盖三件事：
1. 「进入下个事件」的收尾轮必须给前端一个明确的 scene_ended 标志，且收尾轮
   绝不能把预定事件清单当成要推进的场景（这正是玩家看到「弹出一段预定事件文字」的原因）。
2. 玩家 = 主角，四个选项与自由输入的发起方都定死为主角，并在每轮注入主角锚点。
3. 普通剧情轮仍然照常推进预定事件，不能被这次修复顺手关掉。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.core import bible_service, panel_service, prompt_builder, state_manager
from backend.main import app


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _story_request(world_id: str, *, end_scene: bool = False):
    chars = state_manager.characters_for_prompt(world_id)
    schema = panel_service.load_schema(world_id)
    return prompt_builder.build_story_request(
        world_id=world_id,
        characters=chars,
        schema=schema,
        recent_history=[],
        summary="",
        player_action="我推门进去",
        current_location="旧码头",
        relationships={},
        turn=2,
        end_scene=end_scene,
    )


def _seed_planned_event(world_id: str, text: str = "两人在雨夜的码头摊牌") -> str:
    """往主角的关系卡里放一条预定事件，返回它的 id。"""
    npc = next(c for c in state_manager.list_character_ids(world_id) if c != "protagonist")
    bible = bible_service.normalize_bible({
        "relations": [{
            "target_character_id": npc,
            "target_name": "测试配角",
            "relation": {"player_input": "旧识", "text": "旧识"},
            "past_events": [],
            "planned_events": [{"id": "", "text": text}],
        }]
    }, "protagonist")
    state_manager.edit_character(world_id, "protagonist",
                                 {"name": "测试主角", "core_description": "", "bible": bible})
    events = bible_service.collect_planned_events(world_id)
    assert events, "预定事件应当已落盘"
    return events[0]["id"]


# ---------- 1. 收尾轮的明确标志 ----------

def test_turn_response_exposes_scene_ended_flag(seeded_world):
    client = _client()
    client.post(f"/api/worlds/{seeded_world}/start")

    normal = client.post(f"/api/worlds/{seeded_world}/story/turn",
                         json={"action": "我推门进去"}).json()
    assert normal["scene_ended"] is False

    ended = client.post(f"/api/worlds/{seeded_world}/story/turn",
                        json={"action": "决定结束眼前这一幕", "end_scene": True}).json()
    assert ended["scene_ended"] is True
    assert ended["output"]["choices"] == []


def test_end_scene_uses_scene_end_template_not_story_template(seeded_world):
    req = _story_request(seeded_world, end_scene=True)
    assert "任务：收束当前场景" in req.task_prompt
    assert "{{" not in req.task_prompt, "占位符必须全部替换"


# ---------- 2. 收尾轮绝不推进预定事件 ----------

def test_end_scene_does_not_inject_planned_events(seeded_world):
    text = "两人在雨夜的码头摊牌，把三年前的误会讲开"
    _seed_planned_event(seeded_world, text)

    req = _story_request(seeded_world, end_scene=True)
    assert text not in req.task_prompt, "收尾轮不能把预定事件原文塞给模型"
    assert "本轮是场景收束，不推进任何预定事件" in req.task_prompt
    assert "本轮只收束，绝不推进任何新的事件" in req.task_prompt


def test_normal_turn_still_injects_planned_events(seeded_world):
    text = "两人在雨夜的码头摊牌，把三年前的误会讲开"
    _seed_planned_event(seeded_world, text)

    req = _story_request(seeded_world, end_scene=False)
    assert "任务：生成本轮小说剧情" in req.task_prompt
    assert text in req.task_prompt, "普通剧情轮仍要推进玩家排好的预定事件"
    assert "本轮先围绕其中" in req.task_prompt


# ---------- 3. 玩家 = 主角，每轮都有明确锚点 ----------

def test_protagonist_anchor_injected_into_both_templates(seeded_world):
    for end_scene in (False, True):
        req = _story_request(seeded_world, end_scene=end_scene)
        assert "测试主角" in req.task_prompt, f"end_scene={end_scene} 没注入主角名字"
        assert "protagonist" in req.task_prompt
        assert "四个选项 A/B/C/D" in req.task_prompt
        assert "全部是这位主角要做的动作" in req.task_prompt


def test_no_protagonist_in_scene_still_declares_subject(seeded_world):
    """只让 NPC 在场时，也要有明确的主语说明，不能让占位符空着。"""
    req = prompt_builder.build_story_request(
        world_id=seeded_world,
        characters=state_manager.characters_for_prompt(
            seeded_world,
            [next(c for c in state_manager.list_character_ids(seeded_world) if c != "protagonist")],
        ),
        schema=panel_service.load_schema(seeded_world),
        recent_history=[], summary="", player_action="我推门进去",
        current_location="", relationships={}, turn=2,
    )
    assert "没有标记主角" in req.task_prompt
    assert "不是任何 NPC 的动作" in req.task_prompt


def test_global_iron_law_pins_player_to_protagonist():
    """全局铁律永远排在最前，主角归属必须写在这里，而不是只在某个任务模板里。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "config" / "system_prompt.md").read_text(encoding="utf-8")
    assert '"玩家"就是每轮上下文里标注的【主角】' in text
    assert "第五种行动" in text
    assert "不得把这个动作的主语换给某个 NPC" in text


def test_story_template_pins_choice_subject_to_protagonist():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "config" / "prompts" / "story_generation.md").read_text(encoding="utf-8")
    assert "主语归属（写错就是硬错误）" in text
    assert "每一项的主语都是主角" in text
    assert "第五个可选的自由输入" in text
    assert "不得替主角执行" in text


def test_end_scene_template_forbids_advancing_events():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "config" / "prompts" / "scene_end.md").read_text(encoding="utf-8")
    assert "本轮一律不推进" in text
    assert "下一幕由玩家自己用场景表单来定义" in text
    assert "本轮优先推进的预定事件" not in text, "旧的误导性标题必须移除"

"""世界元信息模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# 开场选项不足四项时用来补齐的中性行动。放在模型层，world_generator 与
# world_manager 都能用，避免两处各写一份、改了一边忘另一边。
FALLBACK_OPENING_CHOICES = (
    "先向在场的人问一句眼下发生了什么",
    "不急着开口，把四周仔细看一遍",
    "找一件手边的东西，确认它带来的线索",
    "暂时离开这里，等一个更合适的时机",
)


def normalize_opening_choices(raw: Any) -> list[dict[str, str]]:
    """收敛成恰好四项 A/B/C/D；空输入保持空（「完全自己写」不该被强塞选项）。"""
    if not isinstance(raw, list) or not raw:
        return []
    result: list[dict[str, str]] = []
    for item in raw[:4]:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()[:300]
        else:
            text = str(item or "").strip()[:300]
        if text:
            result.append({"id": "ABCD"[len(result)], "text": text})
    for index in range(len(result), 4):
        result.append({"id": "ABCD"[index], "text": FALLBACK_OPENING_CHOICES[index]})
    return result


class WorldMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    world_id: str
    name: str
    description: str = ""
    genre: str = ""
    created_at: str = ""
    updated_at: str = ""
    # 一句话生成世界时由模型产出、用户确认后固化的开场资料。
    # 放在元信息里，保证每个世界读自己的开场，不会串到别的世界。
    opening_scene: str = ""
    opening_location: str = ""
    opening_choices: list[dict[str, str]] = Field(default_factory=list)
    # 玩家点「开始该世界的故事」后才为真。写在 world.yaml（不进存档快照），
    # 因此读旧档也不会把「已开局」回滚成「未开局」。开局后人物上锁。
    story_started: bool = False


class WorldSummary(WorldMeta):
    """世界选择页要展示的卡片信息。"""

    character_count: int = 0
    save_count: int = 0
    last_played_at: str | None = None
    current_location: str = ""
    has_protagonist: bool = False


class CreateWorldRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    world_id: str | None = None
    name: str
    description: str = ""
    genre: str = ""
    # 只有「一句话生成世界」确认落盘时才带；手动创建时留空，写中性模板。
    setting: str = ""
    rules: str = ""
    opening_scene: str = ""
    opening_location: str = ""
    opening_choices: list[dict[str, str]] = Field(default_factory=list)
    # 玩家在世界自定义表单里写的「写手风格」；确认创建时写进
    # worlds/{world_id}/system_prompt.md，成为该世界的专属风格段。
    writer_style: str = Field(default="", max_length=2000)


class UpdateWorldRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    description: str | None = None
    genre: str | None = None


class CurrentState(BaseModel):
    """state/current.json：一个存档分支的运行状态。"""

    model_config = ConfigDict(extra="ignore")

    world_id: str
    save_id: str = "auto"
    turn: int = 0
    current_location: str = ""
    active_character_ids: list[str] = Field(default_factory=list)
    turns_since_reconcile: int = 0
    last_suggestion_notify_turn: int = 0
    # 已完成的预定事件 id。属于运行态，放在 current.json 才会随存档快照走分支，
    # 不能放进只读的 profile.yaml（否则读档回到过去时事件仍是「已完成」）。
    completed_planned_events: list[str] = Field(default_factory=list)
    updated_at: str = ""

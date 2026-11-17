"""剧情回合的输入输出模型。

注意区分两类键：
- narration / choices / character_updates 等是「输出协议」的结构键，可以写死。
- custom_fields 里的键是「用户自定义人物字段」，代码里绝不预设。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Dialogue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    speaker_id: str
    text: str = ""


class Choice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str = ""


class CharacterUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    character_id: str
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class SchemaSuggestion(BaseModel):
    """模型可选输出：觉得还缺哪个字段。只会被记录成建议，不会改 schema。"""

    model_config = ConfigDict(extra="ignore")

    field_id: str
    label: str = ""
    type: str = "text"
    reason: str = ""
    example: Any = None


class TurnOutput(BaseModel):
    """一轮剧情的完整结果。parse_warning / raw_text 由后端补充，不来自模型。"""

    model_config = ConfigDict(extra="ignore")

    narration: str = ""
    dialogues: list[Dialogue] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    character_updates: list[CharacterUpdate] = Field(default_factory=list)
    memory_candidates: list[str] = Field(default_factory=list)
    schema_suggestions: list[SchemaSuggestion] = Field(default_factory=list)
    parse_warning: str | None = None
    raw_text: str | None = None


class TurnRecord(BaseModel):
    """保存在对话历史里的一轮记录。"""

    model_config = ConfigDict(extra="ignore")

    turn: int
    player_action: str
    narration: str = ""
    dialogues: list[Dialogue] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    created_at: str = ""


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str
    choice_id: str | None = None
    save_id: str | None = None
    active_character_ids: list[str] = Field(default_factory=list)
    # 结构化场景表单（开局 / 换场景）：玩家填的这一幕场景描述与地点。
    scene_description: str = ""
    location: str = ""
    # 为真时本轮是「收束当前场景」：只写收尾、不给四个选项，前端转入下一幕表单。
    end_scene: bool = False


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    turn: int
    world_id: str
    save_id: str
    player_action: str
    output: TurnOutput
    characters: list[dict[str, Any]] = Field(default_factory=list)
    panels: dict[str, Any] = Field(default_factory=dict)
    current_location: str = ""
    suggestions_pending: int = 0
    # 明确告诉前端「这一轮是收束轮」，别再靠 choices 是否为空去猜。
    # 前端一旦看到它，就直接弹出下一幕的结构化表单。
    scene_ended: bool = False

"""人物数据模型：固定设定与动态状态彻底分开。

CharacterProfile  -> profile.yaml，普通剧情回合只读。
CharacterState    -> state.json，custom_fields 是完全动态的键值，不含任何固定字段。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CharacterProfile(BaseModel):
    """固定人物设定。刻意只保留结构性键，其余全部交给自定义字段。

    bible 是玩家逐条自定义、经 AI 补全并亲手确认后的「设定基线」，
    普通剧情回合只读；它不进 custom_fields，因此不会影响动态字段 Schema。
    老角色文件没有这个键时读成空字典，行为不变。
    """

    model_config = ConfigDict(extra="ignore")

    character_id: str
    name: str
    is_protagonist: bool = False
    core_description: str = ""
    bible: dict[str, Any] = Field(default_factory=dict)


class CharacterState(BaseModel):
    """人物此刻的动态状态。

    orphaned 对应文件里的 _orphaned：字段被用户从 schema 删除后，
    旧值不销毁、只搬到这里，方便在 Panel Designer 里恢复。
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    character_id: str
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    orphaned: dict[str, Any] = Field(default_factory=dict, alias="_orphaned")
    # 开局后角色「退场」：不再出现在生成上下文与在场名单，历史与文件保留，可撤回。
    retired: bool = False
    # 开局后新增角色：下一轮生成时作为「首次登场」注入剧情，生成成功后清除。
    pending_introduction: bool = False

    def dump_file(self) -> dict[str, Any]:
        """按文件名约定输出，保证写出的是 _orphaned 而不是 orphaned。"""
        return self.model_dump(by_alias=True, exclude_none=False)


class CharacterView(BaseModel):
    """给前端用的人物完整视图（人物卡 + 状态 + 生效字段定义）。"""

    model_config = ConfigDict(extra="ignore")

    profile: CharacterProfile
    state: CharacterState
    fields: list[dict[str, Any]] = Field(default_factory=list)


class GeneratedCharacter(BaseModel):
    """LLM 生成、尚未落盘的人物预览。必须经用户确认才会写成文件。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    core_description: str = ""
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    plot_hooks: list[str] = Field(default_factory=list)
    opening_situation: str = ""
    is_protagonist: bool = False
    bible: dict[str, Any] = Field(default_factory=dict)

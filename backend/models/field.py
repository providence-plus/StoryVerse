"""人物字段的「定义」模型。

这里是整个动态字段系统的中枢数据结构：字段有哪些、叫什么、什么类型、怎么更新，
全部来自 worlds/{world_id}/ui/panels.yaml，代码里不保留任何具体字段名单。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

FIELD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class FieldType(str, Enum):
    """受支持的字段类型。新增类型 = 在这里加一项 + 在 FieldRegistry 注册校验器 + 前端加渲染分支。"""

    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"
    LIST = "list"
    KEY_VALUE = "key_value"


class UpdatePolicy(str, Enum):
    """更新规则：决定后端是否接受 LLM 对这个字段的修改。"""

    STATIC = "static"                  # 固定设定，普通回合一律拒改
    MOSTLY_STATIC = "mostly_static"    # 原则上不变，仅在有明确剧情依据时允许
    STORY_DRIVEN = "story_driven"      # 随剧情按需更新


class FieldDefinition(BaseModel):
    """单个字段的定义。id 是稳定键，改名只改 label，不会丢历史值。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    description: str = ""
    type: FieldType = FieldType.TEXT
    visible: bool = True
    update_policy: UpdatePolicy = UpdatePolicy.STORY_DRIVEN
    options: list[str] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        # 只接受小写字母开头的标识符，避免有人用路径或特殊字符当字段名。
        if not FIELD_ID_PATTERN.match(value):
            raise ValueError(f"非法字段 id: {value!r}，需为小写字母开头的标识符")
        return value

    def to_prompt_dict(self) -> dict[str, Any]:
        """生成喂给 LLM 的最小字段说明；不把前端渲染细节塞进提示词。"""
        data: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "type": self.type.value,
            "update_policy": self.update_policy.value,
        }
        if self.type == FieldType.ENUM:
            data["options"] = self.options
        if self.type == FieldType.NUMBER and (self.min is not None or self.max is not None):
            data["range"] = {"min": self.min, "max": self.max}
        return data


class PanelGroup(BaseModel):
    """一类角色的字段集合。"""

    model_config = ConfigDict(extra="ignore")

    fields: list[FieldDefinition] = Field(default_factory=list)


class CharacterOverride(BaseModel):
    """为某个具体角色追加或覆盖字段（不影响同世界其他角色）。"""

    model_config = ConfigDict(extra="ignore")

    add_fields: list[FieldDefinition] = Field(default_factory=list)
    override_fields: list[FieldDefinition] = Field(default_factory=list)


class PanelSchema(BaseModel):
    """一个世界完整的 UI Schema。不同世界可以有完全不同的字段。"""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    same_fields_for_all_npcs: bool = True
    protagonist: PanelGroup = Field(default_factory=PanelGroup)
    npc: PanelGroup = Field(default_factory=PanelGroup)
    overrides: dict[str, CharacterOverride] = Field(default_factory=dict)

    def fields_for(self, character_id: str, is_protagonist: bool) -> list[FieldDefinition]:
        """返回某角色实际生效的字段定义（含该角色专属覆盖）。"""
        base = list(self.protagonist.fields if is_protagonist else self.npc.fields)

        # same_fields_for_all_npcs=false 时，NPC 的个性化字段只能来自 overrides。
        extra = self.overrides.get(character_id)
        if extra:
            replaced = {f.id: f for f in extra.override_fields}
            base = [replaced.get(f.id, f) for f in base]
            known = {f.id for f in base}
            base.extend(f for f in extra.add_fields if f.id not in known)
        return base

    def field_map(self, character_id: str, is_protagonist: bool) -> dict[str, FieldDefinition]:
        return {f.id: f for f in self.fields_for(character_id, is_protagonist)}

    def all_field_ids(self, character_id: str, is_protagonist: bool) -> set[str]:
        return set(self.field_map(character_id, is_protagonist))

    def to_prompt_list(self, character_id: str, is_protagonist: bool) -> list[dict[str, Any]]:
        return [f.to_prompt_dict() for f in self.fields_for(character_id, is_protagonist)]


class FieldSuggestion(BaseModel):
    """LLM 想新增字段时留下的建议。只记录，绝不自动写进 schema。"""

    model_config = ConfigDict(extra="ignore")

    field_id: str
    label: str = ""
    type: str = "text"
    reason: str = ""
    example: Any = None
    source_character_id: str = ""
    first_turn: int = 0
    last_turn: int = 0
    occurrences: int = 1
    status: str = "pending"  # pending | ignored | adopted
    notified_at_turn: int | None = None

"""模型输出校验器：所有 LLM 生成的内容在落盘前都必须过这一关。

关卡职责：结构校验 -> 角色归属 -> 字段键白名单 -> 类型校验 -> 更新策略过滤 -> 四选项补齐。
非法项只记录原因、不写入、不中断回合，保证玩家已经做出的行动不会丢。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from backend.core.field_registry import validate_value
from backend.models.field import PanelSchema, UpdatePolicy
from backend.models.story import CharacterUpdate, Choice, SchemaSuggestion, TurnOutput


@dataclass
class ValidationResult:
    output: TurnOutput
    rejected: list[str] = dc_field(default_factory=list)
    unknown_keys: list[tuple[str, str, Any]] = dc_field(default_factory=list)


def _normalize_choices(choices: list[Choice], fallbacks: list[str], rejected: list[str]) -> list[Choice]:
    """保证恰好四个 A/B/C/D，缺的用配置里的安全后备选项补齐。"""
    cleaned: list[Choice] = []
    seen: set[str] = set()
    for choice in choices:
        text = (choice.text or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(Choice(id="", text=text))
    if len(cleaned) > 4:
        cleaned = cleaned[:4]
        rejected.append("选项多于四项，已截断")
    if len(cleaned) < 4:
        rejected.append(f"选项不足四项（实际 {len(cleaned)} 项），已由后端补齐")
    index = 0
    while len(cleaned) < 4:
        text = fallbacks[index % len(fallbacks)] if fallbacks else "继续观察，暂不行动"
        if text not in seen:
            cleaned.append(Choice(id="", text=text))
            seen.add(text)
        index += 1
        if index > 20:
            break
    letters = ["A", "B", "C", "D"]
    for i, choice in enumerate(cleaned[:4]):
        choice.id = letters[i]
    return cleaned[:4]


def validate_turn_output(
    output: TurnOutput,
    schema: PanelSchema,
    character_kinds: dict[str, bool],
    active_character_ids: list[str],
    fallback_choices: list[str],
    max_memory_candidates: int = 3,
) -> ValidationResult:
    """把一份模型输出净化成可以安全写入的 TurnOutput。

    character_kinds: {character_id: 是否主角}，由 StateManager 从当前世界读取，
    因此模型不可能引用别的世界的角色 ID。
    """
    rejected: list[str] = []
    unknown: list[tuple[str, str, Any]] = []

    cleaned_choices = _normalize_choices(output.choices, fallback_choices, rejected)

    # 对话说话人必须是当前世界里存在且在场的人物。
    active = set(active_character_ids) or set(character_kinds)
    dialogues = []
    for d in output.dialogues:
        if d.speaker_id not in character_kinds:
            rejected.append(f"对话说话人不属于当前世界，已丢弃: {d.speaker_id}")
            continue
        if d.speaker_id not in active:
            rejected.append(f"对话说话人本轮不在场，已丢弃: {d.speaker_id}")
            continue
        dialogues.append(d)

    updates: list[CharacterUpdate] = []
    for update in output.character_updates:
        cid = update.character_id
        if cid not in character_kinds:
            rejected.append(f"状态更新的角色不属于当前世界，已整条丢弃: {cid}")
            continue
        field_map = schema.field_map(cid, character_kinds[cid])
        accepted: dict[str, Any] = {}
        for key, value in update.custom_fields.items():
            definition = field_map.get(key)
            if definition is None:
                unknown.append((cid, key, value))
                rejected.append(f"字段 {key} 不在当前世界 Schema 中，已拒写并记录为建议")
                continue
            if definition.update_policy == UpdatePolicy.STATIC:
                rejected.append(f"字段 {key} 为 static，剧情回合不得修改")
                continue
            ok, normalized = validate_value(definition, value)
            if not ok:
                rejected.append(f"字段 {key} 的值不符合类型 {definition.type.value}，已拒写")
                continue
            accepted[key] = normalized
        if accepted:
            updates.append(CharacterUpdate(character_id=cid, custom_fields=accepted))

    clean_output = TurnOutput(
        narration=output.narration.strip(),
        dialogues=dialogues,
        choices=cleaned_choices,
        character_updates=updates,
        memory_candidates=[str(m).strip() for m in output.memory_candidates if str(m).strip()][:max_memory_candidates],
        schema_suggestions=[s for s in output.schema_suggestions if s.field_id],
        parse_warning=output.parse_warning,
        raw_text=output.raw_text,
    )
    return ValidationResult(output=clean_output, rejected=rejected, unknown_keys=unknown)


def validate_generated_character(
    custom_fields: dict[str, Any],
    definitions: list[Any],
) -> tuple[dict[str, Any], list[str]]:
    """角色生成预览的字段校验：缺的补保守默认值，类型不对的拒收。"""
    issues: list[str] = []
    field_map = {f.id: f for f in definitions}
    accepted: dict[str, Any] = {}
    for key, value in custom_fields.items():
        definition = field_map.get(key)
        if definition is None:
            issues.append(f"生成了未定义的字段 {key}，已忽略")
            continue
        ok, normalized = validate_value(definition, value)
        if not ok:
            issues.append(f"字段 {key} 的值类型不符，已忽略")
            continue
        accepted[key] = normalized
    from backend.core.field_registry import default_value
    for key, definition in field_map.items():
        if key not in accepted:
            accepted[key] = default_value(definition)
            issues.append(f"字段 {key} 未生成，已填保守默认值")
    return accepted, issues


__all__ = ["ValidationResult", "validate_turn_output", "validate_generated_character", "SchemaSuggestion"]

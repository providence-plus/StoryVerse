"""自定义 + AI 补全：玩家逐条填写 -> 一次模型调用补写详实 -> 可编辑预览。

三条纪律（对应 docs/plan_v2_customization.md 第 10 节）：
1. 只返回数据，**绝不写任何世界 / 人物文件**（与现有 generate_preview 一致）。
2. 玩家原话是事实锚句：整段逐字保留；模型不会改写的最好保障是「Prompt 里显式注入
   + 玩家原话单独存 + 玩家最后精修」，后端只做整段子串检测与前置补回，
   不做碎片切分（会大量误报），也不假装能做语义矛盾检测。
3. 输出先按**固定键表**对齐再校验：长 JSON 常被长度上限从中间截断，
   parse_json_object 会补 `}` 返回一个「看起来成功」的对象，后面的键全丢。

预定事件清单与玩家原话一律以玩家表单为准，AI 无权改写或新增。
"""

from __future__ import annotations

from typing import Any

from backend.core import (
    bible_service,
    panel_service,
    prompt_builder,
    state_manager,
    world_generator,
    world_manager,
)
from backend.core.llm_provider import generate
from backend.core.story_parser import parse_json_object

ANCHOR_PREFIX = "【玩家设定（原文，不可违背）】"

NEUTRAL_FILL = {
    "appearance": "（这项设定尚未补全，可以直接在这里补写。）",
    "personality": "（这项设定尚未补全，可以直接在这里补写。）",
    "background": "（这项设定尚未补全，可以直接在这里补写。）",
    "major_events": "（这项设定尚未补全，可以直接在这里补写。）",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _anchor(player_input: str, ai_text: str, label: str, warnings: list[str]) -> dict[str, str]:
    """整段子串检测 + 前置锚句。不切碎片，不覆盖 AI 扩写。"""
    anchor = _text(player_input)
    final = _text(ai_text)
    if anchor and anchor not in final:
        final = f"{ANCHOR_PREFIX}{anchor}\n\n{final}" if final else f"{ANCHOR_PREFIX}{anchor}"
        warnings.append(f"「{label}」的玩家原话没有出现在模型补全里，已作为事实锚句原样置顶（AI 扩写保留）。")
    if not final:
        final = NEUTRAL_FILL.get(label, "（尚未补全）")
        if not anchor:
            label_text = bible_service.ITEM_LABELS.get(label, label)
            warnings.append(f"「{label_text}」玩家留空且模型没有补全，请在预览里手动填写。")
    return {"player_input": anchor, "text": final}


def _align_items(raw_items: Any, player_items: Any, warnings: list[str]) -> dict[str, dict[str, str]]:
    source = raw_items if isinstance(raw_items, dict) else {}
    player = player_items if isinstance(player_items, dict) else {}
    result: dict[str, dict[str, str]] = {}
    for key in bible_service.ITEM_KEYS:
        ai = source.get(key)
        ai_text = ""
        if isinstance(ai, str):
            ai_text = ai
        elif isinstance(ai, dict):
            ai_text = _text(ai.get("text"))
        own = player.get(key)
        if isinstance(own, str):
            player_input = own
        elif isinstance(own, dict):
            player_input = _text(own.get("player_input")) or _text(own.get("text"))
        else:
            player_input = ""
        if not ai_text and key not in source:
            warnings.append(f"「{bible_service.ITEM_LABELS[key]}」在模型输出里缺失（可能是长 JSON 被截断），已用玩家原话占位。")
        result[key] = _anchor(player_input, ai_text, key, warnings)
    return result


def _align_relations(
    raw_relations: Any,
    player_relations: Any,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """以玩家的关系卡为准：目标角色、名字、预定事件全部原样保留。"""
    ai_list = raw_relations if isinstance(raw_relations, list) else []
    own_list = player_relations if isinstance(player_relations, list) else []
    result: list[dict[str, Any]] = []
    for index, own in enumerate(own_list):
        if not isinstance(own, dict):
            continue
        ai = ai_list[index] if index < len(ai_list) and isinstance(ai_list[index], dict) else {}
        target_id = _text(own.get("target_character_id"))
        target_name = _text(own.get("target_name"))
        own_rel = own.get("relation") if isinstance(own.get("relation"), dict) else {}
        ai_rel = ai.get("relation") if isinstance(ai.get("relation"), dict) else {}
        relation = _anchor(
            _text(own_rel.get("player_input")),
            _text(ai_rel.get("text")),
            "relation",
            warnings,
        )

        past_events: list[dict[str, str]] = []
        own_past = own.get("past_events") if isinstance(own.get("past_events"), list) else []
        ai_past = ai.get("past_events") if isinstance(ai.get("past_events"), list) else []
        for p_index, item in enumerate(own_past):
            if not isinstance(item, dict):
                item = {"player_input": item if isinstance(item, str) else "", "text": ""}
            ai_item = ai_past[p_index] if p_index < len(ai_past) and isinstance(ai_past[p_index], dict) else {}
            past_events.append(_anchor(
                _text(item.get("player_input")) or _text(item.get("text")),
                _text(ai_item.get("text")),
                "past_event",
                warnings,
            ))

        # 预定事件是剧情任务清单，只有玩家能定，AI 不参与改写。
        # 预览阶段刻意不生成正式 id：正式 id 由 bible_service.normalize_bible()
        # 在确认保存时按「角色 ID + 关系卡位置 + 事件位置」重算；此刻角色还没建，
        # 拿不到 character_id，这里造出来的任何 id 都和落盘后的不一致，只会误导前端。
        # 前端预览只用 text 展示，勾选完成走 GET /planned-events 返回的正式 id。
        planned_events: list[dict[str, str]] = []
        seen: set[str] = set()
        for e_index, item in enumerate(own.get("planned_events") or []):
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = _text(item.get("text"))
            else:
                continue
            if not text:
                continue
            # 仅用于本卡内去重，不对外返回。
            dedup_key = bible_service.event_id(text, f"{target_id}:{index}:{e_index}")
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            planned_events.append({"id": "", "text": text})

        result.append({
            "target_character_id": target_id,
            "target_name": target_name,
            "relation": relation,
            "past_events": past_events,
            "planned_events": planned_events,
        })
    if own_list and len(ai_list) < len(own_list):
        warnings.append("模型返回的关系卡数量少于玩家填写的数量，缺失部分已用玩家原话占位。")
    return result


def _existing_characters(world_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for view in state_manager.list_characters(world_id):
        result[view.profile.character_id] = {
            "name": view.profile.name,
            "is_protagonist": view.profile.is_protagonist,
            "core_description": bible_service.clip(view.profile.core_description, 300),
        }
    return result


def generate_character_preview(
    world_id: str,
    inputs: dict[str, Any],
    is_protagonist: bool = False,
) -> dict[str, Any]:
    """人物自定义补全：一次调用，返回可逐条精修的预览。"""
    world_manager.validate_world_id(world_id)
    # 与送进模型的那份用同一套裁剪，保证锚句比对和落盘内容不会各说各话。
    form = prompt_builder.clip_customize_payload(inputs) if isinstance(inputs, dict) else {}
    schema = panel_service.load_schema(world_id)
    placeholder = "protagonist" if is_protagonist else "__preview_npc__"
    definitions = schema.fields_for(placeholder, is_protagonist)
    request = prompt_builder.build_character_customize_request(
        world_id=world_id,
        inputs=form,
        field_definitions=[f.to_prompt_dict() for f in definitions],
        existing_characters=_existing_characters(world_id),
    )
    result = generate(request)
    warnings: list[str] = []
    degraded = False
    data: dict[str, Any] | None = None
    raw_text = ""
    if result.error:
        warnings.append(f"模型补全失败：{result.error}。下面只保留你填写的原文，可以手动补全后保存，或检查模型服务后重试。")
        degraded = True
    else:
        raw_text = result.text
        data, reason = parse_json_object(result.text)
        if reason:
            warnings.append(f"模型输出做过格式修复：{reason}")
            degraded = True
        if data is None:
            warnings.append("模型输出无法解析为设定 JSON，已退回你填写的原文，请手动补全或重试。")
            degraded = True
            data = {}

    items = _align_items(data.get("items"), form.get("items"), warnings)
    relations = _align_relations(data.get("relations"), form.get("relations"), warnings)
    # 姓名 / 定位：玩家填了就以玩家为准（不能落成「未命名角色」），玩家留空才用模型建议。
    name = _text(form.get("name")) or _text(data.get("name")) or "未命名角色"
    core = _text(form.get("core_description")) or _text(data.get("core_description"))
    if name == "未命名角色":
        warnings.append("角色姓名为空，请在预览里补上再保存。")

    return {
        "name": name,
        "core_description": core,
        "items": items,
        "relations": relations,
        "warnings": warnings,
        "provider": result.provider,
        "model": result.model,
        "raw_text": raw_text if degraded else "",
        "degraded": degraded,
    }


def _align_world(
    data: dict[str, Any],
    form: dict[str, Any],
    warnings: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """世界预览先过 world_generator 的白名单规范化，再逐项做锚句保真。"""
    clean = world_generator._normalize(  # noqa: SLF001 - 复用同一套世界预览规范化，避免两份规则漂移
        data,
        _text(form.get("description")) or _text(form.get("name")) or "玩家自定义世界",
        _text(form.get("genre")),
        warnings,
    )
    # 名称 / 类型 / 简介 / 地点是结构化短字段：玩家填了就直接采用，
    # 不能套锚句前缀，否则「【玩家设定（原文，不可违背）】」会被写进 world.yaml。
    for key in ("name", "genre", "description", "opening_location"):
        player_input = _text(form.get(key))
        if player_input:
            clean[key] = player_input
    # 长正文条目才做锚句保真：玩家原话必须逐字出现，否则置顶补回、保留 AI 扩写。
    for key in ("setting", "rules", "opening_scene"):
        player_input = _text(form.get(key))
        if not player_input:
            continue
        ai_text = _text(clean.get(key))
        if player_input not in ai_text:
            clean[key] = f"{ANCHOR_PREFIX}{player_input}\n\n{ai_text}" if ai_text else f"{ANCHOR_PREFIX}{player_input}"
            warnings.append(f"世界条目「{key}」的玩家原话没有出现在模型补全里，已作为事实锚句原样置顶。")
    # 玩家写的开场选项不能被悄悄换成别的行动。
    own_choices = form.get("opening_choices")
    if isinstance(own_choices, list):
        texts = [_text(c.get("text")) if isinstance(c, dict) else _text(c) for c in own_choices]
        fixed = list(clean.get("opening_choices") or [])
        for index, text in enumerate(texts[:4]):
            if text and index < len(fixed) and text not in _text(fixed[index].get("text")):
                fixed[index] = {"id": "ABCD"[index], "text": text}
        clean["opening_choices"] = fixed
    # 写手风格由程序原样保存，AI 无权改写。
    clean["writer_style"] = _text(form.get("writer_style"))
    return clean, warnings


def generate_world_preview(items: dict[str, Any]) -> dict[str, Any]:
    """世界自定义补全：一次调用，返回与「一句话生成」同结构的可编辑预览。"""
    form = prompt_builder.clip_customize_payload(items) if isinstance(items, dict) else {}
    # writer_style 只作为风格提示注入任务 Prompt，不放进模型要输出的 JSON。
    model_items = {k: v for k, v in form.items() if k != "writer_style"}
    request = prompt_builder.build_world_customize_request(model_items, _text(form.get("writer_style")))
    result = generate(request)
    warnings: list[str] = []
    degraded = False
    data: dict[str, Any] | None = None
    raw_text = ""
    if result.error:
        warnings.append(f"模型补全失败：{result.error}。下面只保留你填写的原文，可以手动补全后创建。")
        degraded = True
        data = {}
    else:
        raw_text = result.text
        data, reason = parse_json_object(result.text)
        if reason:
            warnings.append(f"模型输出做过格式修复：{reason}")
            degraded = True
        if data is None:
            warnings.append("模型输出无法解析为设定 JSON，已退回你填写的原文，请手动补全或重试。")
            degraded = True
            data = {}
        # 长 JSON 被截断时后面的键会整个消失；用玩家原话先垫上。
        for key in ("setting", "rules", "opening_scene", "opening_choices", "npc_hints"):
            if key not in data:
                warnings.append(f"世界条目「{key}」在模型输出里缺失（可能是长 JSON 被截断），已用你填写的内容占位。")
                degraded = True
                data[key] = form.get(key)

    clean, warnings = _align_world(data or {}, form, warnings)
    return {
        "preview": clean,
        "warnings": warnings,
        "provider": result.provider,
        "model": result.model,
        "raw_text": raw_text if degraded else "",
        "degraded": degraded,
    }

"""设定基线（bible）服务：玩家自定义条目的唯一读写与裁剪入口。

设计要点（对应 docs/plan_v2_customization.md 第 10 节）：
- bible 存在 profile.yaml 的固定设定区，普通剧情回合只读；不进 custom_fields。
- 完成态不在这里：completed_planned_events 属于运行态，存 state/current.json，
  由存档快照带走，避免「读档回到过去，事件却还是已完成」。
- 注入剧情 Prompt 前必须裁剪。完整 bible 可能有上万字，不裁剪会把
  prompt 撑过模型服务的上下文窗口，导致全局铁律被从左侧截掉。

本模块只依赖 file_utils / world_manager，不引用 state_manager / prompt_builder，
因此可以安全地被两者同时导入，不会形成循环依赖。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from backend.core import file_utils, world_manager

ITEM_KEYS = ("appearance", "personality", "background", "major_events")
ITEM_LABELS = {
    "appearance": "外貌",
    "personality": "性格",
    "background": "背景",
    "major_events": "角色背景重大事件",
}

# 存储上限：防止把整本小说粘进一个条目。
MAX_ITEM_CHARS = 20000
MAX_RELATION_CARDS = 12
MAX_EVENTS_PER_CARD = 20
MAX_EVENT_CHARS = 2000

# 注入剧情 Prompt 时的限额（字符数近似，不引入 tokenizer 依赖）。
PROMPT_ITEM_CHARS = 260
PROMPT_RELATION_CHARS = 220
PROMPT_EVENT_CHARS = 160
PROMPT_BIBLE_TOTAL_CHARS = 3200

_UID_CLEAN = re.compile(r"[^a-zA-Z0-9_-]")


def clip(text: Any, limit: int) -> str:
    """保守截断，并在真的截断时留一个可读标记。"""
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…（后略）"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def event_id(text: str, seed: str = "") -> str:
    """由内容生成稳定 id：同一句话重复保存不会换 id，已完成标记不会失效。"""
    digest = hashlib.md5(f"{seed}|{text}".encode("utf-8")).hexdigest()[:10]
    return f"evt_{digest}"


def blank_item(player_input: str = "", text: str = "") -> dict[str, str]:
    return {"player_input": _text(player_input), "text": _text(text)}


def blank_bible() -> dict[str, Any]:
    return {
        "items": {key: blank_item() for key in ITEM_KEYS},
        "relations": [],
    }


def _normalize_item(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        # 容错：老数据/手填可能直接给字符串，当成玩家原话。
        return blank_item(player_input=raw, text=raw)
    if not isinstance(raw, dict):
        return blank_item()
    return blank_item(
        player_input=clip(raw.get("player_input"), MAX_ITEM_CHARS),
        text=clip(raw.get("text"), MAX_ITEM_CHARS),
    )


def normalize_bible(raw: Any, character_id: str = "") -> dict[str, Any]:
    """把任意来源（前端表单、模型输出、手改文件）收敛成可安全落盘的结构。"""
    source = raw if isinstance(raw, dict) else {}
    raw_items = source.get("items") if isinstance(source.get("items"), dict) else {}
    result = blank_bible()
    for key in ITEM_KEYS:
        result["items"][key] = _normalize_item(raw_items.get(key))

    raw_relations = source.get("relations") if isinstance(source.get("relations"), list) else []
    for rel_index, raw_rel in enumerate(raw_relations[:MAX_RELATION_CARDS]):
        if not isinstance(raw_rel, dict):
            continue
        target_id = _text(raw_rel.get("target_character_id"))
        # 目标角色必须是个像样的 ID；前端只能从下拉里选，这里是防御性兜底。
        if target_id and not re.match(r"^[A-Za-z0-9_-]{1,64}$", target_id):
            continue
        card: dict[str, Any] = {
            "target_character_id": target_id,
            "target_name": clip(raw_rel.get("target_name"), 120),
            "relation": _normalize_item(raw_rel.get("relation")),
            "past_events": [],
            "planned_events": [],
        }
        past = raw_rel.get("past_events") if isinstance(raw_rel.get("past_events"), list) else []
        for item in past[:MAX_EVENTS_PER_CARD]:
            normalized = _normalize_item(item)
            if normalized["text"] or normalized["player_input"]:
                card["past_events"].append(normalized)

        planned = raw_rel.get("planned_events") if isinstance(raw_rel.get("planned_events"), list) else []
        seen: set[str] = set()
        for evt_index, item in enumerate(planned[:MAX_EVENTS_PER_CARD]):
            if isinstance(item, str):
                evt_text = item.strip()
            elif isinstance(item, dict):
                evt_text = _text(item.get("text"))
            else:
                continue
            if not evt_text:
                continue
            evt_text = clip(evt_text, MAX_EVENT_CHARS)
            # 一律按内容 + 稳定的位置种子重算 id，忽略前端传来的临时 id：
            # 这样不同关系卡不会撞 id，且同一句话重复保存 id 不变，完成标记不会失效。
            eid = event_id(evt_text, f"{character_id}:{rel_index}:{evt_index}")
            if eid in seen:
                continue
            seen.add(eid)
            # 刻意不存 done：完成态属于运行态，见模块说明。
            card["planned_events"].append({"id": eid, "text": evt_text})
        result["relations"].append(card)
    return result


def bible_for_prompt(bible: Any, names: dict[str, str] | None = None) -> str:
    """生成给剧情模型看的精简固定设定；总长度有硬上限。

    玩家原话单独放在最前面并标注为最高优先级事实，AI 补全的定稿随后，
    这样即使 AI 扩写时措辞走样，事实锚句也一定在上下文里。
    """
    data = normalize_bible(bible)
    names = names or {}
    parts: list[str] = []
    for key in ITEM_KEYS:
        item = data["items"][key]
        anchor = item["player_input"].strip()
        final = item["text"].strip()
        if not anchor and not final:
            continue
        block = f"【{ITEM_LABELS[key]}】"
        if anchor:
            block += f"\n玩家原话（事实锚句，不得与之矛盾）：{clip(anchor, PROMPT_ITEM_CHARS)}"
        if final and final != anchor:
            block += f"\n定稿：{clip(final, PROMPT_ITEM_CHARS)}"
        parts.append(block)

    for card in data["relations"]:
        target = card.get("target_name") or names.get(card.get("target_character_id", ""), "")
        target = target or card.get("target_character_id") or "某位角色"
        rel = card["relation"]
        block = f"【与 {target} 的关系】"
        if rel["player_input"]:
            block += f"\n玩家原话（事实锚句）：{clip(rel['player_input'], PROMPT_RELATION_CHARS)}"
        if rel["text"]:
            block += f"\n定稿：{clip(rel['text'], PROMPT_RELATION_CHARS)}"
        past = [clip(i["text"] or i["player_input"], PROMPT_EVENT_CHARS)
                for i in card["past_events"] if (i["text"] or i["player_input"])]
        if past:
            block += "\n两人间已发生的关键事件（既成事实，不得改写）：\n- " + "\n- ".join(past)
        parts.append(block)
    return clip("\n\n".join(parts), PROMPT_BIBLE_TOTAL_CHARS)


def _profile_files(world_id: str) -> list[tuple[str, Any]]:
    """列出本世界所有角色 profile.yaml，返回 (角色 ID, 文件数据)。"""
    world_manager.validate_world_id(world_id)
    found: list[tuple[str, Any]] = []
    proto = world_manager.resolve(world_id, "characters", "protagonist", "profile.yaml")
    if proto.is_file():
        data = file_utils.read_yaml(proto, {}) or {}
        found.append((str(data.get("character_id") or "protagonist"), data))
    npc_root = world_manager.resolve(world_id, "characters", "npcs")
    if npc_root.is_dir():
        for folder in sorted(npc_root.iterdir()):
            path = folder / "profile.yaml"
            if folder.is_dir() and path.is_file():
                data = file_utils.read_yaml(path, {}) or {}
                found.append((str(data.get("character_id") or folder.name), data))
    return found


def collect_planned_events(
    world_id: str,
    active_ids: list[str] | None = None,
    completed_ids: list[str] | None = None,
    *,
    for_prompt: bool = True,
) -> list[dict[str, str]]:
    """收集本轮该推进的预定事件。

    过滤规则：关系双方**任一在场**就收集（事件存在 A 的 bible 里，但本轮
    只有 B 在场时也要能推进）；已完成的 id 不再注入。

    for_prompt=True 时把 text 裁到 PROMPT_EVENT_CHARS，防止长清单撑爆上下文；
    人物列表的 GET 接口要显示玩家原文，用 for_prompt=False 拿完整文本
    （落盘时已受 MAX_EVENT_CHARS 限制，不会无限长）。
    """
    active = set(active_ids or [])
    completed = set(completed_ids or [])
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for owner_id, data in _profile_files(world_id):
        profile = data if isinstance(data, dict) else {}
        owner_name = str(profile.get("name") or owner_id)
        bible = normalize_bible(profile.get("bible"), owner_id)
        for card in bible["relations"]:
            target_id = card.get("target_character_id", "")
            # 没有在场列表时视为全员在场；有列表时按「双方任一在场」判断。
            if active and owner_id not in active and target_id not in active:
                continue
            for evt in card["planned_events"]:
                eid = evt.get("id", "")
                if not eid or eid in completed or eid in seen:
                    continue
                seen.add(eid)
                result.append({
                    "id": eid,
                    "text": clip(evt.get("text"), PROMPT_EVENT_CHARS)
                    if for_prompt else str(evt.get("text") or ""),
                    "owner_id": owner_id,
                    "owner_name": owner_name,
                    "target_character_id": target_id,
                    "target_name": card.get("target_name", ""),
                })
    return result


def planned_events_for_prompt(events: list[dict[str, str]]) -> str:
    """注入剧情前再裁一次：即使调用方传的是未裁剪的全文，也不会撑爆上下文。"""
    if not events:
        return "无"
    lines = []
    for evt in events[:12]:
        who = f"{evt.get('owner_name') or evt.get('owner_id')} 与 {evt.get('target_name') or evt.get('target_character_id') or '对方'}"
        body = clip(evt.get("text", ""), PROMPT_EVENT_CHARS)
        lines.append(f"- [{evt.get('id')}] {who}：{body}")
    return "\n".join(lines)


def all_event_ids(world_id: str) -> set[str]:
    """用于校验 PATCH 请求里的事件 id 确实属于本世界。"""
    return {evt["id"] for evt in collect_planned_events(world_id)}

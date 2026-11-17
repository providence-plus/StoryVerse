"""一句话生成世界：LLM 扩写 -> 后端校验 -> 前端预览可编辑 -> 用户确认 -> 才落盘。

与人物生成遵循同一条纪律：模型只返回文本，绝不接触文件系统；
写 setting.md / rules.md / world.yaml 的唯一入口是这里的 confirm_world()。
世界尚未创建时不会读取任何世界目录，因此不可能混入别的世界的设定。
"""

from __future__ import annotations

import re
from typing import Any

from backend.core import prompt_builder, world_manager
from backend.core.llm_provider import generate
from backend.core.story_parser import parse_json_object
from backend.models.world import FALLBACK_OPENING_CHOICES as FALLBACK_CHOICES, CreateWorldRequest, WorldMeta

CODE_FENCE = re.compile(r"^\s*```(?:markdown|md|text)?\s*\n?([\s\S]*?)\n?\s*```\s*$", re.IGNORECASE)


def _as_text(value: Any, limit: int = 20000) -> str:
    """模型可能把长文塞进 Markdown 代码块，这里剥掉围栏并限制长度。"""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    match = CODE_FENCE.match(text)
    if match:
        text = match.group(1).strip()
    return text[:limit]


def _clean_choices(raw: Any) -> list[dict[str, str]]:
    """无论模型给的是对象数组还是字符串数组，都整理成恰好四项 A/B/C/D。"""
    result: list[dict[str, str]] = []
    items = raw if isinstance(raw, list) else []
    for index, item in enumerate(items[:4]):
        if isinstance(item, dict):
            text = _as_text(item.get("text"), 300)
            cid = _as_text(item.get("id"), 4).upper()
        else:
            text = _as_text(item, 300)
            cid = ""
        if not text:
            continue
        result.append({"id": cid if cid in "ABCD" and len(cid) == 1 else "ABCD"[index],
                       "text": text})
    for index in range(len(result), 4):
        result.append({"id": "ABCD"[index], "text": FALLBACK_CHOICES[index]})
    # 去重 id，防止模型全填成 A。
    for index, choice in enumerate(result):
        choice["id"] = "ABCD"[index]
    return result


def _normalize(raw: dict[str, Any], description: str, genre_hint: str, warnings: list[str]) -> dict[str, Any]:
    """把模型输出收敛成可直接展示、可直接落盘的安全结构。"""
    name = _as_text(raw.get("name"), 60).replace("\n", " ").strip()
    if not name:
        name = description.replace("\n", " ")[:12] or "未命名世界"
    setting = _as_text(raw.get("setting"))
    rules = _as_text(raw.get("rules"))
    scene = _as_text(raw.get("opening_scene"), 4000)
    if len(setting) < 40:
        warnings.append("模型返回的世界观背景过短，建议手动补充 setting 内容后再创建。")
    if len(rules) < 20:
        warnings.append("模型返回的世界规则过短，建议手动补充 rules 内容后再创建。")
    hints = raw.get("npc_hints") if isinstance(raw.get("npc_hints"), list) else []
    return {
        "name": name,
        "genre": _as_text(raw.get("genre"), 120) or genre_hint or "未分类",
        "description": _as_text(raw.get("description"), 1000) or description,
        "setting": setting,
        "rules": rules,
        "opening_location": _as_text(raw.get("opening_location"), 120) or "故事起点",
        "opening_scene": scene,
        "opening_choices": _clean_choices(raw.get("opening_choices")),
        "protagonist_hint": _as_text(raw.get("protagonist_hint"), 500),
        "npc_hints": [_as_text(h, 500) for h in hints[:6] if isinstance(h, str)],
        # 写手风格是玩家在世界自定义里填的，不经过模型改写，必须一路带到落盘。
        "writer_style": _as_text(raw.get("writer_style"), 4000),
    }


def generate_preview(description: str, genre_hint: str = "") -> dict[str, Any]:
    """只产出预览，不创建任何目录、不写任何文件。"""
    if not description.strip():
        raise ValueError("请先写一句世界设定")
    request = prompt_builder.build_world_request(description.strip(), genre_hint.strip())
    result = generate(request)
    if result.error:
        # 模型没接通时明确失败，绝不假装「已生成」再悄悄塞一句原话进去。
        raise RuntimeError(result.error)
    data, reason = parse_json_object(result.text)
    warnings: list[str] = []
    if reason:
        # 自动修复过（换行 / 引号 / 截断补齐）必须明确告诉玩家，不能伪装成完美结果。
        warnings.append(reason)
    if data is None:
        snippet = (result.text or "").strip()[:400]
        raise RuntimeError(
            f"世界设定生成结果无法解析：{reason}。模型原文开头：{snippet}。"
            "可在 Prompt Studio 调整 world_generator.md，或调高 Max Tokens 后重试。"
        )

    preview = _normalize(data, description.strip(), genre_hint.strip(), warnings)
    if not preview["opening_scene"]:
        warnings.append("开场画面字段缺失（通常是输出被截断），已用世界观简介代替；也可在下方预览里手动补写。")
        preview["opening_scene"] = (
            f"你站在{preview['opening_location']}。{preview['description']}"
            "眼前的一切尚未展开，等你迈出第一步。"
        )
    return {
        "preview": preview,
        "warnings": warnings,
        "provider": result.provider,
        "model": result.model,
        "raw_text": result.text if warnings else "",
    }


def confirm_world(payload: dict[str, Any]) -> WorldMeta:
    """用户在预览里编辑并点确认之后，才创建世界目录并写入扩写后的设定。"""
    if not isinstance(payload, dict):
        raise ValueError("世界预览数据格式不正确")
    warnings: list[str] = []
    clean = _normalize(payload, _as_text(payload.get("description"), 200), "", warnings)
    if not clean["setting"].strip():
        raise ValueError("世界观背景不能为空，请在预览里补充 setting 内容")
    return world_manager.create_world(CreateWorldRequest(
        name=clean["name"],
        description=clean["description"],
        genre=clean["genre"],
        setting=clean["setting"],
        rules=clean["rules"],
        opening_scene=clean["opening_scene"],
        opening_location=clean["opening_location"],
        opening_choices=clean["opening_choices"],
        writer_style=clean.get("writer_style", ""),
    ))

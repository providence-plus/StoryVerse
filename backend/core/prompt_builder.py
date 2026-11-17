"""Prompt Builder：唯一负责「这一轮给模型看什么」的模块。

两条硬约束：
1. 每次调用都从文件系统重新读取 Prompt 文件，Prompt Studio 保存后下一轮立即生效；
2. 只接受 world_id 一个世界入口，绝不拼接其他世界的设定、人物、记忆或字段定义。

占位符 {{...}} 由 render() 注入，模板文件里不出现任何具体人物字段名。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.core import bible_service, file_utils, panel_service, settings_service, world_manager
from backend.core.field_registry import describe_types
from backend.core.llm_provider import LLMRequest, max_prompt_chars

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPT_DIR = CONFIG_DIR / "prompts"

# 白名单：GET/PUT /api/prompts/{name} 只认这些名字，从根上挡住任意路径读写。
PROMPT_FILES: dict[str, Path] = {
    "system_prompt": CONFIG_DIR / "system_prompt.md",
    "story_generation": PROMPT_DIR / "story_generation.md",
    "npc_generator": PROMPT_DIR / "npc_generator.md",
    "protagonist_generator": PROMPT_DIR / "protagonist_generator.md",
    "memory_extractor": PROMPT_DIR / "memory_extractor.md",
    "field_backfill": PROMPT_DIR / "field_backfill.md",
    "field_reconcile": PROMPT_DIR / "field_reconcile.md",
    "world_generator": PROMPT_DIR / "world_generator.md",
    "world_customize": PROMPT_DIR / "world_customize.md",
    "character_customize": PROMPT_DIR / "character_customize.md",
    "scene_end": PROMPT_DIR / "scene_end.md",
}

PROMPT_LABELS: dict[str, str] = {
    "system_prompt": "全局 System Prompt",
    "story_generation": "剧情生成",
    "npc_generator": "NPC 生成",
    "protagonist_generator": "主角生成",
    "memory_extractor": "记忆整理",
    "field_backfill": "新增字段回填",
    "field_reconcile": "字段定期校准",
    "world_generator": "一句话生成世界",
    "world_customize": "自定义世界补全",
    "character_customize": "自定义人物补全",
    "scene_end": "收束当前场景",
}


def read_prompt(name: str) -> str:
    """每次都重新读盘，不做任何缓存。"""
    path = PROMPT_FILES.get(name)
    if path is None:
        raise ValueError(f"未知的 Prompt 名称: {name}")
    return file_utils.read_text(path, default="")


def list_prompts() -> list[dict[str, Any]]:
    result = []
    for name, path in PROMPT_FILES.items():
        content = file_utils.read_text(path, default="")
        result.append({
            "name": name,
            "label": PROMPT_LABELS.get(name, name),
            "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "size": len(content),
        })
    return result


def get_prompt(name: str) -> dict[str, Any]:
    if name not in PROMPT_FILES:
        raise ValueError(f"未知的 Prompt 名称: {name}")
    return {
        "name": name,
        "label": PROMPT_LABELS.get(name, name),
        "content": read_prompt(name),
        # PromptStudio 的 select() 依赖 path/size，之前漏了会显示「—」。
        "path": str(PROMPT_FILES[name].relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "size": len(read_prompt(name)),
    }


_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_WORLD_STYLE_FILE = "system_prompt.md"

# 玩家可以在 Prompt Studio / 世界风格文件里自由编辑这些文本。
# 不加限额时，一段几万字的粘贴就能把整个上下文窗口吃满，导致全局铁律被左截断。
GLOBAL_SYSTEM_CHARS = 6000
WORLD_STYLE_CHARS = 2000
TASK_TEMPLATE_CHARS = 12000


def read_world_system_prompt(world_id: str) -> str:
    """读世界专属风格段；文件不存在/为空/只有注释都视为「没有风格」。"""
    try:
        path = world_manager.resolve(world_id, _WORLD_STYLE_FILE)
        raw = file_utils.read_text(path, default="")
    except (OSError, ValueError):
        return ""
    return bible_service.clip(_HTML_COMMENT.sub("", raw).strip(), WORLD_STYLE_CHARS)


def system_prompt_for(world_id: str | None) -> str:
    """所有模型调用的唯一 System Prompt 入口：全局铁律在前，世界风格追加。

    每次调用都重新读盘，所以玩家在游戏里改全局或改世界 Prompt，
    下一次生成立即生效；正在生成中的那一轮用的是发出时拼好的字符串，不受影响。
    存档快照不包含任何 Prompt 文件，读档也不会把改动回滚。
    """
    base = bible_service.clip(read_prompt("system_prompt").strip(), GLOBAL_SYSTEM_CHARS)
    style = read_world_system_prompt(world_id) if world_id else ""
    if not style:
        return base
    return (
        base
        + "\n\n---\n\n# 本世界专属写手风格\n\n"
        + style
        + "\n\n（以上只调整文风与题材腔调；不得推翻前面的基础写作铁律、玩家自主权、"
          "「世界观与固定人物设定只能引用不能改写」的约束，以及严格 JSON 输出协议。）"
    )


def save_prompt(name: str, content: str) -> None:
    path = PROMPT_FILES.get(name)
    if path is None:
        raise ValueError(f"未知的 Prompt 名称: {name}")
    # 原子写入：编辑器保存中途失败也不会留下半截 Prompt。
    file_utils.atomic_write_text(path, content)


def _json(value: Any) -> str:
    # 紧凑序列化：缩进美化在长设定 / 多人物时会多出成千上万字符，
    # 对模型理解没有帮助，却会挤占上下文窗口。
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render(template: str, values: dict[str, Any], *, clip_template: bool = True) -> str:
    """把 {{key}} 替换成实际内容；dict/list 自动序列化成 JSON。

    bounded_render 已经裁过模板并补齐占位符，此时必须传 clip_template=False，
    否则二次裁剪会把刚补回来的占位符又切掉。
    """
    # 任务模板也是玩家可编辑的文件，给一个上限防止把上下文窗口吃满。
    result = bible_service.clip(template, TASK_TEMPLATE_CHARS) if clip_template else template
    for key, value in values.items():
        rendered = _json(value) if isinstance(value, (dict, list)) else str(value)
        result = result.replace("{{" + key + "}}", rendered)
    return result


# 上下文窗口保护（审核 M3）：世界设定、摘要、历史、人物圣经都可能很长。
# 不裁剪时 prompt_n + max_tokens 会超过模型服务的 -c，llama.cpp 会从**左边**
# 截断 prompt，正好切掉最前面的全局铁律。这里给每个来源设硬上限。
WORLD_MAIN_FILE_CHARS = 2600
WORLD_EXTRA_FILE_CHARS = 700
WORLD_CONTEXT_TOTAL_CHARS = 9000
SUMMARY_CHARS = 2200
HISTORY_ENTRY_CHARS = 500
BIBLE_TOTAL_FOR_STORY = 10000
# 一轮剧情 Prompt 里所有「可膨胀来源」共享的字符总预算。
# 分项限额只能防单点，玩家完全可以把每个可编辑文件都写满；统一预算才能兜住总量。
MAX_STORY_PROMPT_CHARS = 24000


# 留给 system_prompt / context / history / user_action / 消息拼装开销的字符数；
# 剩下全给任务 Prompt，保证「prompt + max_tokens」不会超过模型服务的 -c。
TASK_OVERHEAD_CHARS = 6000
USER_ACTION_CHARS = 1000
# 每个占位符至少能拿到的字符数。没有这个保底，「先到先得」会让排在后面的
# 字段定义 / 玩家原话被前面的大 JSON 挤成空串，模型就收不到关键输入了。
MIN_PIECE_CHARS = 320
PIECE_MARK = "…（其余略）"


def _task_chars(max_tokens: int) -> int:
    """按这次请求的输出预算，算出任务 Prompt 最多能占多少字符。

    下限 12000 是硬性的：默认 max_tokens=16384 时窗口只剩 15872，
    再减去开销也不该把任务压到连模板 + 字段定义都放不下的程度。
    真超窗口时由 llm_provider.build_messages 做最后一道保头保尾的裁剪。
    """
    return max(12000, min(MAX_STORY_PROMPT_CHARS, max_prompt_chars(max_tokens) - TASK_OVERHEAD_CHARS))


_PLACEHOLDER = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")
_DANGLING_PLACEHOLDER = re.compile(r"\{\{[^}]*$")


def _prepare_template(name: str, keys: list[str]) -> str:
    """裁模板，但保证每个 {{key}} 都还在；被截掉的占位符补到末尾。

    玩家可以在 Prompt Studio 里把模板写到几万字，占位符可能全在长文之后。
    直接截断会让 render 找不到占位符，注入的数据凭空消失。
    """
    raw = _DANGLING_PLACEHOLDER.sub("", read_prompt(name))
    clipped = bible_service.clip(raw, TASK_TEMPLATE_CHARS)
    clipped = _DANGLING_PLACEHOLDER.sub("", clipped)
    missing = [key for key in keys if "{{" + key + "}}" not in clipped]
    if missing:
        clipped += "\n\n" + "\n".join("{{" + key + "}}" for key in missing)
    return clipped


def _allocate(pieces: dict[str, str], room: int) -> dict[str, str]:
    """两轮分配：先给每个占位符保底，再按字典顺序（=优先级）补足剩余额度。

    dict 顺序即优先级，调用方要把「玩家原话 / 本轮输入 / 字段定义」放前面，
    把「世界背景 / 历史」这类可压缩的大块放后面。
    """
    keys = list(pieces)
    if not keys:
        return {}
    reserve = len(PIECE_MARK) * len(keys)
    usable = max(len(keys), room - reserve)
    floor = max(1, min(MIN_PIECE_CHARS, usable // len(keys)))
    result: dict[str, str] = {}
    used = 0
    for key in keys:
        text = pieces[key]
        grant = min(len(text), floor, max(0, usable - used))
        result[key] = text[:grant]
        used += grant
    for key in keys:
        if used >= usable:
            break
        text = pieces[key]
        extra = min(len(text) - len(result[key]), usable - used)
        if extra > 0:
            result[key] += text[len(result[key]):len(result[key]) + extra]
            used += extra
    return {key: (result[key] + PIECE_MARK if len(result[key]) < len(pieces[key]) else result[key])
            for key in keys}


def bounded_render(
    name: str,
    values: dict[str, Any],
    max_tokens: int,
) -> str:
    """模板 + 所有注入值共用一个字符预算，且每个占位符都有保底额度。"""
    serialized = {
        key: (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
              if isinstance(value, (dict, list)) else str(value or ""))
        for key, value in values.items()
    }
    template = _prepare_template(name, list(serialized))
    total = _task_chars(max_tokens)
    room = max(len(serialized) * MIN_PIECE_CHARS, total - len(template))
    pieces = _allocate(serialized, room)
    # 模板本身也要进总预算：玩家把它写满时，让 build_messages 保住头尾。
    return render(template, pieces, clip_template=False)


def world_context(world_id: str, current_location: str = "") -> str:
    """只读当前世界目录下的设定文件，路径全部经 resolve 校验，且有长度上限。"""
    parts: list[str] = []
    for name, title in (("setting.md", "世界观背景"), ("rules.md", "世界规则")):
        text = file_utils.read_text(world_manager.resolve(world_id, name)).strip()
        if text:
            parts.append(f"## {title}\n{bible_service.clip(text, WORLD_MAIN_FILE_CHARS)}")
    extras = 0
    for folder in ("locations", "lore"):
        root = world_manager.resolve(world_id, folder)
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            if extras >= 4:
                break
            if path.name.startswith("README"):
                continue
            text = file_utils.read_text(path).strip()
            if text:
                parts.append(f"## {folder}/{path.name}\n{bible_service.clip(text, WORLD_EXTRA_FILE_CHARS)}")
                extras += 1
    if current_location:
        parts.append(f"## 当前地点\n{current_location}")
    return bible_service.clip("\n\n".join(parts), WORLD_CONTEXT_TOTAL_CHARS)


def _generation_params() -> dict[str, Any]:
    llm = settings_service.llm_settings()
    return {
        "temperature": float(llm.get("temperature", 0.85)),
        "max_tokens": int(llm.get("max_tokens", 8192)),
        "timeout_seconds": float(llm.get("timeout_seconds", 300)),
    }


def _cap(params: dict[str, Any], limit: int) -> dict[str, Any]:
    """按任务给 max_tokens 设上限，避免短任务也背着长文预算。

    Settings 里的 8192 是给「世界观 / 人物扩写」留的；剧情、记忆、字段回填
    只需要几百到一两千字。本地 RP 模型有时不肯主动收尾，会一路写到上限才停，
    于是每轮剧情都白等一两分钟。这里只收紧短任务，**不压缩世界 / 人物的长文**。
    """
    params["max_tokens"] = min(int(params.get("max_tokens", 8192)), limit)
    return params


def build_story_request(
    world_id: str,
    characters: dict[str, Any],
    schema,
    recent_history: list[dict[str, Any]],
    summary: str,
    player_action: str,
    current_location: str,
    relationships: dict[str, Any],
    turn: int,
    scene_description: str = "",
    introducing: list[str] | None = None,
    end_scene: bool = False,
) -> LLMRequest:
    """装配一轮剧情。characters 由 StateManager 从当前世界读出，键为角色 ID。"""
    # 预定事件是玩家排好的剧情任务清单：完成态属于运行态，从 current.json 读，
    # 这样读档回到过去的分支时，事件也会跟着回到「未完成」。
    current = file_utils.read_json(world_manager.resolve(world_id, "state", "current.json"), {}) or {}
    completed = current.get("completed_planned_events") or []
    planned = bible_service.collect_planned_events(world_id, list(characters.keys()), completed)
    clipped_history = [
        {**h, "narration": bible_service.clip(h.get("narration", ""), HISTORY_ENTRY_CHARS)}
        for h in recent_history
    ]
    # 每个角色的 bible 已各自裁剪，但 8 个角色叠起来仍可能上万字；这里再给一个
    # 全局总预算，保证「输入 + max_tokens」稳定落在模型服务的上下文窗口内。
    budget = BIBLE_TOTAL_FOR_STORY
    prompt_characters: dict[str, Any] = {}
    for cid, info in characters.items():
        text = str(info.get("bible_text") or "")
        take = min(len(text), max(0, budget))
        budget -= take
        clipped = text[:take] + ("…（其余设定略）" if len(text) > take else "")
        prompt_characters[cid] = {**info, "bible_text": clipped}
    # 剧情任务里要注入多份大 JSON。全部用紧凑序列化，并共享同一个字符总预算。
    # 字典顺序 = 优先级：玩家本轮输入、预定事件、字段定义最不能被砍；
    # 8 个角色的大 JSON 与历史放后面。每个占位符都有保底，不会被挤成空串。
    story_params = _cap(_generation_params(), 4096)

    def pack(value: Any) -> str:
        return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, (dict, list)) else str(value or ""))

    introducing_text = ""
    if introducing:
        introducing_text = "、".join(
            characters[c]["name"] if c in characters else c for c in introducing
        )

    # 玩家扮演的就是在场角色里 is_protagonist=True 的那一位，且只有这一位。
    # 每轮显式注入主语锚点，模型才不会把「玩家行动 / 四个选项」安到某个 NPC 头上。
    protagonist_line = ""
    for cid, info in characters.items():
        if info.get("is_protagonist"):
            protagonist_line = (
                f"{info.get('name') or cid}（角色 ID：{cid}）。"
                "玩家扮演的就是这位【主角】；四个选项 A/B/C/D，以及玩家在输入框里"
                "自己写的任何行动，全部是这位主角要做的动作。"
            )
            break
    if not protagonist_line:
        protagonist_line = (
            "（本轮在场角色里没有标记主角）玩家扮演的仍是故事的第一人称人物；"
            "四个选项与自定义输入都是这位人物要做的动作，不是任何 NPC 的动作。"
        )

    serialized: dict[str, str] = {
        "protagonist_line": pack(protagonist_line),
        "player_action": pack(bible_service.clip(player_action, USER_ACTION_CHARS)),
        "scene_description": pack(bible_service.clip(scene_description, USER_ACTION_CHARS)),
        "introducing": pack(introducing_text),
        # 收束轮只负责把这一幕收尾，绝不能把「预定事件清单」当成本轮要写的场景。
        # 之前收尾模板也带这份清单，模型便会把玩家排好的未来事件直接写成新场景。
        "planned_events": pack(
            "无（本轮是场景收束，不推进任何预定事件）"
            if end_scene else bible_service.planned_events_for_prompt(planned)
        ),
        "panel_schema_json": pack({cid: schema.to_prompt_list(cid, info["is_protagonist"])
                                   for cid, info in characters.items()}),
        "summary": pack(bible_service.clip(summary, SUMMARY_CHARS)),
        "relationships_json": pack(relationships),
        "recent_history": pack(clipped_history),
        "world_context": pack(world_context(world_id, current_location)),
        "character_fields_json": pack({cid: info["values"] for cid, info in characters.items()}),
        "characters_json": pack(prompt_characters),
    }
    template = _prepare_template("scene_end" if end_scene else "story_generation", list(serialized))
    room = max(len(serialized) * MIN_PIECE_CHARS, _task_chars(story_params["max_tokens"]) - len(template))
    task = render(template, _allocate(serialized, room), clip_template=False)
    context = (
        "字段类型说明（通用，不是本世界的字段清单）：\n"
        + describe_types()
        + "\n\n本轮只使用上下文里出现的人物与设定，不要引用其他世界的内容。"
    )
    # Mock 需要看到真实的字段定义才能按类型造演示值。
    mock_characters = {
        cid: {
            "name": info.get("name", cid),
            "is_protagonist": info.get("is_protagonist", False),
            "fields": [f.model_dump(mode="json") for f in schema.fields_for(cid, info["is_protagonist"])],
            "values": info.get("values", {}),
        }
        for cid, info in characters.items()
    }
    try:
        world_name = world_manager.get_meta(world_id).name
    except (OSError, ValueError, TypeError):
        world_name = world_id
    return LLMRequest(
        system_prompt=system_prompt_for(world_id),
        task_prompt=task,
        context=context,
        history=[{"role": "story", "content": str(h.get("narration", ""))[:400]} for h in recent_history[-4:]],
        user_action=bible_service.clip(player_action, USER_ACTION_CHARS),
        purpose="story",
        mock_context={
            "turn": turn,
            "characters": mock_characters,
            "world_name": world_name,
            "current_location": current_location,
        },
        **story_params,
    )


def build_world_request(user_description: str, genre_hint: str = "") -> LLMRequest:
    """把一句话扩写成完整世界观。此时世界还不存在，因此不读任何世界目录，天然不会串世界。"""
    params = _generation_params()
    # 世界观设定是长中文 JSON（setting 可到 1500 字），上限过小会被截成半个对象。
    # 这里按任务设下限，Settings 页调大的值仍然生效（取 max）。
    params["max_tokens"] = max(int(params.get("max_tokens", 8192)), 6144)
    safe_description = bible_service.clip(user_description, CUSTOMIZE_FIELD_CHARS)
    task = bounded_render("world_generator", {
        "user_description": safe_description,
        "genre_hint": bible_service.clip(genre_hint, 500),
    }, params["max_tokens"])
    return LLMRequest(
        # 世界还没创建，没有 world_id，因此只用全局铁律。
        system_prompt=system_prompt_for(None),
        task_prompt=task,
        context="本次任务是创建全新的独立世界，不要引用任何已有世界的设定或人物。",
        user_action=bible_service.clip(safe_description, USER_ACTION_CHARS),
        purpose="world",
        mock_context={"user_description": safe_description, "genre_hint": genre_hint},
        **params,
    )


def _customize_params() -> dict[str, Any]:
    """补全任务的长度预算。

    上下文窗口 -c 32768 是解锁长文的关键；但本地 RP 模型常常不肯主动收尾，
    一路写到 max_tokens 才停。按补全任务的实际需求（约 2500–4300 token）
    给 12288 已经两倍以上，再高只会白等几分钟并增加输出被截断的风险。
    """
    params = _cap(_generation_params(), 12288)
    params["max_tokens"] = max(int(params["max_tokens"]), 8192)
    return params


CUSTOMIZE_FIELD_CHARS = 4000
CUSTOMIZE_TOTAL_CHARS = 20000


def clip_customize_payload(value: Any) -> Any:
    """递归裁剪玩家表单：单个条目、总字数都有上限，防止把整本小说粘进来。"""
    budget = [CUSTOMIZE_TOTAL_CHARS]

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            allowed = min(CUSTOMIZE_FIELD_CHARS, max(0, budget[0]))
            out = node[:allowed]
            budget[0] -= len(out)
            return out + ("…（输入过长已截断）" if len(node) > allowed else "")
        if isinstance(node, list):
            return [walk(item) for item in node[:40]]
        if isinstance(node, dict):
            return {key: walk(item) for key, item in list(node.items())[:40]}
        return node

    return walk(value)


def build_world_customize_request(items: dict[str, Any], writer_style: str = "") -> LLMRequest:
    """玩家自己逐条填了世界草稿，一次调用把每个条目补写详实。"""
    params = _customize_params()
    safe_items = clip_customize_payload(items)
    safe_style = bible_service.clip(writer_style, WORLD_STYLE_CHARS)
    task = bounded_render("world_customize", {
        "world_items_json": safe_items,
        "writer_style": safe_style or "（未指定，按全局写作铁律行文）",
    }, params["max_tokens"])
    return LLMRequest(
        # 世界还没创建，没有世界专属写手；风格作为任务提示注入。
        system_prompt=system_prompt_for(None),
        task_prompt=task,
        context="本次任务是补全玩家已经写好的世界设定，玩家原话是最高优先级事实，不要另起炉灶重写一个世界。",
        user_action=bible_service.clip(
            str(safe_items.get("description") or safe_items.get("name") or "补全世界设定"),
            USER_ACTION_CHARS,
        ),
        purpose="customize_world",
        mock_context={"items": safe_items, "writer_style": safe_style},
        **params,
    )


def build_character_customize_request(
    world_id: str,
    inputs: dict[str, Any],
    field_definitions: list[dict[str, Any]],
    existing_characters: dict[str, Any],
) -> LLMRequest:
    """玩家自己逐条填了人物草稿，一次调用把每个条目补写详实。"""
    params = _customize_params()
    safe_inputs = clip_customize_payload(inputs)
    # 玩家逐条写的事实锚句排第一：预算不够时先砍背景资料，不能先砍玩家原话。
    task = bounded_render("character_customize", {
        "character_inputs_json": safe_inputs,
        "panel_schema_json": field_definitions,
        "existing_characters_json": existing_characters,
        "world_context": world_context(world_id),
    }, params["max_tokens"])
    return LLMRequest(
        system_prompt=system_prompt_for(world_id),
        task_prompt=task,
        context="字段类型说明（通用）：\n" + describe_types(),
        user_action=bible_service.clip(
            str(safe_inputs.get("name") or "补全人物设定"), USER_ACTION_CHARS,
        ),
        purpose="customize_character",
        mock_context={"inputs": safe_inputs},
        **params,
    )


def build_character_request(
    world_id: str,
    user_description: str,
    field_definitions: list[dict[str, Any]],
    is_protagonist: bool,
    count: int,
    protagonist_json: dict[str, Any] | None,
    auto_relations: bool,
) -> LLMRequest:
    name = "protagonist_generator" if is_protagonist else "npc_generator"
    # 人物卡要同时写完整设定、剧情钩子和所有自定义字段，短上限会把 JSON 截断。
    # 批量生成（最多 8 人）时更长，因此下限比单角色再放宽。
    character_params = _generation_params()
    floor = 4096 if count <= 1 else 6144
    character_params["max_tokens"] = max(int(character_params.get("max_tokens", 8192)), floor)
    safe_description = bible_service.clip(user_description, CUSTOMIZE_FIELD_CHARS)
    # 玩家核心描述与字段定义优先；世界背景、主角快照放后面（可压缩）。
    values: dict[str, Any] = {
        "user_description": safe_description,
        "panel_schema_json": field_definitions,
    }
    if not is_protagonist:
        values.update({
            "protagonist_json": protagonist_json or {},
            "generation_request": {"count": count, "auto_relations": auto_relations},
        })
    values["world_context"] = world_context(world_id)
    task = bounded_render(name, values, character_params["max_tokens"])
    return LLMRequest(
        system_prompt=system_prompt_for(world_id),
        task_prompt=task,
        context="字段类型说明（通用）：\n" + describe_types(),
        user_action=bible_service.clip(safe_description, USER_ACTION_CHARS),
        purpose="protagonist" if is_protagonist else "npc",
        mock_context={"count": count, "field_definitions": field_definitions},
        **character_params,
    )


def build_memory_request(
    world_id: str,
    summary: str,
    timeline_tail: list[dict[str, Any]],
    narration: str,
    player_action: str,
    memory_candidates: list[str],
) -> LLMRequest:
    cfg = settings_service.load_settings()
    params = _cap(_generation_params(), 1536)
    task = bounded_render("memory_extractor", {
        "memory_candidates": memory_candidates,
        "turn_narration": bible_service.clip(narration, 4000),
        "player_action": bible_service.clip(player_action, USER_ACTION_CHARS),
        "summary": bible_service.clip(summary, SUMMARY_CHARS),
        "timeline_tail": timeline_tail,
        "summary_max_chars": (cfg.get("memory") or {}).get("summary_max_chars", 4000),
    }, params["max_tokens"])
    return LLMRequest(
        system_prompt=system_prompt_for(world_id),
        task_prompt=task,
        user_action=bible_service.clip(player_action, USER_ACTION_CHARS),
        purpose="memory",
        mock_context={"summary": summary, "memory_candidates": memory_candidates,
                      "turn": len(timeline_tail) + 1},
        **params,
    )


def _slim_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """回填 / 校准是短任务（max_tokens 2048）：只带结构性人物信息。

    bible 可能上千字，塞进去既撑爆预算又会用固定设定干扰「此刻状态」的判断；
    剧情回合才会通过 characters_for_prompt 注入精简后的设定基线。
    """
    return {
        "name": profile.get("name", ""),
        "core_description": profile.get("core_description", ""),
        "is_protagonist": bool(profile.get("is_protagonist", False)),
    }


def build_backfill_request(
    world_id: str,
    profile: dict[str, Any],
    existing_fields: dict[str, Any],
    new_fields: list[dict[str, Any]],
    summary: str,
    timeline_events: list[dict[str, Any]],
    recent_history: list[dict[str, Any]],
    current_context: str,
    turn: int,
) -> LLMRequest:
    params = _cap(_generation_params(), 2048)
    task = bounded_render("field_backfill", {
        # 这次要回填的字段清单是第一优先，模型不知道它就无法作答。
        "new_fields_json": new_fields,
        "existing_fields_json": existing_fields,
        "character_profile_json": _slim_profile(profile),
        "current_context": bible_service.clip(current_context, SUMMARY_CHARS),
        "summary": bible_service.clip(summary, SUMMARY_CHARS),
        "timeline_events": timeline_events,
        "recent_history": recent_history,
        "world_context": world_context(world_id),
    }, params["max_tokens"])
    return LLMRequest(
        system_prompt=system_prompt_for(world_id),
        task_prompt=task,
        purpose="backfill",
        mock_context={"new_fields": new_fields, "turn": turn},
        **params,
    )


def build_reconcile_request(
    world_id: str,
    profile: dict[str, Any],
    field_definitions: list[dict[str, Any]],
    current_fields: dict[str, Any],
    summary: str,
    recent_history: list[dict[str, Any]],
    current_context: str,
) -> LLMRequest:
    params = _cap(_generation_params(), 2048)
    task = bounded_render("field_reconcile", {
        "panel_schema_json": field_definitions,
        "current_fields_json": current_fields,
        "character_profile_json": _slim_profile(profile),
        "current_context": bible_service.clip(current_context, SUMMARY_CHARS),
        "summary": bible_service.clip(summary, SUMMARY_CHARS),
        "recent_history": recent_history,
        "world_context": world_context(world_id),
    }, params["max_tokens"])
    return LLMRequest(
        system_prompt=system_prompt_for(world_id),
        task_prompt=task,
        purpose="reconcile",
        mock_context={},
        **params,
    )

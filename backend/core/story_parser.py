"""模型输出解析：多层容错。

解析顺序（越靠后越宽容）：
1. 直接按 JSON 解析；
2. 剥掉 <think> 后再试；
3. 从 Markdown 代码块里抽 JSON；
4. 取「第一个 { 到最后一个 }」；
5. 字符串级修复：字符串内的真实换行、裸 ASCII 双引号、非法转义；
6. 截断修复：输出被 max_tokens 砍断时补齐引号与括号，尽量保住已生成的正文；
7. 仍失败时由 StoryEngine 发起一次「格式修复」请求，再失败则展示原文 + 安全兜底。

第 5/6 层是本地模型（尤其长中文设定、角色扮演微调）最常见的两类毛病：
模型会在 JSON 字符串里直接按回车分段，或者写到一半 token 用完。
任何一层成功都会返回说明文字，供上层作为 warning 显示给玩家，绝不假装"格式完美"。
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.models.story import TurnOutput

CODE_BLOCK = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")
THINK_BLOCK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
THINK_TAG = re.compile(r"</?think>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    """部分推理模型会在 JSON 前吐一段思考过程，先剥掉避免污染首尾截取。"""
    return THINK_TAG.sub("", THINK_BLOCK.sub("", text)).strip()


def repair_json_string(text: str) -> tuple[str, list[str]]:
    """把本地模型常见的 JSON 毛病修成合法 JSON 文本，返回 (修复后文本, 修复项说明)。

    只改动「字符串字面量内部」的字符，不改动 JSON 结构本身。
    逐字符扫描并维护 in_string / 括号栈，因此能区分结构里的引号和正文里的引号。
    """
    start = text.find("{")
    if start < 0:
        return text, []

    body = text[start:]
    out: list[str] = []
    stack: list[str] = []
    in_string = False
    n = len(body)
    i = 0
    counts = {"newline": 0, "quote": 0, "backslash": 0}

    while i < n:
        ch = body[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]" and stack:
                stack.pop()
            i += 1
            continue

        # ---- 以下都在字符串字面量内部 ----
        if ch == "\\":
            nxt = body[i + 1] if i + 1 < n else ""
            if nxt in '"\\/bfnrtu':
                out.append(ch)
                out.append(nxt)
                i += 2
            else:
                # Windows 路径、Markdown 转义等裸反斜杠不是合法 JSON 转义。
                out.append("\\\\")
                counts["backslash"] += 1
                i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and body[j] in " \t\r\n":
                j += 1
            if j >= n or body[j] in ",:}]":
                out.append('"')
                in_string = False
            else:
                # 正文里引用的引号：换成人读得懂的中文右引号，不破坏结构。
                out.append("\u201d")
                counts["quote"] += 1
            i += 1
            continue
        if ch == "\n":
            out.append("\\n")
            counts["newline"] += 1
            i += 1
            continue
        if ch == "\r":
            i += 1
            continue
        if ch == "\t":
            out.append("\\t")
            counts["newline"] += 1
            i += 1
            continue
        if ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
            counts["newline"] += 1
            i += 1
            continue
        out.append(ch)
        i += 1

    notes: list[str] = []
    truncated = False
    if in_string:
        # 输出被砍断在字符串中间：先闭合这个字符串，保住已经写出来的正文。
        out.append('"')
        truncated = True
    while out and out[-1] in " \t\r\n":
        out.pop()
    if out and out[-1] == ":":
        # 砍在 "键": 后面，值还没写：补 null，让前面所有字段仍然可读。
        out.append("null")
        truncated = True
    elif out and out[-1] == ",":
        out.pop()
        truncated = True

    if stack:
        truncated = True
    for opener in reversed(stack):
        out.append("}" if opener == "{" else "]")

    if counts["newline"]:
        notes.append(f"字符串内 {counts['newline']} 处真实换行已转成 \\n")
    if counts["quote"]:
        notes.append(f"字符串内 {counts['quote']} 处多余双引号已转成中文引号")
    if counts["backslash"]:
        notes.append(f"{counts['backslash']} 处非法反斜杠已转义")
    if truncated:
        notes.append("输出疑似被长度上限截断，已补齐结尾；缺失字段由后端用安全默认值补齐，建议调高 Max Tokens")
    return "".join(out), notes


def _try_loads(candidate: str) -> dict[str, Any] | None:
    """尝试解析成 JSON 对象。

    本地模型偶尔把多人物直接返回成顶层数组；这里包装成 {"characters": [...]}，
    由 CharacterGenerator 统一收敛。剧情/世界生成不会输出顶层数组，包装不会误伤。
    """
    if not candidate or not candidate.strip():
        return None
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"characters": data}
    return None


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str]:
    """尽力把模型原文解析成 JSON 对象，返回 (结果, 说明)。

    说明在完全合法时为空字符串；经过修复才成功时是一段可读文案，调用方应作为
    warning 展示，避免把「降级修复过的内容」伪装成完美结果。
    """
    if not text or not text.strip():
        return None, "模型返回为空"
    cleaned = text.strip().lstrip("\ufeff")
    cleaned = _strip_think(cleaned)

    # 1) 完全合法
    if data := _try_loads(cleaned):
        return data, ""

    # 2) Markdown 代码块
    for match in CODE_BLOCK.finditer(cleaned):
        if data := _try_loads(match.group(1).strip()):
            return data, ""

    # 3) 首尾截取（容忍前后寒暄），同时留下截断样本给修复层。
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    sliced = cleaned[first:last + 1] if first != -1 and last > first else ""
    if sliced and (data := _try_loads(sliced)):
        return data, ""

    # 4) 字符串级修复 + 截断补齐。没有右大括号时用「第一个 { 到文末」。
    raw = sliced if sliced else (cleaned[first:] if first != -1 else cleaned)
    repaired, notes = repair_json_string(raw)
    if data := _try_loads(repaired):
        return data, "模型输出格式已自动修复（" + "；".join(notes) + "）" if notes else ""

    # 5) 修复后仍不合法：可能是多补了括号，逐个回退尾部括号再试。
    for _ in range(4):
        if repaired.endswith(("}", "]")):
            repaired = repaired[:-1].rstrip().rstrip(",")
            if data := _try_loads(repaired):
                return data, "模型输出格式已自动修复（截断尾部已裁剪）"
    return None, "模型输出不是合法 JSON，且无法通过格式修复提取出对象"


def parse_turn_output(text: str) -> tuple[TurnOutput | None, str]:
    """把模型原文解析成 TurnOutput。结构不对的字段由 Pydantic 忽略，不抛异常。

    第二个返回值为解析说明（含自动修复提示），调用方应写入 parse_warning 展示。
    """
    data, reason = parse_json_object(text)
    if data is None:
        return None, reason
    try:
        output = TurnOutput(**data)
    except (TypeError, ValueError) as exc:
        return None, f"JSON 结构无法转成剧情输出：{exc}"
    if reason:
        output.parse_warning = (output.parse_warning or "") + ("｜" if output.parse_warning else "") + reason
    return output, reason


def safe_fallback(raw_text: str, reason: str, fallbacks: list[str]) -> TurnOutput:
    """解析彻底失败时的安全结果：正文展示原文，四个后备选项，零状态更新。"""
    readable = raw_text.strip() or "（模型没有返回任何内容）"
    from backend.models.story import Choice
    choices = [
        Choice(id=letter, text=fallbacks[i % len(fallbacks)] if fallbacks else "继续观察，暂不行动")
        for i, letter in enumerate("ABCD")
    ]
    return TurnOutput(
        narration=f"模型这次没有按约定格式返回。以下是原始输出，本轮剧情未推进，你的行动已经保存：\n\n{readable}",
        choices=choices,
        parse_warning=reason,
        raw_text=readable,
    )

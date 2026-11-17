"""Model Provider 统一接口。

业务层只认识 LLMRequest / GenerationResult，不认识任何具体模型 SDK。
- OpenAICompatProvider：兼容 OpenAI Chat Completions，可接 KoboldCpp / LM Studio / Ollama。
- MockProvider：不联网也能跑完整流程，按当前世界 Schema 生成合理的演示数据。

API Key 只在本模块内部使用，绝不写进日志、异常信息或 GenerationResult。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass
class LLMRequest:
    """一次模型调用的全部输入。"""

    system_prompt: str
    task_prompt: str
    context: str = ""
    history: list[dict[str, str]] = dc_field(default_factory=list)
    user_action: str = ""
    temperature: float = 0.85
    max_tokens: int = 1600
    timeout_seconds: float = 120
    # story | npc | protagonist | world | memory | backfill | reconcile | repair
    purpose: str = "story"
    mock_context: dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class GenerationResult:
    """一次模型调用的全部输出：原文、用量、错误。error 非空即代表失败。"""

    text: str = ""
    usage: dict[str, Any] | None = None
    error: str | None = None
    provider: str = "mock"
    model: str = ""


# llama.cpp 的 -c 是「输入 + 输出」共用的总窗口，与模型服务的 -c 参数保持一致（见 README 的模型服务章节）。
# 中文约 1 字 1 token、JSON/ASCII 更省，因此按 1 字符 >= 1 token 做保守估算：
# 只要字符数守住下面这个额度，实际 token 数一定不会超窗口。
CONTEXT_WINDOW_TOKENS = 32768
WINDOW_SAFETY_TOKENS = 512
# 输出最多占窗口的一半：另一半必须留给 prompt，否则全局铁律会被左截断。
# 与 settings_service.MAX_TOKENS_CEILING 保持一致，且在这里再兜一次底，
# 防止有调用方绕过 settings 直接构造 LLMRequest。
MAX_OUTPUT_TOKENS = CONTEXT_WINDOW_TOKENS // 2
_TRIM_MARK = "…（上下文预算不足，已裁掉次要内容；基础写作铁律与本轮输入保留）"


def clamp_output_tokens(value: Any) -> int:
    """真正发给模型的 max_tokens。所有路径都要过这里，窗口保证才成立。"""
    return max(64, min(int(value or 0), MAX_OUTPUT_TOKENS))


def effective_max_tokens(request: LLMRequest) -> int:
    return clamp_output_tokens(request.max_tokens)


def max_prompt_chars(max_tokens: int) -> int:
    """这一次请求允许发给模型的 prompt 字符总数（保守上界）。"""
    return max(2048, CONTEXT_WINDOW_TOKENS - clamp_output_tokens(max_tokens) - WINDOW_SAFETY_TOKENS)


def build_messages(request: LLMRequest) -> list[dict[str, str]]:
    """按真实请求格式拼装消息，并在最后一道关卡保证不撑爆上下文窗口。

    裁剪优先级（从最不重要到最重要）：
    1. user 消息里的 context / 早期 history —— 从**头部**裁，保留最近的历史；
    2. 任务正文中段 —— 保住模板开头与「必须输出 JSON」的格式红线（尾）；
    3. system_prompt 尾部 —— 全局基础铁律在最前面，永远不动。

    **本轮输入**与末尾的 JSON 格式指令拼在 user 消息的尾部，任何情况下都不裁。
    """
    head = request.system_prompt.strip()
    task = request.task_prompt

    # 可裁部分：context + history（越靠前越旧、越次要）
    droppable: list[str] = []
    if request.context:
        droppable.append(request.context)
    for item in request.history[-12:]:
        droppable.append(f"[{item.get('role', 'user')}] {item.get('content', '')}")
    # 必须保留的部分：本轮输入 + 输出格式指令
    must_keep: list[str] = []
    if request.user_action:
        must_keep.append(f"[本轮输入] {request.user_action}")
    must_keep.append("请严格按任务要求输出 JSON，不要输出解释文字。")
    tail = "\n\n".join(must_keep)
    optional = "\n\n".join(p for p in droppable if p)

    budget = max_prompt_chars(request.max_tokens)
    separator = "\n\n---\n\n"

    def total(opt: str, t: str, h: str) -> int:
        return len(h) + len(separator) + len(t) + (len(opt) + 2 + len(tail) if opt else len(tail))

    user = f"{optional}\n\n{tail}" if optional else tail
    # 第一刀：只裁 context / history 的头部，tail 一个字都不动。
    if total(optional, task, head) > budget:
        allowed = budget - len(head) - len(separator) - len(task) - 2 - len(tail) - len(_TRIM_MARK)
        if allowed > 0:
            if len(optional) > allowed:
                optional = _TRIM_MARK + optional[-allowed:]
            user = f"{optional}\n\n{tail}"
        else:
            user = tail
    else:
        user = f"{optional}\n\n{tail}" if optional else tail

    # 第二刀：仍超额就裁任务正文中段，保住模板头与格式红线尾。
    if len(head) + len(separator) + len(task) + len(user) > budget:
        task_room = budget - len(head) - len(separator) - len(user) - len(_TRIM_MARK)
        if task_room > 0 and len(task) > task_room:
            keep_back = max(1, min(task_room - 1, 1200))
            keep_front = max(0, task_room - keep_back)
            task = task[:keep_front] + _TRIM_MARK + task[-keep_back:]
        else:
            task = task[:max(0, task_room)].rstrip()
    # 第三刀：极端情况（全局 Prompt 被写到几万字）从 system 尾部裁，铁律仍在开头。
    if len(head) + len(separator) + len(task) + len(user) > budget:
        head_room = max(1, budget - len(separator) - len(task) - len(user) - len(_TRIM_MARK))
        if len(head) > head_room:
            head = head[:head_room].rstrip() + _TRIM_MARK
    system = f"{head}{separator}{task}".strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def prompt_chars(request: LLMRequest) -> int:
    """真正发给模型的 prompt 字符数（含拼装开销），供窗口预算测试用。"""
    return sum(len(m["content"]) for m in build_messages(request))


class BaseProvider:
    name = "base"

    def generate(self, request: LLMRequest) -> GenerationResult:  # pragma: no cover
        raise NotImplementedError

    def probe(self) -> GenerationResult:  # pragma: no cover
        raise NotImplementedError


class OpenAICompatProvider(BaseProvider):
    """OpenAI Chat Completions 兼容接口。"""

    name = "openai_compatible"

    def __init__(self, config: dict[str, Any]):
        self.base_url = str(config.get("base_url") or "http://127.0.0.1:5001/v1").rstrip("/")
        self.model = str(config.get("model") or "local-model")
        self.api_key = str(config.get("api_key") or "")
        self.timeout = float(config.get("timeout_seconds") or 120)
        # 本机服务不要走系统代理。Windows 上常见 HTTP_PROXY 有值而 NO_PROXY 为空，
        # httpx 会把发往 127.0.0.1 的请求也交给代理；非流式长请求会被代理缓冲，
        # 甚至比服务端更早撞上代理自己的超时。远程地址仍保留代理能力。
        host = (urlparse(self.base_url).hostname or "").lower()
        self.trust_env = host not in {"127.0.0.1", "localhost", "::1"}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _messages(self, request: LLMRequest) -> list[dict[str, str]]:
        # System Prompt 与任务 Prompt 每轮都从文件重读；拼装与窗口保护统一走模块级函数。
        return build_messages(request)

    def _post(self, request: LLMRequest, payload_extra: dict[str, Any] | None = None) -> GenerationResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(request),
            "temperature": request.temperature,
            # 与 build_messages 用同一个夹取值：不能一边按 16384 留 prompt 额度、
            # 一边又真的让模型输出 32768，那窗口保证就形同虚设。
            "max_tokens": effective_max_tokens(request),
            "stream": False,
        }
        if payload_extra:
            payload.update(payload_extra)
        url = f"{self.base_url}/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout, trust_env=self.trust_env) as client:
                response = client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError:
            return GenerationResult(
                error=f"无法连接模型服务（{self.base_url}）。请确认本地推理服务已启动、Base URL 填写正确，或开启 Mock Mode。",
                provider=self.name, model=self.model,
            )
        except httpx.TimeoutException:
            return GenerationResult(
                error=(f"模型请求超时（{self.timeout:.0f} 秒）。若每次都很慢，请到设置页点 "
                       "Test Connection 看实测速度：低于 15 token/秒通常是模型服务没启用 GPU、"
                       "退回纯 CPU 了；也可临时调大请求超时。"),
                provider=self.name, model=self.model,
            )
        except httpx.HTTPStatusError as exc:
            # 只回状态码与简短原因，不回显请求头，避免密钥泄漏。
            return GenerationResult(
                error=f"模型服务返回 HTTP {exc.response.status_code}。常见原因：模型名不存在、接口未开启 OpenAI 兼容模式、API Key 不正确。",
                provider=self.name, model=self.model,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return GenerationResult(
                error=f"模型服务响应异常：{type(exc).__name__}。请检查服务日志与接口格式。",
                provider=self.name, model=self.model,
            )

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return GenerationResult(
                error="模型服务返回的 JSON 缺少 choices[0].message.content，可能不是 Chat Completions 兼容接口。",
                provider=self.name, model=self.model,
            )
        # llama.cpp 会在 timings 里回报真实生成速度，留着给设置页做「是不是掉回 CPU」的诊断。
        usage = dict(data.get("usage") or {})
        timings = data.get("timings")
        if isinstance(timings, dict):
            usage["timings"] = {
                key: timings.get(key)
                for key in ("predicted_n", "predicted_ms", "predicted_per_second",
                            "prompt_n", "prompt_ms", "prompt_per_second")
            }
        return GenerationResult(
            text=text, usage=usage, provider=self.name, model=self.model,
        )

    def generate(self, request: LLMRequest) -> GenerationResult:
        return self._post(request)

    def probe(self) -> GenerationResult:
        """一次短请求同时判断可用性与实际生成速度（2 个 token 测不出速度）。"""
        request = LLMRequest(
            system_prompt="You are a test endpoint.",
            task_prompt="请连续输出数字 1 到 30，用空格隔开，不要输出其它内容。",
            max_tokens=96,
            temperature=0.0,
            timeout_seconds=min(self.timeout, 30),
            purpose="repair",
        )
        return self._post(request)


def _mock_value(definition: dict[str, Any], turn: int, label: str) -> Any:
    """按字段类型造一个「和剧情有关」的演示值，而不是固定占位符。"""
    kind = definition.get("type", "text")
    options = definition.get("options") or []
    if kind in ("text", "long_text"):
        return f"第 {turn} 轮之后，{label}有了新的变化：与刚才那句话有关，但还没有说透。"
    if kind == "number":
        return turn
    if kind == "boolean":
        return turn % 2 == 0
    if kind == "enum":
        return options[turn % len(options)] if options else ""
    if kind == "list":
        return ["随身旧物", f"第 {turn} 轮记下的线索"]
    if kind == "key_value":
        return {"最近的线索": f"来自第 {turn} 轮的对话"}
    return ""


class MockProvider(BaseProvider):
    """不联网的完整演示实现。所有字段值都从当前世界 Schema 动态生成。"""

    name = "mock"

    def generate(self, request: LLMRequest) -> GenerationResult:
        handler = {
            "story": self._story,
            "npc": self._characters,
            "protagonist": self._characters,
            "world": self._world,
            "memory": self._memory,
            "backfill": self._backfill,
            "reconcile": self._reconcile,
            "repair": self._repair,
            # 自定义补全的两个 purpose：不注册会静默落到 _story，返回剧情 JSON 导致解析失败。
            "customize_world": self._customize_world,
            "customize_character": self._customize_character,
        }.get(request.purpose, self._story)
        text = json.dumps(handler(request), ensure_ascii=False, indent=2)
        return GenerationResult(text=text, usage={"mock": True}, provider=self.name, model="mock-model")

    def probe(self) -> GenerationResult:
        return GenerationResult(
            error="当前是 Mock 模拟模式，没有连接真实模型。关闭 Mock Mode 后才能测试真实服务。",
            provider=self.name, model="mock-model",
        )

    def _story(self, request: LLMRequest) -> dict[str, Any]:
        ctx = request.mock_context
        turn = int(ctx.get("turn", 1))
        action = request.user_action or "（玩家尚未输入行动）"
        characters: dict[str, Any] = ctx.get("characters") or {}
        npc_ids = [cid for cid, info in characters.items() if not info.get("is_protagonist")]
        main_npc = npc_ids[0] if npc_ids else ""
        npc_name = characters.get(main_npc, {}).get("name", "对方")
        world_name = str(ctx.get("world_name") or "这个世界")
        place = str(ctx.get("current_location") or world_name)

        # 演示文本按当前世界与玩家行动生成，不复用任何 Demo 世界的固定桥段，
        # 避免新世界看起来像在播放「雾港旧事」的存档。
        narration = (
            f"（Mock 演示 · {world_name}）\n"
            f"{place}。你把「{action}」这个念头落下，四周静了一瞬，"
            f"光线挪过半寸，有什么细节正等着被看清。\n"
            f"{npc_name}抬起眼，没有立刻开口，只把手边那样东西往你这边推了推。"
            f"「你来得比我想的早。」话尾留着没说完的半句。\n"
            f"第 {turn} 轮的这一刻，事情还没有定论——接下来做什么，由你决定。"
        )

        # 只挑少量 story_driven 字段更新，绝不每轮重填全部字段。
        updates: list[dict[str, Any]] = []
        for cid, info in characters.items():
            definitions = [d for d in info.get("fields", [])
                           if d.get("update_policy") == "story_driven"][:2]
            values = {
                d["id"]: _mock_value(d, turn, d.get("label", d["id"]))
                for d in definitions
            }
            if values:
                updates.append({"character_id": cid, "custom_fields": values})

        output: dict[str, Any] = {
            "narration": narration,
            "dialogues": [{"speaker_id": main_npc, "text": "你来得比我想的早。"}] if main_npc else [],
            "choices": [
                {"id": "A", "text": f"直接问{npc_name}刚才那句没说完的话"},
                {"id": "B", "text": f"仔细看看{place}里还有什么别人"},
                {"id": "C", "text": "先不表态，把手边那样东西拿起来看看"},
                {"id": "D", "text": "换个话题，试探对方真正关心什么"},
            ],
            "character_updates": updates,
            "memory_candidates": [f"第 {turn} 轮：玩家「{action}」；{npc_name}留下一句没说完的话"],
            "schema_suggestions": [],
        }

        # 每 5 轮演示一次「LLM 想新增字段」，用来验收非阻塞建议机制。
        all_ids = {d["id"] for info in characters.values() for d in info.get("fields", [])}
        if turn % 5 == 0 and "mood_towards_player" not in all_ids:
            output["schema_suggestions"].append({
                "field_id": "mood_towards_player",
                "label": "对主角的真实态度",
                "type": "text",
                "reason": "这段关系反复出现口是心非的描写，值得单独记一项",
                "example": "戒备里带一点好奇",
            })
        return output

    def _characters(self, request: LLMRequest) -> dict[str, Any]:
        ctx = request.mock_context
        count = max(1, int(ctx.get("count", 1)))
        is_proto = request.purpose == "protagonist"
        definitions: list[dict[str, Any]] = ctx.get("field_definitions") or []
        desc = request.user_action or "一个身份复杂、话不多的成年人"
        names = ["沈知微", "陆砚", "顾晚棠", "程叙", "林见夏", "谢珩"]
        result = []
        for i in range(count):
            values = {d["id"]: _mock_value(d, i + 1, d.get("label", d["id"])) for d in definitions}
            result.append({
                "name": names[i % len(names)],
                "core_description": f"{desc}。由 Mock Provider 依当前世界字段补齐，共 {len(values)} 项。",
                "custom_fields": values,
                "plot_hooks": ["袖口有一道洗不掉的旧墨痕，从不解释来历"],
                "opening_situation": "刚在雨里抵达旧宅门口，手里攥着一封没拆的信。",
            })
        if is_proto:
            only = result[0]
            return {"name": only["name"], "core_description": only["core_description"],
                    "custom_fields": only["custom_fields"],
                    "opening_situation": only["opening_situation"]}
        return {"characters": result, "relationships_between_generated": []}

    def _world(self, request: LLMRequest) -> dict[str, Any]:
        """Mock 下也给一份真正「扩写过」的世界观预览，方便无模型时验收流程。"""
        desc = request.user_action or "一个尚未命名的故事"
        genre = str(request.mock_context.get("genre_hint") or "当代 · 群像")
        short = desc[:14]
        return {
            "name": f"{short}…" if len(desc) > 14 else short,
            "genre": genre,
            "description": f"以「{desc}」为起点展开的故事。人物关系与日常细节会随剧情逐步显形。",
            "setting": (
                f"## 时代与地理\n\n故事发生在由「{desc}」所指向的环境中。时间设定为当代，"
                "主要活动范围是一处相对封闭、人物会反复相遇的场所：走廊、教室、办公区或住处彼此连通，"
                "天气与光线用来标记时间的推移，而不承担剧情机关。\n\n"
                "## 主要场所\n\n- 主场景：人物每天都会经过、因此最容易发生偶遇的地方。\n"
                "- 次级场景：适合私下谈话、能避开旁人耳目的角落。\n"
                "- 边界场景：离开主场景才会去的地方，往往牵出外部势力。\n\n"
                "## 势力与关系网\n\n人物分属几个互相有往来的小圈子，彼此既有合作也有未说清的旧账。"
                "表面的秩序由不成文的规矩维持，真正的张力藏在没有被当面提起的那件事里。\n\n"
                "## 日常生活质感\n\n写清作息、通勤、吃饭、课业或工作里重复出现的小动作，"
                "让对话有落点；避免用抽象形容词概括气氛。\n\n"
                "## 核心矛盾\n\n一桩过去发生的、至今没有被正面解释的事件，正通过当下几次偶遇重新浮出水面。"
            ),
            "rules": (
                "## 不可违背的规则\n\n- 所有出场人物都是明确的成年人，人物关系的变化必须由具体事件推动。\n"
                "- 玩家角色的行动、台词与想法只能由玩家决定，叙述者不得代做决定。\n"
                "- 已经写进人物卡的固定设定不因普通剧情随意改变；只有随剧情变化的当前状态才允许更新。\n\n"
                "## 叙事一致性\n\n时间线只向前推进；地点、称呼、已发生的事件前后必须一致。"
                "不引入突然出现的超自然能力，除非用户原话里明确要求。\n\n"
                "## 禁忌\n\n不写旁白替玩家解释动机，不用一句「总而言之」跳过多轮积累的情绪。"
            ),
            "opening_location": "主场景入口",
            "opening_scene": (
                "（Mock 演示开场）门还半掩着，里面的人声压得很低，像在等谁先开口。"
                "你站在门槛这一侧，能看清桌面上摊着的东西，也看得见抬眼看你的那个人。"
                "有一句话显然已经准备好了，却还没轮到它出场。接下来走向哪里，由你决定。"
            ),
            "opening_choices": [
                {"id": "A", "text": "推门进去，先向在场的人打招呼"},
                {"id": "B", "text": "停在门外，把那句话听完再说"},
                {"id": "C", "text": "先看桌面上的东西，再决定要不要进去"},
                {"id": "D", "text": "转身离开，等一个更合适的时机"},
            ],
            "protagonist_hint": f"一位与「{desc}」直接相关、但仍有秘密没有说出口的成年人",
            "npc_hints": ["一位表面冷淡、其实一直在留意主角的成年人",
                          "一位掌握关键信息却不愿主动挑明的成年人"],
        }

    def _customize_world(self, request: LLMRequest) -> dict[str, Any]:
        """Mock 补全：保留玩家原话并追加一段演示扩写，保证锚句校验不误报。"""
        items = request.mock_context.get("items") or {}

        def expanded(key: str, label: str, fallback: str = "") -> str:
            own = str(items.get(key) or "").strip()
            head = own or fallback or f"（Mock 补全）{label}由演示数据生成，可在这里改写。"
            return (
                f"{head}\n\n（Mock 演示扩写）{label}：把可看见的细节、具体名词与来龙去脉写足；"
                "真实模型会在这里补出完整设定，玩家可逐项精修后保存。"
            )

        raw_choices = items.get("opening_choices")
        choices = []
        for index, letter in enumerate("ABCD"):
            own_text = ""
            if isinstance(raw_choices, list) and index < len(raw_choices):
                item = raw_choices[index]
                own_text = str(item.get("text") if isinstance(item, dict) else item or "").strip()
            choices.append({"id": letter, "text": own_text or f"（Mock 补全）具体行动 {letter}"})
        hints = items.get("npc_hints")
        return {
            "name": expanded("name", "世界名"),
            "genre": expanded("genre", "类型基调"),
            "description": expanded("description", "简介"),
            "setting": expanded("setting", "世界观背景"),
            "rules": expanded("rules", "世界规则"),
            "opening_location": expanded("opening_location", "开场地点"),
            "opening_scene": expanded("opening_scene", "开场画面"),
            "opening_choices": choices,
            "protagonist_hint": expanded("protagonist_hint", "主角方向"),
            "npc_hints": hints if isinstance(hints, list) and hints else ["（Mock 补全）一位与主角有旧账的成年人"],
        }

    def _customize_character(self, request: LLMRequest) -> dict[str, Any]:
        """Mock 补全：每个条目都以玩家原话开头，再追加演示扩写。"""
        inputs = request.mock_context.get("inputs") or {}
        own_items = inputs.get("items") if isinstance(inputs.get("items"), dict) else {}
        labels = {
            "appearance": "外貌", "personality": "性格",
            "background": "背景", "major_events": "重大事件",
        }
        items = {}
        for key, label in labels.items():
            own = own_items.get(key)
            anchor = ""
            if isinstance(own, dict):
                anchor = str(own.get("player_input") or own.get("text") or "").strip()
            elif isinstance(own, str):
                anchor = own.strip()
            head = anchor or f"（Mock 补全）{label}由演示数据生成，可在这里改写。"
            items[key] = {"text": f"{head}\n\n（Mock 演示扩写）{label}：补足具体细节、行为来处与可写进正文的抓手。"}

        def rel_text(raw: Any, label: str) -> dict[str, str]:
            anchor = ""
            if isinstance(raw, dict):
                anchor = str(raw.get("player_input") or raw.get("text") or "").strip()
            elif isinstance(raw, str):
                anchor = raw.strip()
            head = anchor or f"（Mock 补全）{label}由演示数据生成。"
            return {"text": f"{head}\n\n（Mock 演示扩写）{label}：把时间、地点、在场者与后果写具体。"}

        relations = []
        for card in inputs.get("relations") or []:
            if not isinstance(card, dict):
                continue
            past = card.get("past_events") if isinstance(card.get("past_events"), list) else []
            relations.append({
                "target_character_id": str(card.get("target_character_id") or ""),
                "target_name": str(card.get("target_name") or ""),
                "relation": rel_text(card.get("relation"), "两人关系"),
                "past_events": [rel_text(item, "已发生的关键事件") for item in past],
                # 预定事件由程序从玩家表单原样保留，Mock 也不输出这个键。
            })
        return {
            "name": str(inputs.get("name") or "（Mock 补全）未命名角色"),
            "core_description": str(inputs.get("core_description") or "（Mock 补全）一句话人物定位"),
            "items": items,
            "relations": relations,
        }

    def _memory(self, request: LLMRequest) -> dict[str, Any]:
        ctx = request.mock_context
        candidates = ctx.get("memory_candidates") or []
        return {
            "timeline_events": [{"text": str(c), "importance": "normal"} for c in candidates[:2]],
            "summary": (ctx.get("summary") or "故事刚开始。") + f"\n第 {ctx.get('turn', 1)} 轮：{request.user_action}",
            "ignored_candidates": [],
        }

    def _backfill(self, request: LLMRequest) -> dict[str, Any]:
        definitions: list[dict[str, Any]] = request.mock_context.get("new_fields") or []
        values: dict[str, Any] = {}
        reasons: dict[str, str] = {}
        for d in definitions:
            values[d["id"]] = _mock_value(d, int(request.mock_context.get("turn", 1)), d.get("label", d["id"]))
            reasons[d["id"]] = "依据最近若干轮互动与剧情摘要推断（Mock 演示）"
        return {"values": values, "reasons": reasons}

    def _reconcile(self, request: LLMRequest) -> dict[str, Any]:
        # 校准的正确默认答案就是「无需修正」，Mock 也照此演示。
        return {"corrections": []}

    def _repair(self, request: LLMRequest) -> dict[str, Any]:
        return {
            "narration": "（模型输出格式有误，已由后端修复为可继续的结构。）在场的人都暂时没有说话，这一刻先留白。",
            "dialogues": [],
            "choices": [
                {"id": "A", "text": "继续追问刚才那句话"},
                {"id": "B", "text": "换个话题"},
                {"id": "C", "text": "先看看四周"},
                {"id": "D", "text": "暂不表态"},
            ],
            "character_updates": [],
            "memory_candidates": [],
            "schema_suggestions": [],
        }


def get_provider(config: dict[str, Any] | None = None) -> BaseProvider:
    """根据设置选择 Provider。业务代码不直接实例化具体类。

    mock_mode 是唯一真源：只有它没被显式给出时，才回退看 provider 字符串，
    以免旧配置里的 provider=mock 把已经关掉的 Mock 又拉回来。
    """
    from backend.core.settings_service import is_mock_mode, llm_settings

    cfg = config or llm_settings()
    if "mock_mode" in cfg:
        use_mock = is_mock_mode(cfg.get("mock_mode"))
    else:
        use_mock = is_mock_mode(cfg.get("mock_mode")) or cfg.get("provider") == "mock"
    return MockProvider() if use_mock else OpenAICompatProvider(cfg)


def generate(request: LLMRequest, config: dict[str, Any] | None = None) -> GenerationResult:
    return get_provider(config).generate(request)

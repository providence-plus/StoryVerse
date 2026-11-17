"""设置服务：默认值来自 generation_config.yaml，用户修改写在 config/settings.yaml。

API Key 只保存在后端文件里。public_settings() 返回给前端时会把密钥替换成掩码，
日志与任何响应体都不会出现明文密钥。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core import file_utils

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULTS_PATH = PROJECT_ROOT / "config" / "generation_config.yaml"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

PUBLIC_KEYS = ("provider", "base_url", "model", "temperature", "max_tokens", "timeout_seconds", "mock_mode")

# llama.cpp 的 -c 是「输入 + 输出」共用的总窗口，不是只给输出用。
# 默认服务按 -c 32768 启动；若允许把 max_tokens 也设成 32768，输出会把窗口
# 占满，llama.cpp 只能从**左边**截断 prompt，正好切掉最前面的全局写作铁律。
# 因此输出上限锁到窗口的一半，至少给 Prompt 留一半；与默认模板的 16384 一致。
MAX_TOKENS_CEILING = 16384


def is_mock_mode(value: Any) -> bool:
    """兼容 YAML 布尔值与表单可能传来的字符串，统一判断 Mock 开关。"""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def load_defaults() -> dict[str, Any]:
    return file_utils.read_yaml(DEFAULTS_PATH, default={}) or {}


def load_settings() -> dict[str, Any]:
    """合并默认配置与用户设置，返回后端内部使用的完整配置。"""
    defaults = load_defaults()
    user = file_utils.read_yaml(SETTINGS_PATH, default={}) or {}
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in defaults.items()}
    for section, values in (user or {}).items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section].update(values)
        else:
            merged[section] = values
    return merged


def llm_settings() -> dict[str, Any]:
    settings = load_settings()
    llm = dict(settings.get("llm") or {})
    # settings.yaml 未创建时退回默认配置里的值，保证首次启动可用。
    for key, value in (load_defaults().get("llm") or {}).items():
        llm.setdefault(key, value)
    # mock_mode 是唯一真源。旧配置可能只改了 mock_mode 却留着 provider=mock，
    # 这里强制推导，避免「关掉 Mock 却仍在用假数据」这种难以察觉的状态不一致。
    llm["mock_mode"] = is_mock_mode(llm.get("mock_mode", True))
    llm["provider"] = "mock" if llm["mock_mode"] else "openai_compatible"
    # 手改 settings.yaml 或换到 -c 更小的模型服务时，同样不能让输出占满窗口。
    llm["max_tokens"] = max(64, min(MAX_TOKENS_CEILING, int(llm.get("max_tokens", 1600))))
    return llm


def public_settings() -> dict[str, Any]:
    """给前端的设置视图：绝不返回 api_key 明文。"""
    llm = llm_settings()
    result = {k: llm.get(k) for k in PUBLIC_KEYS}
    result["api_key_set"] = bool(llm.get("api_key"))
    result["api_key"] = ""  # 前端输入框留空表示「不修改」
    return result


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """保存用户设置。api_key 为空字符串表示保留原值，不覆盖。"""
    current = file_utils.read_yaml(SETTINGS_PATH, default={}) or {}
    llm = dict(current.get("llm") or {})
    incoming = payload.get("llm") if isinstance(payload.get("llm"), dict) else payload

    for key in PUBLIC_KEYS:
        if key in incoming:
            llm[key] = incoming[key]
    if "api_key" in incoming and str(incoming["api_key"]).strip():
        llm["api_key"] = str(incoming["api_key"]).strip()

    # 温度、长度、超时做边界限制，避免把服务调崩。
    llm["temperature"] = max(0.0, min(2.0, float(llm.get("temperature", 0.85))))
    llm["max_tokens"] = max(64, min(MAX_TOKENS_CEILING, int(llm.get("max_tokens", 1600))))
    llm["timeout_seconds"] = max(5, min(600, int(llm.get("timeout_seconds", 120))))
    llm["mock_mode"] = is_mock_mode(llm.get("mock_mode", True))
    # 前端的 Mock 开关只控制 mock_mode；provider 由后端推导并同步写回，
    # 不允许两个字段互相打架。
    llm["provider"] = "mock" if llm["mock_mode"] else "openai_compatible"

    current["llm"] = llm
    file_utils.atomic_write_yaml(SETTINGS_PATH, current)
    return public_settings()


def test_connection() -> dict[str, Any]:
    """真实探测模型服务。Mock 模式下明确说明未连接真模型，不伪装成功。"""
    from backend.core.llm_provider import OpenAICompatProvider

    llm = llm_settings()
    if llm.get("mock_mode"):
        return {
            "ok": False,
            "message": "当前是 Mock 模拟模式，没有连接真实模型。若要测试真实服务，请先在设置里关闭 Mock Mode。",
            "provider": "mock",
        }
    provider = OpenAICompatProvider(llm)
    result = provider.probe()
    speed = ((result.usage or {}).get("timings") or {}).get("predicted_per_second")
    if result.error:
        message = result.error
    elif isinstance(speed, (int, float)):
        message = f"连接成功，模型：{llm.get('model')}；实测生成速度 {speed:.1f} token/秒。"
        if speed < 15:
            # 启用 GPU 时通常几十 token/秒，退回纯 CPU 只有个位数。
            message += (
                " ⚠️ 速度明显偏低，模型服务很可能没启用 GPU（退回了纯 CPU），长文生成会慢十几倍。"
                "请按你所用推理服务的文档重新启动模型服务后再试。"
            )
    else:
        message = f"连接成功，模型：{llm.get('model')}（该服务未回报速度数据）"
    return {
        "ok": result.error is None,
        "message": message,
        "provider": "openai_compatible",
        "usage": result.usage,
    }

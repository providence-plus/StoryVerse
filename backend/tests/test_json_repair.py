"""回归：本地模型写长中文 JSON 时的三类顽疾。

真实样本来自 Nyx-RP-9B Q5_K_M 的一次世界观扩写：字符串里夹真实回车、
结尾被 max_tokens 砍断。这类输出以前会被直接判为「不是合法 JSON」，
导致世界扩写和人物扩写都看不到结果。
"""

from __future__ import annotations

import json

from backend.core import character_generator, world_generator
from backend.core.llm_provider import GenerationResult
from backend.core.story_parser import (
    parse_json_object,
    parse_turn_output,
    repair_json_string,
)

# 模拟真实模型：字符串里直接按回车分段，且写到一半被截断。
REAL_WORLD_OUTPUT = '''{
  "name": "晴雨街",
  "genre": "现代校园",
  "description": "故事从学生会活动室的一封旧信开始。",
  "setting": "# 城科大学

## 时代与地理

2024年10月，临海城市的新区。梧桐林道把校园切成几块，空气里有旧书纸浆的味道。

## 主要场所

### 学生会活动室
一张掉漆的长桌，三把塑料椅，墙上贴着「禁止触摸」的标签。",
  "rules": "## 硬规则

1. 人物对同一事件的了解程度必须与接触途径匹配。
2. 时间线只向前推进。

## 叙事一致性

关键事件必须绑定具体日期，例如「10月24日'''


def test_repair_handles_real_newlines_and_truncation():
    data, note = parse_json_object(REAL_WORLD_OUTPUT)
    assert data is not None, "真实世界的长中文 JSON 必须能解析"
    assert data["name"] == "晴雨街"
    assert "城科大学" in data["setting"]
    assert "硬规则" in data["rules"]
    assert "自动修复" in note
    assert "换行" in note
    assert "截断" in note


def test_repair_converts_inner_ascii_quotes():
    raw = '{"narration": "他低声说"你来了"，然后停住。", "choices": []}'
    data, note = parse_json_object(raw)
    assert data is not None
    assert "你来了" in data["narration"]
    assert "中文引号" in note


def test_clean_json_is_not_flagged():
    raw = json.dumps({"narration": "正文\n第二段", "choices": []}, ensure_ascii=False)
    data, note = parse_json_object(raw)
    assert data == {"narration": "正文\n第二段", "choices": []}
    assert note == "", "合法 JSON 不应被标成已修复"


def test_markdown_fence_and_think_are_stripped():
    raw = '<think>先想一下</think>```json\n{"a": 1}\n```'
    data, _ = parse_json_object(raw)
    assert data == {"a": 1}


def test_top_level_array_is_wrapped_for_characters():
    raw = '[{"name": "甲", "custom_fields": {}}, {"name": "乙", "custom_fields": {}}]'
    data, _ = parse_json_object(raw)
    assert data is not None and len(data["characters"]) == 2


def test_turn_output_exposes_repair_warning():
    raw = '{"narration": "第一段\n第二段", "choices": []}'
    # 用真实换行（非转义）触发修复
    raw = raw.replace("\\n", "\n")
    output, reason = parse_turn_output(raw)
    assert output is not None
    assert output.parse_warning and "自动修复" in output.parse_warning
    assert "自动修复" in reason


def test_world_preview_survives_broken_model_output(worlds_root, monkeypatch):
    """世界扩写：坏 JSON 也要出预览，并把修复原因告诉用户。"""
    monkeypatch.setattr(
        world_generator, "generate",
        lambda request: GenerationResult(text=REAL_WORLD_OUTPUT, provider="fake", model="fake"),
    )
    result = world_generator.generate_preview("现代大学校园，一个大三男生的故事")
    preview = result["preview"]
    assert "城科大学" in preview["setting"], "必须看到扩写后的世界观，而不是原话"
    assert len(preview["setting"]) > 100
    assert len(preview["opening_choices"]) == 4
    assert preview["opening_scene"], "截断时开场画面要有可读兜底"
    assert any("自动修复" in w for w in result["warnings"])


def test_character_preview_survives_broken_model_output(seeded_world, monkeypatch):
    """人物扩写：字段值含真实换行时仍要出预览，且字段按 schema 补齐。"""
    broken = '''{
  "characters": [
    {
      "name": "沈知微",
      "core_description": "一位话不多的成年档案管理员。",
      "custom_fields": {
        "appearance": "身形清瘦，
常把袖口扣到手腕。",
        "hidden_thoughts": "她在等人先开口。"
      },
      "plot_hooks": ["抽屉里有一封没寄出的信"]
    }
  ]
}'''
    monkeypatch.setattr(
        character_generator, "generate",
        lambda request: GenerationResult(text=broken, provider="fake", model="fake"),
    )
    result = character_generator.generate_preview(seeded_world, "一位话不多的档案管理员")
    assert len(result["characters"]) == 1
    char = result["characters"][0]
    assert char["name"] == "沈知微"
    assert "袖口" in str(char["custom_fields"].get("appearance", ""))
    assert result["field_definitions"], "预览要带上字段定义供前端渲染"
    assert any("自动修复" in w for w in result["warnings"])


def test_character_preview_accepts_single_object(seeded_world, monkeypatch):
    """模型有时把单个人物直接返回成顶层对象，不能报「没有返回人物数据」。"""
    single = '{"name": "陆砚", "core_description": "成年船长", "custom_fields": {}}'
    monkeypatch.setattr(
        character_generator, "generate",
        lambda request: GenerationResult(text=single, provider="fake", model="fake"),
    )
    result = character_generator.generate_preview(seeded_world, "一位成年船长")
    assert result["characters"][0]["name"] == "陆砚"


def test_repair_json_string_returns_parseable_text():
    fixed, notes = repair_json_string(REAL_WORLD_OUTPUT)
    assert isinstance(json.loads(fixed), dict)
    assert any("换行" in n for n in notes)

# 任务：收束当前场景

> 本文件由后端每轮重新读取，可在 Prompt Studio 里编辑，保存后下一轮立即生效。

玩家决定结束眼前这一幕。请写一段**自然收束**的旁白，把这场戏有头有尾地落下：对话告一段落、某个角色转身离去、天色已晚各自散去……总之让这一幕有个像样的句点，同时给下一幕留一个自然的接点。

**不要**开新剧情、不要给接下来四个选项、不要写主角下一步打算做什么。角色是否离场，按剧情顺势而为，不强行让人人散场。

**本轮只收束，绝不推进任何新的事件。** 尤其是下面「预定事件」那一栏如果给了清单，那也只是告诉你玩家排过什么，本轮一个都不要写成新场景——下一幕由玩家自己用场景表单来定义。

## 本轮上下文

- **玩家扮演的角色（下称「主角」）**：
  {{protagonist_line}}

- 世界设定、规则、当前地点：
  {{world_context}}

- 在场人物卡：
  {{characters_json}}

- 人物字段定义：
  {{panel_schema_json}}

- 人物字段值：
  {{character_fields_json}}

- 人物关系：
  {{relationships_json}}

- 剧情摘要：
  {{summary}}

- 最近几轮：
  {{recent_history}}

- 预定事件清单（本轮一律不推进，只为避免写出矛盾）：
  {{planned_events}}

- 本幕场景：
  {{scene_description}}

- 本轮首次登场角色：
  {{introducing}}

- 玩家本轮行动：
  {{player_action}}

## 动态字段

规则同剧情回合：只提交本段确实改变的字段，字段定义见 {{panel_schema_json}}，人物字段一律不写死。

## 严格输出以下 JSON，不要包代码块以外的解释文字

JSON 红线同剧情回合：字符串内换行写成 `\n`，字符串值内禁止 ASCII 双引号，最后以 `}` 完整收尾。

```json
{
  "narration": "收尾旁白",
  "dialogues": [],
  "character_updates": [],
  "memory_candidates": [],
  "schema_suggestions": []
}
```

`dialogues` / `character_updates` / `memory_candidates` / `schema_suggestions` 没有内容时给空数组，不要省略键。本任务**没有 choices**。

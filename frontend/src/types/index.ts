export type FieldType = 'text' | 'long_text' | 'number' | 'boolean' | 'enum' | 'list' | 'key_value'
export type UpdatePolicy = 'static' | 'mostly_static' | 'story_driven'

export interface FieldDefinition {
  id: string
  /** 仅前端用于列表 key 的稳定标识，不发给后端、不落盘。 */
  uid?: string
  label: string
  description: string
  type: FieldType
  visible: boolean
  update_policy: UpdatePolicy
  options?: string[]
  min?: number | null
  max?: number | null
}

export interface PanelGroup { fields: FieldDefinition[] }

export interface PanelSchema {
  version: number
  same_fields_for_all_npcs: boolean
  protagonist: PanelGroup
  npc: PanelGroup
  overrides: Record<string, unknown>
}

export interface CharacterPanelData {
  is_protagonist: boolean
  fields: FieldDefinition[]
  values: Record<string, unknown>
  orphaned: Record<string, unknown>
}

export interface PanelsPayload {
  schema: PanelSchema
  characters: Record<string, CharacterPanelData>
  available_types: string[]
}

export interface CharacterProfile {
  character_id: string
  name: string
  is_protagonist: boolean
  core_description: string
  bible?: CharacterBible
}

export interface CharacterState {
  character_id: string
  custom_fields: Record<string, unknown>
  _orphaned: Record<string, unknown>
  retired?: boolean
  pending_introduction?: boolean
}

export interface CharacterView {
  profile: CharacterProfile
  state: CharacterState
  fields: FieldDefinition[]
}

export interface Dialogue { speaker_id: string; text: string }
export interface Choice { id: string; text: string }

export interface TurnOutput {
  narration: string
  dialogues: Dialogue[]
  choices: Choice[]
  character_updates: unknown[]
  memory_candidates: string[]
  schema_suggestions: unknown[]
  parse_warning?: string | null
  raw_text?: string | null
}

export interface TurnResponse {
  turn: number
  world_id: string
  save_id: string
  player_action: string
  output: TurnOutput
  characters: CharacterView[]
  panels: PanelsPayload
  current_location: string
  suggestions_pending: number
  /** 本轮是「收束当前场景」的收尾轮，前端据此直接弹出下一幕表单。 */
  scene_ended: boolean
}

export interface WorldSummary {
  world_id: string
  name: string
  description: string
  genre: string
  character_count: number
  save_count: number
  last_played_at: string | null
  current_location: string
  has_protagonist: boolean
  opening_scene?: string
  opening_location?: string
  opening_choices?: Choice[]
  story_started?: boolean
}

/** 一句话生成世界后、用户确认落盘之前的可编辑预览。 */
export interface GeneratedWorld {
  name: string
  genre: string
  description: string
  setting: string
  rules: string
  opening_location: string
  opening_scene: string
  opening_choices: Choice[]
  protagonist_hint: string
  npc_hints: string[]
  /** 写手风格：确认创建后写入该世界自己的 system_prompt.md。 */
  writer_style: string
}

export interface GeneratedWorldResponse {
  preview: GeneratedWorld
  warnings: string[]
  provider: string
  model: string
  raw_text: string
  degraded?: boolean
}

export interface PromptInfo { name: string; label: string; path: string; size: number }

export interface AppSettings {
  provider: string
  base_url: string
  model: string
  temperature: number
  max_tokens: number
  timeout_seconds: number
  mock_mode: boolean
  api_key: string
  api_key_set: boolean
}

export interface SaveInfo {
  save_id: string
  name: string
  created_at?: string
  updated_at?: string
  turn?: number
}

export interface FieldSuggestion {
  field_id: string
  label: string
  type: string
  reason: string
  example: unknown
  occurrences: number
  status: 'pending' | 'ignored' | 'adopted'
  first_turn: number
  last_turn: number
}

export interface GeneratedCharacter {
  name: string
  core_description: string
  custom_fields: Record<string, unknown>
  plot_hooks: string[]
  opening_situation: string
  is_protagonist: boolean
  bible?: CharacterBible
}

/* ---------- 玩家自定义设定基线 ---------- */

/** 一个条目：player_input 是玩家原话（只读展示），text 是 AI 补全后玩家精修的定稿。 */
export interface BibleItem {
  player_input: string
  text: string
}

export interface PlannedEvent {
  /** 预览阶段为空串：正式 id 在确认保存时由后端按角色 ID 重算；列表页用 GET /planned-events 的 id。 */
  id: string
  text: string
}

/** 一条「与某角色」的关系卡。target_character_id 只能是本世界已存在角色。 */
export interface RelationCard {
  /** 仅前端列表 key 用；后端返回的 bible 关系卡没有这个字段。 */
  uid?: string
  target_character_id: string
  target_name: string
  relation: BibleItem
  past_events: BibleItem[]
  planned_events: PlannedEvent[]
}

export interface CharacterBible {
  items: Record<'appearance' | 'personality' | 'background' | 'major_events', BibleItem>
  relations: RelationCard[]
}

/** 自定义补全接口返回的预览（与 GeneratedCharacter[] 不是同一结构，需独立 state）。 */
export interface CustomizePreview {
  name: string
  core_description: string
  items: CharacterBible['items']
  relations: RelationCard[]
  warnings: string[]
  provider: string
  model: string
  raw_text: string
  degraded?: boolean
}

export interface WorldCustomizePreview {
  preview: GeneratedWorld
  warnings: string[]
  provider: string
  model: string
  raw_text: string
  degraded?: boolean
}

export interface HistoryPayload {
  history: Array<{
    turn: number
    player_action: string
    narration: string
    dialogues: Dialogue[]
    choices: Choice[]
  }>
  timeline: Array<{ turn: number; text: string; importance: string }>
  summary: string
}

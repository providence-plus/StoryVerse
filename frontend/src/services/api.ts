import type {
  AppSettings, CharacterBible, CharacterView, CustomizePreview, FieldSuggestion, GeneratedCharacter,
  GeneratedWorld, GeneratedWorldResponse, HistoryPayload, PanelsPayload, PromptInfo,
  SaveInfo, TurnResponse, WorldCustomizePreview, WorldSummary,
} from '../types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    })
  } catch {
    throw new Error('无法连接后端服务。请确认 start.bat 已启动、8000 端口可用。')
  }
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body)
    } catch { /* 响应不是 JSON 时保留默认文案 */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

const get = <T,>(path: string) => request<T>(path)
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const put = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
const patch = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T,>(path: string) => request<T>(path, { method: 'DELETE' })

export const api = {
  health: () => get<{ status: string }>('/health'),

  listWorlds: () => get<{ worlds: WorldSummary[] }>('/worlds'),
  createWorld: (name: string, description: string, genre: string, options?: Record<string, unknown>) =>
    post<WorldSummary>('/worlds', { name, description, genre, ...(options || {}) }),
  generateWorld: (description: string, genre: string) =>
    post<GeneratedWorldResponse>('/worlds/generate', { description, genre }),
  confirmGeneratedWorld: (preview: GeneratedWorld) =>
    post<WorldSummary>('/worlds/confirm', preview),
  customizeWorld: (items: Record<string, unknown>) =>
    post<WorldCustomizePreview>('/worlds/customize-preview', items),
  getWorldSystemPrompt: (id: string) =>
    get<{ world_id: string; content: string; global_size: number }>(`/worlds/${id}/system-prompt`),
  saveWorldSystemPrompt: (id: string, content: string) =>
    put<{ saved: string; size: number }>(`/worlds/${id}/system-prompt`, { content }),
  getWorld: (id: string) => get<WorldSummary & { panels: PanelsPayload }>(`/worlds/${id}`),
  startWorld: (id: string) => post<WorldSummary>(`/worlds/${id}/start`),
  deleteWorld: (id: string) => del<{ deleted: string }>(`/worlds/${id}`),

  getCharacters: (id: string) =>
    get<{ characters: CharacterView[]; panels: PanelsPayload }>(`/worlds/${id}/characters`),
  generateCharacter: (id: string, description: string, count: number, isProtagonist: boolean, autoRelations: boolean) =>
    post<{
      characters: GeneratedCharacter[]; warnings: string[]; field_definitions: unknown[];
      provider: string; is_protagonist: boolean;
    }>(`/worlds/${id}/generate-character`,
      { description, count, is_protagonist: isProtagonist, auto_relations: autoRelations }),
  confirmCharacters: (id: string, characters: GeneratedCharacter[], isProtagonist: boolean) =>
    post<{ saved: CharacterView[]; panels: PanelsPayload }>(`/worlds/${id}/characters`,
      { characters, is_protagonist: isProtagonist }),
  editCharacter: (id: string, cid: string, payload: { name: string; core_description: string; bible: CharacterBible }) =>
    put<CharacterView>(`/worlds/${id}/characters/${cid}`, payload),
  deleteCharacter: (id: string, cid: string) =>
    del<{ deleted: string }>(`/worlds/${id}/characters/${cid}`),
  retireCharacter: (id: string, cid: string, retired: boolean) =>
    post<CharacterView>(`/worlds/${id}/characters/${cid}/retire`, { retired }),
  customizeCharacter: (id: string, inputs: Record<string, unknown>, isProtagonist: boolean) =>
    post<CustomizePreview>(`/worlds/${id}/customize-character-preview`,
      { inputs, is_protagonist: isProtagonist }),
  getPlannedEvents: (id: string) => get<{
    events: Array<{
      id: string; text: string; owner_id: string; owner_name: string;
      target_character_id: string; target_name: string;
    }>;
    completed: string[];
  }>(`/worlds/${id}/planned-events`),
  setPlannedEventDone: (id: string, cid: string, eventId: string, done: boolean) =>
    patch<{ event_id: string; done: boolean; completed: string[] }>(
      `/worlds/${id}/characters/${cid}/planned-events/${eventId}`, { done }),
  patchField: (id: string, cid: string, fieldId: string, value: unknown) =>
    patch<CharacterView>(`/worlds/${id}/characters/${cid}/fields`, { field_id: fieldId, value }),
  backfill: (id: string, cid: string) =>
    post<{ applied: Record<string, unknown>; message?: string }>(`/worlds/${id}/characters/${cid}/fields/backfill`),
  reconcile: (id: string, cid: string) =>
    post<{ corrections: number; fields?: string[] }>(`/worlds/${id}/characters/${cid}/fields/reconcile`),

  getPanels: (id: string) => get<PanelsPayload>(`/worlds/${id}/ui/panels`),
  savePanels: (id: string, schema: unknown) =>
    put<{ panels: PanelsPayload; added_fields: string[]; backfilled: unknown; warnings: string[] }>(
      `/worlds/${id}/ui/panels`, { schema }),
  getSuggestions: (id: string) => get<{ items: FieldSuggestion[] }>(`/worlds/${id}/field-suggestions`),
  adoptSuggestion: (id: string, fieldId: string) =>
    post<unknown>(`/worlds/${id}/field-suggestions/${fieldId}/adopt`),
  ignoreSuggestion: (id: string, fieldId: string) =>
    post<{ ignored: string }>(`/worlds/${id}/field-suggestions/${fieldId}/ignore`),

  turn: (id: string, body: {
    action: string; choice_id?: string | null; scene_description?: string;
    location?: string; active_character_ids?: string[]; end_scene?: boolean;
  }) => post<TurnResponse>(`/worlds/${id}/story/turn`, body),
  getHistory: (id: string) => get<HistoryPayload>(`/worlds/${id}/history?limit=100`),
  getStoryConfig: (id: string) =>
    get<{ field_system: Record<string, number>; history_turns: number; panels: PanelsPayload }>(
      `/worlds/${id}/story/config`),

  getSaves: (id: string) => get<{ saves: SaveInfo[] }>(`/worlds/${id}/saves`),
  createSave: (id: string, name: string) => post<SaveInfo>(`/worlds/${id}/saves`, { name }),
  loadSave: (id: string, saveId: string) =>
    post<SaveInfo>(`/worlds/${id}/saves/${saveId}/load`),

  listPrompts: () => get<{ prompts: PromptInfo[] }>('/prompts'),
  getPrompt: (name: string) => get<PromptInfo & { content: string }>(`/prompts/${name}`),
  savePrompt: (name: string, content: string) =>
    put<{ saved: string; size: number }>(`/prompts/${name}`, { content }),

  getSettings: () => get<AppSettings>('/settings'),
  saveSettings: (payload: Partial<AppSettings>) => put<AppSettings>('/settings', payload),
  testConnection: () =>
    post<{ ok: boolean; message: string; provider: string }>('/settings/test-connection'),
}

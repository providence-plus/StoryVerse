import { useEffect, useState } from 'react'
import { Pencil, RefreshCw, Save, Sparkles, Trash2, UserMinus, UserPlus, Users } from 'lucide-react'
import CharacterCustomizeForm from '../components/CharacterCustomizeForm'
import DynamicPanel from '../components/DynamicPanel'
import FieldRenderer from '../components/FieldRenderer'
import FieldSuggestions from '../components/FieldSuggestions'
import PanelDesigner from '../components/PanelDesigner'
import { api } from '../services/api'
import type { CharacterView, FieldDefinition, GeneratedCharacter, PanelsPayload } from '../types'

type Tab = 'list' | 'generate' | 'designer'
type GenMode = 'oneline' | 'custom'

interface PlannedRow {
  id: string
  text: string
  owner_id: string
  owner_name: string
  target_character_id: string
  target_name: string
}

interface Props { worldId: string; worldName: string }

export default function CharactersPage({ worldId, worldName }: Props) {
  const [tab, setTab] = useState<Tab>('list')
  const [genMode, setGenMode] = useState<GenMode>('oneline')
  const [characters, setCharacters] = useState<CharacterView[]>([])
  const [panels, setPanels] = useState<PanelsPayload | null>(null)
  const [planned, setPlanned] = useState<{ events: PlannedRow[]; completed: string[] }>({ events: [], completed: [] })
  const [description, setDescription] = useState('')
  const [count, setCount] = useState(1)
  const [isProtagonist, setIsProtagonist] = useState(false)
  const [autoRelations, setAutoRelations] = useState(false)
  const [previews, setPreviews] = useState<GeneratedCharacter[]>([])
  const [fieldDefs, setFieldDefs] = useState<FieldDefinition[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pendingSuggestions, setPendingSuggestions] = useState(0)
  const [storyStarted, setStoryStarted] = useState(false)
  const [editing, setEditing] = useState<CharacterView | null>(null)

  const load = () => {
    Promise.all([
      api.getCharacters(worldId),
      api.getPanels(worldId),
      api.getSuggestions(worldId),
      api.getPlannedEvents(worldId).catch(() => ({ events: [], completed: [] })),
      api.getWorld(worldId).catch(() => null),
    ])
      .then(([chars, panelData, suggestions, plannedData, worldData]) => {
        setCharacters(chars.characters)
        setPanels(panelData)
        setPendingSuggestions(suggestions.items.filter((i) => i.status === 'pending').length)
        setPlanned({ events: plannedData.events || [], completed: plannedData.completed || [] })
        setStoryStarted(worldData?.story_started === true)
      })
      .catch((e) => setError((e as Error).message))
  }
  useEffect(load, [worldId])

  const generate = async () => {
    if (!description.trim()) { setError('先写一句人物核心描述'); return }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api.generateCharacter(worldId, description.trim(), count, isProtagonist, autoRelations)
      setPreviews(result.characters)
      setFieldDefs(result.field_definitions as FieldDefinition[])
      setWarnings(result.warnings)
      setNotice(`已生成 ${result.characters.length} 份预览（${result.provider}）。请检查并编辑，确认后才会保存。`)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const updatePreview = (index: number, patch: Partial<GeneratedCharacter>) => {
    setPreviews((list) => list.map((item, i) => i === index ? { ...item, ...patch } : item))
  }

  const updatePreviewField = (index: number, fieldId: string, value: unknown) => {
    setPreviews((list) => list.map((item, i) => i === index
      ? { ...item, custom_fields: { ...item.custom_fields, [fieldId]: value } }
      : item))
  }

  const confirm = async () => {
    setBusy(true); setError('')
    try {
      const result = await api.confirmCharacters(worldId, previews, isProtagonist)
      setPreviews([])
      setNotice(`已保存 ${result.saved.length} 位人物到独立 YAML / JSON 文件。`)
      load()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const removeCharacter = async (c: CharacterView) => {
    if (!window.confirm(`确定删除「${c.profile.name}」吗？会同时清理其他角色设定里指向 TA 的关系与事件，此操作不可恢复。`)) return
    setBusy(true); setError('')
    try {
      await api.deleteCharacter(worldId, c.profile.character_id)
      setNotice(`已删除「${c.profile.name}」。`)
      load()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const toggleRetire = async (c: CharacterView) => {
    setBusy(true); setError('')
    try {
      await api.retireCharacter(worldId, c.profile.character_id, !c.state.retired)
      setNotice(c.state.retired
        ? `「${c.profile.name}」已恢复在场。`
        : `「${c.profile.name}」已退场，不再出现在后续剧情里（历史与设定都还在，可随时恢复）。`)
      load()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const toggleEvent = async (characterId: string, eventId: string, done: boolean) => {
    try {
      await api.setPlannedEventDone(worldId, characterId, eventId, done)
      setPlanned((prev) => ({
        ...prev,
        completed: done
          ? [...prev.completed.filter((x) => x !== eventId), eventId]
          : prev.completed.filter((x) => x !== eventId),
      }))
      setNotice(done ? '已标记为完成，之后剧情不再把这条当作优先任务。' : '已重新开启这条预定事件。')
    } catch (e) { setError((e as Error).message) }
  }

  const names: Record<string, string> = {}
  for (const c of characters) names[c.profile.character_id] = c.profile.name

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">人物 · {worldName}</h1>
          <p className="page-sub">固定人物设定与当前动态字段分开存；面板上有哪些字段，完全由你在 Panel Designer 里决定。</p>
        </div>
        <FieldSuggestions worldId={worldId} pendingCount={pendingSuggestions} onChanged={load} />
      </div>

      <div className="tabs">
        <button className={`tab ${tab === 'list' ? 'active' : ''}`} onClick={() => setTab('list')}><Users size={13} /> 人物列表</button>
        <button className={`tab ${tab === 'generate' ? 'active' : ''}`} onClick={() => setTab('generate')}><Sparkles size={13} /> 新建人物</button>
        <button className={`tab ${tab === 'designer' ? 'active' : ''}`} onClick={() => setTab('designer')}>Panel Designer</button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      {tab === 'list' && (
        <div className="split">
          <div>
            <h3 className="section-title" style={{ marginTop: 0 }}>人物卡（{characters.length}）</h3>
            {characters.length === 0 && <div className="card empty">这个世界还没有人物，用「新建人物」造一个。</div>}
            {characters.map((c) => {
              const bible = c.profile.bible
              const filled = bible ? Object.values(bible.items || {}).filter((i) => i && i.text).length : 0
              const relationCount = bible?.relations?.length || 0
              const myEvents = planned.events.filter((e) => e.owner_id === c.profile.character_id)
              return (
                <div className="card" key={c.profile.character_id} style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                    <strong>{c.profile.name}</strong>
                    <span className="chip">{c.profile.is_protagonist ? '主角' : 'NPC'}</span>
                    {c.state.retired && <span className="chip plain">已退场</span>}
                  </div>
                  <div className="field-hint" style={{ margin: '6px 0 10px' }}>{c.profile.core_description}</div>
                  <div className="field-hint">
                    profile.yaml 固定设定 · state.json 动态字段 {Object.keys(c.state.custom_fields).length} 项
                    {Object.keys(c.state._orphaned || {}).length > 0 && ` · 旧值 ${Object.keys(c.state._orphaned).length} 项`}
                  </div>
                  {(filled > 0 || relationCount > 0) && (
                    <div className="field-hint" style={{ marginTop: 6 }}>
                      设定基线：{filled}/4 条已定稿 · 关系 {relationCount} 条（保存后剧情以它为准）
                    </div>
                  )}
                  {myEvents.length > 0 && (
                    <div style={{ marginTop: 10 }}>
                      <div className="field-label">预定将发生的事件</div>
                      {myEvents.map((evt) => (
                        <label className="switch-row" key={evt.id} style={{ padding: '4px 0' }}>
                          <span className="field-hint">{evt.text}</span>
                          <input type="checkbox" checked={planned.completed.includes(evt.id)}
                            onChange={(e) => void toggleEvent(c.profile.character_id, evt.id, e.target.checked)} />
                        </label>
                      ))}
                      <div className="field-hint" style={{ marginTop: 4 }}>勾选表示这条已在剧情里完成，之后不再优先推进；完成状态随存档走。</div>
                    </div>
                  )}
                  <div className="btn-row" style={{ marginTop: 10, flexWrap: 'wrap' }}>
                    {!storyStarted ? (
                      <>
                        <button className="btn btn-sm" onClick={() => { setEditing(c); setTab('generate'); setGenMode('custom') }}>
                          <Pencil size={13} /> 编辑
                        </button>
                        <button className="btn btn-sm" onClick={() => void removeCharacter(c)}>
                          <Trash2 size={13} /> 删除
                        </button>
                      </>
                    ) : (
                      <button className="btn btn-sm" onClick={() => void toggleRetire(c)}>
                        {c.state.retired ? <UserPlus size={13} /> : <UserMinus size={13} />}
                        {c.state.retired ? '恢复在场' : '让角色退场'}
                      </button>
                    )}
                    <button className="btn btn-sm" onClick={async () => {
                      try {
                        const r = await api.backfill(worldId, c.profile.character_id)
                        setNotice(Object.keys(r.applied).length ? `已为新增字段补齐 ${Object.keys(r.applied).length} 项。` : (r.message || '没有需要补齐的字段。'))
                        load()
                      } catch (e) { setError((e as Error).message) }
                    }}><RefreshCw size={13} /> 补齐新字段</button>
                    <button className="btn btn-sm" onClick={async () => {
                      try {
                        const r = await api.reconcile(worldId, c.profile.character_id)
                        setNotice(r.corrections ? `校准修正了 ${r.corrections} 个字段。` : '校准完成，无需修正。')
                        load()
                      } catch (e) { setError((e as Error).message) }
                    }}>校准字段</button>
                  </div>
                </div>
              )
            })}
          </div>
          <div>
            <h3 className="section-title" style={{ marginTop: 0 }}>当前动态字段</h3>
            {panels && Object.entries(panels.characters).map(([id, data]) => (
              <div key={id} style={{ marginBottom: 12 }}>
                <DynamicPanel title={names[id] || (data.is_protagonist ? '主角' : 'NPC')}
                  worldId={worldId} characterId={id} data={data} onChanged={load} />
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'generate' && (
        <div>
          {editing ? (
            <div>
              <div className="btn-row" style={{ marginBottom: 12 }}>
                <button className="btn btn-sm" onClick={() => setEditing(null)}>取消编辑</button>
                <span className="field-hint">正在编辑：{editing.profile.name}（{editing.profile.is_protagonist ? '主角' : 'NPC'}）</span>
              </div>
              <CharacterCustomizeForm
                worldId={worldId}
                characters={characters}
                isProtagonist={editing.profile.is_protagonist}
                editingCharacter={editing}
                key={editing.profile.character_id}
                onSaved={() => { setEditing(null); load() }}
                onGoCreateCharacter={() => setNotice('正在编辑，请先「取消编辑」再去新建人物。')}
                onError={setError}
                onNotice={setNotice}
              />
            </div>
          ) : (
          <div>
          <div className="tabs" style={{ marginBottom: 14 }}>
            <button className={`tab ${genMode === 'oneline' ? 'active' : ''}`} onClick={() => setGenMode('oneline')}>
              <Sparkles size={13} /> 一句话生成
            </button>
            <button className={`tab ${genMode === 'custom' ? 'active' : ''}`} onClick={() => setGenMode('custom')}>
              自定义 + AI 补全
            </button>
          </div>

          {/* 类型必须两种模式共用：放在 custom 分支里会导致「自定义创建主角」不可达。 */}
          <div className="form-row two" style={{ marginBottom: 14 }}>
            <div>
              <label className="field-label">角色类型（两种模式共用）</label>
              <select className="select" value={isProtagonist ? 'protagonist' : 'npc'}
                onChange={(e) => setIsProtagonist(e.target.value === 'protagonist')}>
                <option value="npc">NPC</option>
                <option value="protagonist">主角</option>
              </select>
            </div>
            <div className="field-hint" style={{ alignSelf: 'end', paddingBottom: 6 }}>
              主角每个世界只有一个；用同样的流程再确认一次会覆盖已有主角（已保存的设定基线会保留）。
            </div>
          </div>

          {genMode === 'custom' ? (
            <CharacterCustomizeForm
              worldId={worldId}
              characters={characters}
              isProtagonist={isProtagonist}
              onSaved={load}
              onGoCreateCharacter={() => {
                setGenMode('oneline')
                setNotice('已暂存。用「一句话生成」把对方建好，再切回「自定义 + AI 补全」点「恢复继续填写」。')
              }}
              onError={setError}
              onNotice={setNotice}
            />
          ) : (
            <div className="split">
              <div className="card">
                <h3 className="section-title" style={{ marginTop: 0 }}>核心描述</h3>
                <textarea className="textarea" value={description}
                  placeholder="例如：一个傲娇但内心善良的成年大小姐，养尊处优，却把旧宅钥匙攥了三年"
                  onChange={(e) => setDescription(e.target.value)} />
                <div className="field-hint">模型会读取当前世界的字段定义，把你没写的每一项都补齐。想逐条自己定，切到「自定义 + AI 补全」。</div>

                <div className="form-row" style={{ marginTop: 14 }}>
                  <div>
                    <label className="field-label">生成数量（自定义模式固定 1 人）</label>
                    <input className="input" type="number" min={1} max={8} value={isProtagonist ? 1 : count}
                      disabled={isProtagonist}
                      onChange={(e) => setCount(Math.max(1, Math.min(8, Number(e.target.value))))} />
                  </div>
                </div>
                <label className="switch-row">
                  <span>批量时让 AI 自动设计人物之间的关系</span>
                  <input type="checkbox" checked={autoRelations} disabled={isProtagonist || count < 2}
                    onChange={(e) => setAutoRelations(e.target.checked)} />
                </label>
                <button className="btn btn-primary" disabled={busy} onClick={() => void generate()}>
                  <Sparkles size={14} /> {busy ? '生成中…' : '生成预览'}
                </button>
                {warnings.length > 0 && (
                  <div className="notice" style={{ marginTop: 12 }}>
                    校验提示：{warnings.slice(0, 4).join('；')}{warnings.length > 4 ? '…' : ''}
                  </div>
                )}
              </div>

              <div>
                <h3 className="section-title" style={{ marginTop: 0 }}>人物预览（{previews.length}）</h3>
                {previews.length === 0 && <div className="card empty">生成之后在这里检查、编辑，确认才会写进文件。</div>}
                {previews.map((preview, index) => (
                  <div className="preview-card" key={index}>
                    <label className="field-label">姓名</label>
                    <input className="input" value={preview.name}
                      onChange={(e) => updatePreview(index, { name: e.target.value })} />
                    <label className="field-label" style={{ marginTop: 8 }}>人物定位</label>
                    <textarea className="textarea" style={{ minHeight: 60 }} value={preview.core_description}
                      onChange={(e) => updatePreview(index, { core_description: e.target.value })} />
                    <div style={{ marginTop: 10 }}>
                      {fieldDefs.map((field) => (
                        <div className="field-row" key={field.id}>
                          <div className="field-label" title={field.description}>{field.label}</div>
                          <FieldRenderer field={field} value={preview.custom_fields[field.id]} editing
                            onChange={(v) => updatePreviewField(index, field.id, v)} />
                        </div>
                      ))}
                    </div>
                    {preview.plot_hooks.length > 0 && (
                      <div className="field-hint" style={{ marginTop: 8 }}>剧情钩子：{preview.plot_hooks.join('；')}</div>
                    )}
                  </div>
                ))}
                {previews.length > 0 && (
                  <button className="btn btn-primary" disabled={busy} onClick={() => void confirm()}>
                    <Save size={14} /> 确认保存为人物文件
                  </button>
                )}
              </div>
            </div>
          )}
          </div>
          )}
        </div>
      )}

      {tab === 'designer' && <PanelDesigner worldId={worldId} onChanged={load} />}
    </div>
  )
}

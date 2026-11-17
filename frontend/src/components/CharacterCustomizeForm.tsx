import { useEffect, useState } from 'react'
import { Plus, Save, Sparkles, Trash2, Users } from 'lucide-react'
import { api } from '../services/api'
import { clearDraft, draftKey, readDraft, saveDraft } from '../utils/draft'
import type {
  BibleItem, CharacterBible, CharacterView, CustomizePreview, GeneratedCharacter, PlannedEvent,
} from '../types'

interface Props {
  worldId: string
  characters: CharacterView[]
  isProtagonist: boolean
  onSaved: () => void
  onGoCreateCharacter: () => void
  onError: (message: string) => void
  onNotice: (message: string) => void
  /** 传入即进入「编辑已有角色」模式，保存走 PUT 而非新建。 */
  editingCharacter?: CharacterView | null
}

type ItemKey = 'appearance' | 'personality' | 'background' | 'major_events'

const ITEM_LABELS: Record<ItemKey, string> = {
  appearance: '外貌', personality: '性格', background: '背景', major_events: '角色背景重大事件',
}
const ITEM_KEYS: ItemKey[] = ['appearance', 'personality', 'background', 'major_events']

/** 表单与后端 bible 结构不同：多行文本按行存，提交时才拆成条目数组。 */
interface RelationForm {
  uid: string
  target_character_id: string
  relation: string
  past_events: string
  planned_events: string
}

interface FormState {
  name: string
  core_description: string
  appearance: string
  personality: string
  background: string
  major_events: string
  relations: RelationForm[]
}

let uidCounter = 0
const nextUid = () => `rel_${Date.now().toString(36)}_${++uidCounter}`

const emptyForm = (): FormState => ({
  name: '', core_description: '',
  appearance: '', personality: '', background: '', major_events: '',
  relations: [],
})

/** 旧版本草稿可能缺字段：能补就补；只有 relations 不是数组才真没法恢复。 */
const normalizeCharacterDraft = (value: unknown): FormState | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const draft = value as Record<string, unknown>
  const base = emptyForm()
  const text = (key: 'name' | 'core_description' | 'appearance' | 'personality'
    | 'background' | 'major_events'): string =>
    typeof draft[key] === 'string' ? draft[key] as string : base[key]
  if (!Array.isArray(draft.relations)) return null
  const relations: RelationForm[] = draft.relations.map((rel) => {
    const row = (rel && typeof rel === 'object' && !Array.isArray(rel))
      ? rel as Record<string, unknown> : {}
    const str = (key: keyof RelationForm): string =>
      typeof row[key] === 'string' ? row[key] as string : ''
    return {
      uid: str('uid') || nextUid(),
      target_character_id: str('target_character_id'),
      relation: str('relation'),
      past_events: str('past_events'),
      planned_events: str('planned_events'),
    }
  })
  return {
    name: text('name'), core_description: text('core_description'),
    appearance: text('appearance'), personality: text('personality'),
    background: text('background'), major_events: text('major_events'),
    relations,
  }
}

const splitLines = (value: string): string[] =>
  value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)

const blankItem = (text: string): BibleItem => ({ player_input: text, text })

function targetName(characters: CharacterView[], id: string): string {
  return characters.find((c) => c.profile.character_id === id)?.profile.name || ''
}

function toInputs(form: FormState, characters: CharacterView[]) {
  return {
    name: form.name,
    core_description: form.core_description,
    items: {
      appearance: { player_input: form.appearance, text: '' },
      personality: { player_input: form.personality, text: '' },
      background: { player_input: form.background, text: '' },
      major_events: { player_input: form.major_events, text: '' },
    },
    relations: form.relations.map((rel) => ({
      target_character_id: rel.target_character_id,
      target_name: targetName(characters, rel.target_character_id),
      relation: { player_input: rel.relation, text: '' },
      past_events: splitLines(rel.past_events).map((line) => ({ player_input: line, text: '' })),
      // id 留空：由后端按内容生成稳定且世界内唯一的 id，避免多张关系卡撞 id。
      planned_events: splitLines(rel.planned_events).map((line) => ({ id: '', text: line })),
    })),
  }
}

function fromCharacter(c: CharacterView): FormState {
  const bible = c.profile.bible
  const items = bible?.items || ({} as Partial<Record<ItemKey, BibleItem>>)
  const textOf = (key: ItemKey): string => {
    const it = items[key]
    return it ? (it.text || it.player_input || '') : ''
  }
  return {
    name: c.profile.name,
    core_description: c.profile.core_description,
    appearance: textOf('appearance'),
    personality: textOf('personality'),
    background: textOf('background'),
    major_events: textOf('major_events'),
    relations: (bible?.relations || []).map((rel) => ({
      uid: nextUid(),
      target_character_id: rel.target_character_id,
      relation: rel.relation?.text || rel.relation?.player_input || '',
      past_events: (rel.past_events || []).map((e) => e.text || e.player_input || '').join('\n'),
      planned_events: (rel.planned_events || []).map((e) => e.text || '').join('\n'),
    })),
  }
}

function previewFromForm(form: FormState, characters: CharacterView[]): CustomizePreview {
  const items = {} as Record<ItemKey, BibleItem>
  for (const key of ITEM_KEYS) items[key] = blankItem(form[key])
  return {
    name: form.name,
    core_description: form.core_description,
    items,
    relations: form.relations.map((rel) => ({
      uid: rel.uid,
      target_character_id: rel.target_character_id,
      target_name: targetName(characters, rel.target_character_id),
      relation: blankItem(rel.relation),
      past_events: splitLines(rel.past_events).map(blankItem),
      // id 留空：由后端按内容生成稳定且世界内唯一的 id，避免多张关系卡撞 id。
      planned_events: splitLines(rel.planned_events).map((line) => ({ id: '', text: line })),
    })),
    warnings: [], provider: '手动填写（未调用模型）', model: '', raw_text: '', degraded: false,
  }
}

export default function CharacterCustomizeForm({
  worldId, characters, isProtagonist, onSaved, onGoCreateCharacter, onError, onNotice, editingCharacter,
}: Props) {
  const draftKeyForWorld = draftKey('character', worldId)
  const [form, setForm] = useState<FormState>(() => editingCharacter ? fromCharacter(editingCharacter) : emptyForm())
  const [preview, setPreview] = useState<CustomizePreview | null>(null)
  const [provider, setProvider] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [draftSavedAt, setDraftSavedAt] = useState<number | null>(null)
  const [draftCorrupt, setDraftCorrupt] = useState(false)

  useEffect(() => {
    const draft = readDraft<FormState>(draftKeyForWorld, normalizeCharacterDraft)
    if (draft) { setDraftSavedAt(draft.savedAt); setDraftCorrupt(draft.kind === 'corrupt') }
  }, [draftKeyForWorld])

  useEffect(() => {
    if (!generating) return
    const timer = window.setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => window.clearInterval(timer)
  }, [generating])

  const patch = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const patchRelation = (uid: string, key: keyof RelationForm, value: string) =>
    setForm((prev) => ({
      ...prev,
      relations: prev.relations.map((rel) => rel.uid === uid ? { ...rel, [key]: value } : rel),
    }))

  const addRelation = () => setForm((prev) => ({
    ...prev,
    relations: [...prev.relations, {
      uid: nextUid(), target_character_id: '', relation: '', past_events: '', planned_events: '',
    }],
  }))

  const removeRelation = (uid: string) => setForm((prev) => ({
    ...prev, relations: prev.relations.filter((rel) => rel.uid !== uid),
  }))

  const stashDraft = (): boolean => {
    const ok = saveDraft(draftKeyForWorld, form)
    if (!ok) { onError('暂存失败：浏览器本地存储不可用或空间不足，请先复制走已填写的内容。'); return false }
    setDraftSavedAt(Date.now())
    return true
  }

  const restoreDraft = () => {
    const draft = readDraft<FormState>(draftKeyForWorld, normalizeCharacterDraft)
    if (!draft) { setDraftSavedAt(null); setDraftCorrupt(false); return }
    if (draft.kind === 'corrupt') {
      onError('这份暂存来自旧版本或结构已损坏，无法自动恢复。原文没有被删除，可点「丢弃」清理。')
      return
    }
    setForm(draft.value)
    setPreview(null); setDraftSavedAt(null); setDraftCorrupt(false)
    // 故意保留草稿：内容只回到内存，玩家可能还要再去建另一个人物。
    // 确认保存成功或主动「丢弃」时才清除，中途离开不会丢数据。
    onNotice('已恢复上次暂存的填写，可以在关系下拉里选到新建的角色了。草稿仍保留到保存或丢弃为止。')
  }

  const discardDraft = () => {
    clearDraft(draftKeyForWorld); setDraftSavedAt(null); setDraftCorrupt(false)
  }

  const customize = async () => {
    setBusy(true); setGenerating(true); setElapsed(0); onError(''); setPreview(null); setWarnings([])
    try {
      const result = await api.customizeCharacter(worldId, toInputs(form, characters), isProtagonist)
      // 后端不返回 uid，前端补一个稳定 key，避免编辑时输入框被重建失焦。
      setPreview({
        ...result,
        relations: result.relations.map((rel, index) => ({ ...rel, uid: `prev_${index}_${rel.target_character_id}` })),
      })
      setProvider(result.provider === 'mock' ? 'Mock 演示数据' : `真实模型 ${result.model || result.provider}`)
      setWarnings(result.warnings)
    } catch (e) { onError((e as Error).message) }
    finally { setBusy(false); setGenerating(false) }
  }

  const patchPreviewItem = (key: ItemKey, text: string) =>
    setPreview((prev) => prev ? {
      ...prev, items: { ...prev.items, [key]: { ...prev.items[key], text } },
    } : prev)

  const patchPreviewRelation = (index: number, text: string) =>
    setPreview((prev) => prev ? {
      ...prev,
      relations: prev.relations.map((rel, i) => i === index
        ? { ...rel, relation: { ...rel.relation, text } } : rel),
    } : prev)

  const patchPreviewPastEvent = (relIndex: number, eventIndex: number, text: string) =>
    setPreview((prev) => prev ? {
      ...prev,
      relations: prev.relations.map((rel, i) => i === relIndex
        ? { ...rel, past_events: rel.past_events.map((evt, j) => j === eventIndex ? { ...evt, text } : evt) }
        : rel),
    } : prev)

  const confirm = async () => {
    if (editingCharacter) {
      // 编辑已有角色：保留原玩家原话作为事实锚句，只改定稿。
      const name = (preview?.name || form.name).trim()
      if (!name) { onError('请先给这个角色起个名字'); return }
      setBusy(true); onError('')
      try {
        const source = preview || previewFromForm(form, characters)
        const orig = editingCharacter.profile.bible
        const items = {} as Record<ItemKey, BibleItem>
        for (const key of ITEM_KEYS) {
          items[key] = {
            player_input: orig?.items?.[key]?.player_input || '',
            text: source.items[key].text || source.items[key].player_input || '',
          }
        }
        const relations = source.relations.map((rel) => {
          const origRel = orig?.relations?.find((r) => r.target_character_id === rel.target_character_id)
          return {
            target_character_id: rel.target_character_id,
            target_name: rel.target_name || targetName(characters, rel.target_character_id),
            relation: {
              player_input: origRel?.relation?.player_input || '',
              text: rel.relation.text || rel.relation.player_input || '',
            },
            past_events: rel.past_events.map((e) => ({ player_input: '', text: e.text || e.player_input || '' })),
            planned_events: rel.planned_events.map((e) => ({ id: '', text: e.text || '' })),
          }
        })
        const bible: CharacterBible = { items, relations }
        await api.editCharacter(worldId, editingCharacter.profile.character_id, {
          name, core_description: source.core_description, bible,
        })
        setPreview(null); setForm(emptyForm()); setWarnings([])
        onNotice('已保存修改。保存后的定稿就是后续剧情的设定基线。')
        onSaved()
      } catch (e) { onError((e as Error).message) }
      finally { setBusy(false) }
      return
    }

    if (!preview) return
    if (!preview.name.trim()) { onError('请先给这个角色起个名字'); return }
    setBusy(true); onError('')
    try {
      const payload: GeneratedCharacter = {
        name: preview.name.trim(),
        core_description: preview.core_description,
        custom_fields: {},
        plot_hooks: [],
        opening_situation: '',
        is_protagonist: isProtagonist,
        bible: { items: preview.items, relations: preview.relations },
      }
      const result = await api.confirmCharacters(worldId, [payload], isProtagonist)
      clearDraft(draftKeyForWorld)
      setPreview(null); setForm(emptyForm()); setWarnings([]); setDraftSavedAt(null)
      onNotice(`已保存 ${result.saved.length} 位人物。保存后的定稿就是后续剧情的设定基线。`)
      onSaved()
    } catch (e) { onError((e as Error).message) }
    finally { setBusy(false) }
  }

  const characterOptions = characters.map((c) => ({
    id: c.profile.character_id,
    label: `${c.profile.name}（${c.profile.is_protagonist ? '主角' : 'NPC'}）`,
  }))

  return (
    <div>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="notice" style={{ marginBottom: 14 }}>
          <strong>你说了算。</strong>每一项都可以留空交给 AI 补全；写了内容的条目，AI 会把你的原话当作不可违背的事实锚句，
          只在同一条目里补足你没写到的细节（比如外貌没写常穿服饰，AI 会补）。补全后按同样条目摊开给你精修，
          <strong>保存之后 AI 就以保存的为准</strong>。
        </div>

        {draftSavedAt !== null && (
          <div className="notice" style={{ marginBottom: 14 }}>
            {draftCorrupt
              ? <>发现一份<strong>无法自动恢复</strong>的暂存（{new Date(draftSavedAt).toLocaleTimeString()}），原文没有被删除。</>
              : <>发现未完成的编辑（暂存于 {new Date(draftSavedAt).toLocaleTimeString()}）。</>}
            <div className="btn-row" style={{ marginTop: 8 }}>
              {!draftCorrupt && <button className="btn btn-sm" onClick={restoreDraft}>恢复继续填写</button>}
              <button className="btn btn-sm" onClick={discardDraft}>丢弃</button>
            </div>
            <div className="field-hint" style={{ marginTop: 6 }}>草稿只保存在这台电脑的这个浏览器里。</div>
          </div>
        )}

        <div className="form-row two">
          <div>
            <label className="field-label">角色姓名（必填）</label>
            <input className="input" value={form.name} placeholder="例如：沈知微"
              onChange={(e) => patch('name', e.target.value)} />
          </div>
          <div>
            <label className="field-label">人物定位（一句话，可选）</label>
            <input className="input" value={form.core_description} placeholder="例如：把旧宅钥匙攥了三年的成年大小姐"
              onChange={(e) => patch('core_description', e.target.value)} />
          </div>
        </div>

        {ITEM_KEYS.map((key) => (
          <div key={key} style={{ marginTop: 12 }}>
            <label className="field-label">
              {ITEM_LABELS[key]} <span className="field-hint">（可选，不填由 AI 自主生成；写了就以你为准）</span>
            </label>
            <textarea className="textarea" style={{ minHeight: key === 'major_events' ? 100 : 80 }}
              value={form[key]}
              placeholder={key === 'major_events' ? '每行写一件大事，例如：三年前那场事故之后，她再没回过旧宅' : '写一两句也行，AI 会围绕它补足细节'}
              onChange={(e) => patch(key, e.target.value)} />
          </div>
        ))}

        <h3 className="section-title" style={{ marginTop: 20 }}>与其他角色的关系</h3>
        <div className="field-hint" style={{ marginBottom: 10 }}>
          只能选本世界<strong>已经存在</strong>的角色。若对方还没建，先点「暂存并去新建人物」，建好回来点「恢复」就能在下拉里选到 TA。
        </div>
        {form.relations.length === 0 && <div className="card empty">还没有关系条目，点下面「添加一条关系」开始。</div>}
        {form.relations.map((rel) => (
          <div className="preview-card" key={rel.uid} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
              <div style={{ flex: 1 }}>
                <label className="field-label">和哪个角色</label>
                <select className="select" value={rel.target_character_id}
                  onChange={(e) => patchRelation(rel.uid, 'target_character_id', e.target.value)}>
                  <option value="">{characterOptions.length ? '— 请选择已存在角色 —' : '— 暂无角色，请先创建 —'}</option>
                  {characterOptions.map((opt) => <option key={opt.id} value={opt.id}>{opt.label}</option>)}
                </select>
              </div>
              <button className="btn btn-sm" onClick={() => removeRelation(rel.uid)} aria-label="删除这条关系">
                <Trash2 size={13} />
              </button>
            </div>
            {rel.target_character_id && !characterOptions.some((o) => o.id === rel.target_character_id) && (
              <div className="error-banner" style={{ marginTop: 6 }}>
                这个角色已不存在（恢复草稿时对方可能还没建好），请重新在下拉里选一次。
              </div>
            )}
            <label className="field-label" style={{ marginTop: 10 }}>两人的关系</label>
            <textarea className="textarea" style={{ minHeight: 60 }} value={rel.relation}
              onChange={(e) => patchRelation(rel.uid, 'relation', e.target.value)} />
            <label className="field-label" style={{ marginTop: 10 }}>两人间已发生的关键事件（每行一条）</label>
            <textarea className="textarea" style={{ minHeight: 70 }} value={rel.past_events}
              placeholder="这些是既成事实，剧情只能引用不能改写"
              onChange={(e) => patchRelation(rel.uid, 'past_events', e.target.value)} />
            <label className="field-label" style={{ marginTop: 10 }}>预定故事中将发生的事件（每行一条）</label>
            <textarea className="textarea" style={{ minHeight: 70 }} value={rel.planned_events}
              placeholder="AI 写两人故事时会优先围绕这些事件落笔、展开成完整场景，再自己发挥"
              onChange={(e) => patchRelation(rel.uid, 'planned_events', e.target.value)} />
            <div className="field-hint" style={{ marginTop: 6 }}>预定事件清单只有你能定，AI 不会改写或新增。</div>
          </div>
        ))}
        <button className="btn btn-sm" onClick={addRelation} disabled={characterOptions.length === 0}>
          <Plus size={13} /> 添加一条关系
        </button>

        <div className="btn-row" style={{ marginTop: 16, flexWrap: 'wrap' }}>
          <button className="btn btn-primary" disabled={busy} onClick={() => void customize()}>
            <Sparkles size={14} /> {generating ? `AI 补全中…（已等待 ${elapsed} 秒）` : 'AI 补全我没写透的部分'}
          </button>
          <button className="btn" disabled={busy}
            onClick={() => { setPreview(previewFromForm(form, characters)); setProvider('手动填写（未调用模型）'); setWarnings([]) }}>
            不调用 AI，直接用我填的保存
          </button>
          <button className="btn" disabled={busy} onClick={() => { if (stashDraft()) onNotice('已暂存当前填写。') }}>
            <Save size={14} /> 暂存当前修改
          </button>
          <button className="btn" disabled={busy} onClick={() => {
            if (!stashDraft()) return
            onNotice('已暂存。新建好角色后回到这里，点「恢复继续填写」就能在关系下拉里选到 TA。')
            onGoCreateCharacter()
          }}><Users size={14} /> 暂存并去新建人物</button>
        </div>
        <div className="field-hint" style={{ marginTop: 8 }}>
          一次调用补全全部条目。每一项都会补充，即使你只写了一两句；越详实的设定，之后展开的故事越细致。
        </div>
      </div>

      {preview && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <h3 className="section-title" style={{ marginTop: 0 }}>设定预览与精修</h3>
            <span className="chip plain">来源：{provider}</span>
          </div>
          <div className="notice" style={{ marginBottom: 14 }}>
            下面是补全结果。<strong>「你定的」是玩家原话</strong>（事实锚句，只读），下面文本框是 AI 补全的定稿，可以逐项改；
            确认保存之后，这个定稿就是后续剧情的唯一依据。
          </div>
          {preview.degraded && (
            <div className="error-banner" style={{ marginBottom: 12 }}>
              这次结果是<strong>降级内容</strong>：模型输出被截断、格式异常或补全失败，下面只保证你填过的原话不丢。
              请逐项检查并手动补完再保存。
            </div>
          )}
          {warnings.length > 0 && (
            <div className="error-banner" style={{ marginBottom: 12 }}>校验提示：{warnings.join('；')}</div>
          )}
          {preview.raw_text && (
            <details style={{ marginBottom: 14 }}>
              <summary className="field-hint" style={{ cursor: 'pointer' }}>
                查看模型原始输出（排错 / 手动救回被截断的内容）
              </summary>
              <pre style={{
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260,
                overflow: 'auto', padding: 10, marginTop: 8, borderRadius: 6,
                border: '1px solid var(--border, #444)', fontSize: 12, lineHeight: 1.6,
              }}>{preview.raw_text}</pre>
            </details>
          )}

          <div className="form-row two">
            <div>
              <label className="field-label">角色姓名</label>
              <input className="input" value={preview.name}
                onChange={(e) => setPreview((prev) => prev ? { ...prev, name: e.target.value } : prev)} />
            </div>
            <div>
              <label className="field-label">人物定位</label>
              <input className="input" value={preview.core_description}
                onChange={(e) => setPreview((prev) => prev ? { ...prev, core_description: e.target.value } : prev)} />
            </div>
          </div>

          {ITEM_KEYS.map((key) => {
            const item = preview.items[key]
            return (
              <div key={key} style={{ marginTop: 12 }}>
                <label className="field-label">{ITEM_LABELS[key]}</label>
                {item.player_input && (
                  <div className="notice" style={{ marginBottom: 6 }}>
                    <strong>你定的：</strong>{item.player_input}
                  </div>
                )}
                <div className="field-hint" style={{ marginBottom: 4 }}>AI 补的 / 定稿（可改）</div>
                <textarea className="textarea" style={{ minHeight: key === 'major_events' ? 140 : 110 }}
                  value={item.text} onChange={(e) => patchPreviewItem(key, e.target.value)} />
              </div>
            )
          })}

          {preview.relations.map((rel, relIndex) => (
            <div className="preview-card" key={rel.uid || relIndex} style={{ marginTop: 14 }}>
              <h3 className="section-title" style={{ marginTop: 0 }}>
                与 {rel.target_name || rel.target_character_id || '未选择角色'} 的关系
              </h3>
              {rel.relation.player_input && (
                <div className="notice" style={{ marginBottom: 8 }}><strong>你定的：</strong>{rel.relation.player_input}</div>
              )}
              <label className="field-label">关系设定（可改）</label>
              <textarea className="textarea" style={{ minHeight: 100 }} value={rel.relation.text}
                onChange={(e) => patchPreviewRelation(relIndex, e.target.value)} />
              {rel.past_events.map((evt, eventIndex) => (
                <div key={eventIndex} style={{ marginTop: 10 }}>
                  <label className="field-label">已发生关键事件 {eventIndex + 1}</label>
                  {evt.player_input && <div className="notice" style={{ marginBottom: 6 }}><strong>你定的：</strong>{evt.player_input}</div>}
                  <textarea className="textarea" style={{ minHeight: 80 }} value={evt.text}
                    onChange={(e) => patchPreviewPastEvent(relIndex, eventIndex, e.target.value)} />
                </div>
              ))}
              {rel.planned_events.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <label className="field-label">预定将发生的事件（只有你能定，AI 不改写）</label>
                  {rel.planned_events.map((evt: PlannedEvent) => (
                    <div className="field-hint" key={evt.id || evt.text} style={{ marginTop: 4 }}>· {evt.text}</div>
                  ))}
                  <div className="field-hint" style={{ marginTop: 6 }}>保存后，可在「人物列表」里逐条标记已完成。</div>
                </div>
              )}
            </div>
          ))}

          <div className="btn-row" style={{ marginTop: 16 }}>
            <button className="btn btn-primary" disabled={busy} onClick={() => void confirm()}>
              <Save size={14} /> {busy ? '写入人物文件中…' : '确认保存为人物文件'}
            </button>
            <span className="field-hint">确认前不会写任何角色文件。</span>
          </div>
        </div>
      )}
    </div>
  )
}

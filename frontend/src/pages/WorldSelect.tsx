import { useEffect, useState } from 'react'
import { Plus, Save, Sparkles, X } from 'lucide-react'
import WorldCard from '../components/WorldCard'
import { api } from '../services/api'
import { clearDraft, draftKey, readDraft, saveDraft } from '../utils/draft'
import type { Choice, GeneratedWorld, WorldSummary } from '../types'

interface Props { onEnter: (id: string, name: string) => void }

/** 三条入口：AI 一句话、自定义 + AI 补全、完全自己写（不调模型）。 */
type Mode = 'ai' | 'custom' | 'manual'

const DRAFT = draftKey('world', 'new')

/** 旧版本草稿可能缺字段或字段类型不对：能补就补，别把玩家写的长文判成垃圾删掉。 */
const normalizeWorldDraft = (value: unknown): GeneratedWorld | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const draft = value as Record<string, unknown>
  const base = emptyWorld()
  const text = (key: keyof GeneratedWorld): string =>
    typeof draft[key] === 'string' ? draft[key] as string : base[key] as string
  // opening_choices 会走 .map / .filter；不是数组才真正没法恢复。
  if (!Array.isArray(draft.opening_choices)) return null
  const choices: Choice[] = draft.opening_choices.map((choice, index) => {
    const row = (choice && typeof choice === 'object' && !Array.isArray(choice))
      ? choice as Record<string, unknown> : {}
    return {
      id: typeof row.id === 'string' && row.id ? row.id : 'ABCD'[index] || `选项${index + 1}`,
      text: typeof row.text === 'string' ? row.text : '',
    }
  })
  return {
    name: text('name'), genre: text('genre'), description: text('description'),
    setting: text('setting'), rules: text('rules'),
    opening_location: text('opening_location'), opening_scene: text('opening_scene'),
    protagonist_hint: text('protagonist_hint'), writer_style: text('writer_style'),
    opening_choices: choices.length ? choices : base.opening_choices,
    npc_hints: Array.isArray(draft.npc_hints)
      ? draft.npc_hints.filter((h): h is string => typeof h === 'string') : [],
  }
}

const emptyWorld = (): GeneratedWorld => ({
  name: '', genre: '', description: '', setting: '', rules: '',
  opening_location: '', opening_scene: '',
  opening_choices: [
    { id: 'A', text: '' }, { id: 'B', text: '' },
    { id: 'C', text: '' }, { id: 'D', text: '' },
  ],
  protagonist_hint: '', npc_hints: [], writer_style: '',
})

export default function WorldSelect({ onEnter }: Props) {
  const [worlds, setWorlds] = useState<WorldSummary[]>([])
  const [creating, setCreating] = useState(false)
  const [mode, setMode] = useState<Mode>('ai')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [genre, setGenre] = useState('')
  const [idea, setIdea] = useState('')
  const [genreHint, setGenreHint] = useState('')
  const [customForm, setCustomForm] = useState<GeneratedWorld>(emptyWorld)
  const [draftSavedAt, setDraftSavedAt] = useState<number | null>(null)
  const [draftCorrupt, setDraftCorrupt] = useState(false)
  const [preview, setPreview] = useState<GeneratedWorld | null>(null)
  // 双保险：预览记住自己来自哪条流程，避免任何路径下确认到别的模式的数据。
  const [previewMode, setPreviewMode] = useState<Mode>('ai')
  const [provider, setProvider] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  // 降级结果必须可见：模型原文给玩家留一份，方便手动补完或排错。
  const [degraded, setDegraded] = useState(false)
  const [rawText, setRawText] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [elapsed, setElapsed] = useState(0)

  /** 切换创建模式时必须丢掉另一条流程的预览，否则「确认创建」会提交错的内容。 */
  const switchMode = (next: Mode) => {
    if (next === mode) return
    setMode(next)
    setPreview(null); setWarnings([]); setProvider(''); setError('')
    setDegraded(false); setRawText('')
  }

  const load = () => {
    api.listWorlds().then((r) => setWorlds(r.worlds)).catch((e) => setError((e as Error).message))
  }
  useEffect(load, [])
  useEffect(() => {
    const draft = readDraft<GeneratedWorld>(DRAFT, normalizeWorldDraft)
    if (draft) { setDraftSavedAt(draft.savedAt); setDraftCorrupt(draft.kind === 'corrupt') }
  }, [])

  // 长文生成本来就要等模型一个字一个字写出来；给个计时，避免看起来像卡死。
  useEffect(() => {
    if (!generating) return
    const timer = window.setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => window.clearInterval(timer)
  }, [generating])

  const createManual = async () => {
    if (!name.trim()) { setError('请给世界起个名字'); return }
    setBusy(true); setError('')
    try {
      const world = await api.createWorld(name.trim(), description.trim(), genre.trim())
      resetForm(); load(); onEnter(world.world_id, world.name)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  /** 一句话生成：让模型把一句话扩写成完整世界观，只返回预览，不落盘。 */
  const generate = async () => {
    if (!idea.trim()) { setError('先写一句你想到的世界设定'); return }
    setBusy(true); setGenerating(true); setElapsed(0); setError(''); setPreview(null)
    setWarnings([]); setDegraded(false); setRawText('')
    try {
      const result = await api.generateWorld(idea.trim(), genreHint.trim())
      setPreview({ ...result.preview, writer_style: result.preview.writer_style || '' })
      setPreviewMode('ai')
      setProvider(result.provider === 'mock' ? 'Mock 演示数据' : `真实模型 ${result.model || result.provider}`)
      setWarnings(result.warnings)
      setDegraded(Boolean(result.degraded) || result.warnings.length > 0)
      setRawText(result.raw_text || '')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false); setGenerating(false) }
  }

  /** 自定义 + AI 补全：一次调用把每个玩家填过的条目补写详实，仍只返回预览。 */
  const customize = async () => {
    setBusy(true); setGenerating(true); setElapsed(0); setError(''); setPreview(null)
    setWarnings([]); setDegraded(false); setRawText('')
    try {
      const result = await api.customizeWorld(customForm as unknown as Record<string, unknown>)
      setPreview({ ...result.preview, writer_style: result.preview.writer_style || customForm.writer_style })
      setPreviewMode('custom')
      setProvider(result.provider === 'mock' ? 'Mock 演示数据' : `真实模型 ${result.model || result.provider}`)
      setWarnings(result.warnings)
      setDegraded(Boolean(result.degraded))
      setRawText(result.raw_text || '')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false); setGenerating(false) }
  }

  /** 完全不依赖模型：直接用玩家填写的内容创建。 */
  const createFromCustom = async () => {
    if (!customForm.name.trim()) { setError('请给世界起个名字'); return }
    setBusy(true); setError('')
    try {
      const world = await api.createWorld(customForm.name.trim(), customForm.description.trim(), customForm.genre.trim(), {
        setting: customForm.setting,
        rules: customForm.rules,
        opening_scene: customForm.opening_scene,
        opening_location: customForm.opening_location,
        opening_choices: customForm.opening_choices.filter((c) => c.text.trim()),
        writer_style: customForm.writer_style,
      })
      clearDraft(DRAFT); resetForm(); load(); onEnter(world.world_id, world.name)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  /** 第二步：用户检查、编辑完预览之后才创建世界目录并写入 setting.md / rules.md。 */
  const confirm = async () => {
    if (!preview) return
    if (previewMode !== mode) {
      setError('预览来自另一个创建模式，已阻止提交。请切回对应模式或重新生成。')
      return
    }
    setBusy(true); setError('')
    try {
      const world = await api.confirmGeneratedWorld(preview)
      clearDraft(DRAFT); resetForm(); load(); onEnter(world.world_id, world.name)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const resetForm = () => {
    setCreating(false); setName(''); setDescription(''); setGenre('')
    setIdea(''); setGenreHint(''); setCustomForm(emptyWorld())
    setPreview(null); setPreviewMode('ai'); setWarnings([]); setProvider('')
    setDegraded(false); setRawText('')
  }

  const patch = <K extends keyof GeneratedWorld>(key: K, value: GeneratedWorld[K]) => {
    setPreview((prev) => prev ? { ...prev, [key]: value } : prev)
  }

  const patchCustom = <K extends keyof GeneratedWorld>(key: K, value: GeneratedWorld[K]) => {
    setCustomForm((prev) => ({ ...prev, [key]: value }))
  }

  const patchChoice = (index: number, text: string) => {
    if (!preview) return
    const next: Choice[] = preview.opening_choices.map((c, i) => i === index ? { ...c, text } : c)
    patch('opening_choices', next)
  }

  const patchCustomChoice = (index: number, text: string) => {
    const next: Choice[] = customForm.opening_choices.map((c, i) => i === index ? { ...c, text } : c)
    patchCustom('opening_choices', next)
  }

  const stashDraft = () => {
    const ok = saveDraft(DRAFT, customForm)
    if (!ok) { setError('暂存失败：浏览器本地存储不可用或空间不足，请先复制走已填写的内容。'); return }
    setDraftSavedAt(Date.now())
    setNotice('已暂存当前填写。可以放心离开去新建人物，回来点「恢复」继续。')
  }

  const restoreDraft = () => {
    const draft = readDraft<GeneratedWorld>(DRAFT, normalizeWorldDraft)
    if (!draft) { setDraftSavedAt(null); setDraftCorrupt(false); return }
    if (draft.kind === 'corrupt') {
      setError('这份暂存来自旧版本或结构已损坏，无法自动恢复。原文仍保留在浏览器里，可到开发者工具查阅，或点「丢弃」清理。')
      return
    }
    setCustomForm(draft.value)
    switchMode('custom'); setCreating(true); setDraftSavedAt(null); setDraftCorrupt(false)
    // 故意不立刻删草稿：内容只回到内存里，玩家可能还要再去建一个人物。
    // 留着这份兜底，直到「确认创建 / 直接创建」成功或玩家主动丢弃才清除。
    setNotice('已恢复上次暂存的填写内容。草稿仍保留着，保存成功或点「丢弃」才会清除。')
  }

  const discardDraft = () => {
    clearDraft(DRAFT); setDraftSavedAt(null); setDraftCorrupt(false); setNotice('已丢弃暂存。')
  }

  const remove = async (id: string, worldName: string) => {
    if (!window.confirm(`确定删除世界「${worldName}」吗？该世界的人物、剧情、记忆和所有存档都会一起删除，且无法恢复。`)) return
    try { await api.deleteWorld(id); load() }
    catch (e) { setError((e as Error).message) }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">你的世界</h1>
          <p className="page-sub">每个世界有独立的人物、记忆、面板、存档和写手风格。切换之后可以回来接着写。</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(!creating)}>
          {creating ? <><X size={14} /> 取消</> : <><Plus size={14} /> 创建世界</>}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="notice">{notice}</div>}
      {draftSavedAt !== null && (
        <div className="notice">
          {draftCorrupt
            ? <>发现一份<strong>无法自动恢复</strong>的暂存（{new Date(draftSavedAt).toLocaleTimeString()}）。它可能是旧版本留下的，原文没有被删除。</>
            : <>发现未完成的世界设定（暂存于 {new Date(draftSavedAt).toLocaleTimeString()}）。</>}
          <div className="btn-row" style={{ marginTop: 8 }}>
            {!draftCorrupt && <button className="btn btn-sm" onClick={restoreDraft}>恢复继续填写</button>}
            <button className="btn btn-sm" onClick={discardDraft}>丢弃</button>
          </div>
          <div className="field-hint" style={{ marginTop: 6 }}>草稿只保存在这台电脑的这个浏览器里，换设备不会同步。</div>
        </div>
      )}

      {creating && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="tabs" style={{ marginBottom: 14 }}>
            <button className={`tab ${mode === 'ai' ? 'active' : ''}`} onClick={() => switchMode('ai')}>
              <Sparkles size={13} /> 一句话生成
            </button>
            <button className={`tab ${mode === 'custom' ? 'active' : ''}`} onClick={() => switchMode('custom')}>
              自定义 + AI 补全
            </button>
            <button className={`tab ${mode === 'manual' ? 'active' : ''}`} onClick={() => switchMode('manual')}>完全自己写</button>
          </div>

          {mode === 'manual' && (
            <>
              <div className="form-row two">
                <div>
                  <label className="field-label">世界名称</label>
                  <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：雾港旧事" />
                </div>
                <div>
                  <label className="field-label">类型 / 基调（可选）</label>
                  <input className="input" value={genre} onChange={(e) => setGenre(e.target.value)} placeholder="例如：近现代 · 悬疑" />
                </div>
              </div>
              <div className="form-row">
                <div>
                  <label className="field-label">简介（可选）</label>
                  <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="一句话说清这个世界" />
                </div>
              </div>
              <button className="btn btn-primary" disabled={busy} onClick={() => void createManual()}>
                {busy ? '创建中…' : '创建并进入'}
              </button>
              <div className="field-hint" style={{ marginTop: 8 }}>
                只写元信息，不调用任何模型。世界观设定是空的，之后可在文件里补，或用「自定义 + AI 补全」重来。
              </div>
            </>
          )}

          {mode === 'ai' && (
            <>
              <label className="field-label">用一句话说清你想要的世界</label>
              <textarea className="textarea" style={{ minHeight: 76 }} value={idea}
                placeholder="例如：现代校园，一个大三男生和几位性格完全不同的成年女性之间的故事，基调偏甜"
                onChange={(e) => setIdea(e.target.value)} />
              <div className="form-row" style={{ marginTop: 10 }}>
                <div>
                  <label className="field-label">类型 / 基调补充（可选）</label>
                  <input className="input" value={genreHint} onChange={(e) => setGenreHint(e.target.value)}
                    placeholder="例如：现代校园 · 群像 · 甜向" />
                </div>
              </div>
              <div className="btn-row" style={{ marginTop: 12 }}>
                <button className="btn btn-primary" disabled={busy} onClick={() => void generate()}>
                  <Sparkles size={14} /> {generating ? `模型扩写中…（已等待 ${elapsed} 秒）` : '生成世界观预览'}
                </button>
                {preview && (
                  <button className="btn" disabled={busy} onClick={() => { setPreview(null); setWarnings([]) }}>重新生成</button>
                )}
              </div>
              <div className="field-hint" style={{ marginTop: 8 }}>
                生成后<strong>先展示预览、全部可改</strong>，你点「确认创建」才会写入文件。
                <br />
                本地 9B 模型逐字写这么长一段，显卡正常时通常 30–90 秒；若超过 2 分钟还没结果，
                多半是模型服务没吃到显卡（退回纯 CPU，慢十几倍），可到「设置 → Test Connection」看实测速度。
              </div>
            </>
          )}

          {mode === 'custom' && (
            <>
              <div className="notice" style={{ marginBottom: 14 }}>
                <strong>你说了算。</strong>每一项都可以留空交给 AI 补全；写了内容的条目，AI 会把你的原话当作不可违背的事实锚句，
                只在同一条目里<strong>补足你没写到的细节</strong>（例如外貌里没写常穿服饰，AI 会补）。补全后按同样条目摊开给你精修，
                保存的定稿之后就是唯一有效的设定。
              </div>
              <div className="form-row two">
                <div>
                  <label className="field-label">世界名称（不填则由 AI 起）</label>
                  <input className="input" value={customForm.name} onChange={(e) => patchCustom('name', e.target.value)} placeholder="例如：雨季来信" />
                </div>
                <div>
                  <label className="field-label">类型 / 基调</label>
                  <input className="input" value={customForm.genre} onChange={(e) => patchCustom('genre', e.target.value)} placeholder="例如：现代校园 · 甜向" />
                </div>
              </div>
              <div className="form-row">
                <div>
                  <label className="field-label">简介</label>
                  <input className="input" value={customForm.description} onChange={(e) => patchCustom('description', e.target.value)} placeholder="一句话说清这个世界的起点" />
                </div>
              </div>
              <label className="field-label" style={{ marginTop: 8 }}>世界观背景（可选项，写了就以你为准）</label>
              <textarea className="textarea" style={{ minHeight: 120 }} value={customForm.setting}
                placeholder="时代、地理、势力、日常见闻、核心矛盾……写一两句也行，AI 会围绕它扩写"
                onChange={(e) => patchCustom('setting', e.target.value)} />
              <label className="field-label" style={{ marginTop: 12 }}>世界规则</label>
              <textarea className="textarea" style={{ minHeight: 100 }} value={customForm.rules}
                placeholder="不可违背的硬规则、代价、禁忌……留空由 AI 补"
                onChange={(e) => patchCustom('rules', e.target.value)} />
              <div className="form-row two" style={{ marginTop: 12 }}>
                <div>
                  <label className="field-label">开场地点</label>
                  <input className="input" value={customForm.opening_location} onChange={(e) => patchCustom('opening_location', e.target.value)} />
                </div>
                <div>
                  <label className="field-label">写手风格（这个世界专属的 System Prompt，可选）</label>
                  <input className="input" value={customForm.writer_style}
                    placeholder="例如：克制、慢节奏、偏心理描写；对白短，不用感叹号"
                    onChange={(e) => patchCustom('writer_style', e.target.value)} />
                </div>
              </div>
              <div className="field-hint" style={{ marginTop: 6 }}>
                全局基础写作规则永远在前，这里只追加本世界的文风；创建后可在 Prompt Studio 里随时改，下一轮生成立即生效。
              </div>
              <label className="field-label" style={{ marginTop: 12 }}>开场画面</label>
              <textarea className="textarea" style={{ minHeight: 90 }} value={customForm.opening_scene}
                placeholder="第二人称，只写环境、在场人物和一个悬念，不替玩家做动作"
                onChange={(e) => patchCustom('opening_scene', e.target.value)} />
              <label className="field-label" style={{ marginTop: 12 }}>四个开场选项（可只填一两个，其余交给 AI）</label>
              {customForm.opening_choices.map((choice, index) => (
                <div className="form-row" key={choice.id} style={{ marginBottom: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="chip plain">{choice.id}</span>
                    <input className="input" value={choice.text} onChange={(e) => patchCustomChoice(index, e.target.value)} />
                  </div>
                </div>
              ))}
              <div className="btn-row" style={{ marginTop: 14 }}>
                <button className="btn btn-primary" disabled={busy} onClick={() => void customize()}>
                  <Sparkles size={14} /> {generating ? `AI 补全中…（已等待 ${elapsed} 秒）` : 'AI 补全我没写透的部分'}
                </button>
                <button className="btn" disabled={busy} onClick={() => void createFromCustom()}>不调用 AI，直接用我填的创建</button>
                <button className="btn" disabled={busy} onClick={stashDraft}><Save size={14} /> 暂存当前填写</button>
              </div>
              <div className="field-hint" style={{ marginTop: 8 }}>
                一次调用补全所有条目。想先去建好人物再回来接着填，点「暂存当前填写」即可。
              </div>
            </>
          )}
        </div>
      )}

      {/* 预览区独立于创建面板，长文设定有足够空间展示与编辑 */}
      {mode !== 'manual' && preview && previewMode === mode && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <h3 className="section-title" style={{ marginTop: 0 }}>设定预览与精修</h3>
            <span className="chip plain">来源：{provider}</span>
          </div>
          <div className="notice" style={{ marginBottom: 14 }}>
            {mode === 'custom'
              ? <>下面是 AI 在你填写的条目上做的<strong>补全</strong>。你写过的原话会作为事实锚句保留，每一项都能直接改；确认后才会写入文件。</>
              : <>下面是模型对你那句话的<strong>完整扩写</strong>，不是原话回显。每一项都可以直接改；确认后才会写成文件。</>}
          </div>
          {degraded && (
            <div className="error-banner" style={{ marginBottom: 12 }}>
              这次结果是<strong>降级内容</strong>：模型输出被截断、格式异常或补全失败，下面只保证你填过的原话不丢。
              请逐项检查并手动补完再确认，别把占位文字当成完整设定。
            </div>
          )}
          {warnings.length > 0 && (
            <div className="error-banner" style={{ marginBottom: 12 }}>
              校验提示：{warnings.join('；')}
            </div>
          )}
          {rawText && (
            <details style={{ marginBottom: 14 }}>
              <summary className="field-hint" style={{ cursor: 'pointer' }}>
                查看模型原始输出（排错 / 手动救回被截断的内容）
              </summary>
              <pre style={{
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260,
                overflow: 'auto', padding: 10, marginTop: 8, borderRadius: 6,
                border: '1px solid var(--border, #444)', fontSize: 12, lineHeight: 1.6,
              }}>{rawText}</pre>
            </details>
          )}

          <div className="form-row two">
            <div>
              <label className="field-label">世界名称</label>
              <input className="input" value={preview.name} onChange={(e) => patch('name', e.target.value)} />
            </div>
            <div>
              <label className="field-label">类型 / 基调</label>
              <input className="input" value={preview.genre} onChange={(e) => patch('genre', e.target.value)} />
            </div>
          </div>
          <div className="form-row">
            <div>
              <label className="field-label">简介</label>
              <input className="input" value={preview.description} onChange={(e) => patch('description', e.target.value)} />
            </div>
          </div>

          <label className="field-label" style={{ marginTop: 8 }}>世界观背景（写进 setting.md）</label>
          <textarea className="textarea" style={{ minHeight: 200 }} value={preview.setting}
            onChange={(e) => patch('setting', e.target.value)} />

          <label className="field-label" style={{ marginTop: 12 }}>世界规则（写进 rules.md）</label>
          <textarea className="textarea" style={{ minHeight: 140 }} value={preview.rules}
            onChange={(e) => patch('rules', e.target.value)} />

          <div className="form-row two" style={{ marginTop: 12 }}>
            <div>
              <label className="field-label">开场地点</label>
              <input className="input" value={preview.opening_location}
                onChange={(e) => patch('opening_location', e.target.value)} />
            </div>
            <div>
              <label className="field-label">主角设定建议（仅提示，不自动建人物）</label>
              <input className="input" value={preview.protagonist_hint}
                onChange={(e) => patch('protagonist_hint', e.target.value)} />
            </div>
          </div>

          <label className="field-label" style={{ marginTop: 12 }}>写手风格（写进本世界的 system_prompt.md）</label>
          <textarea className="textarea" style={{ minHeight: 70 }} value={preview.writer_style}
            placeholder="只写风格，全局基础写作规则会自动放在它前面"
            onChange={(e) => patch('writer_style', e.target.value)} />

          <label className="field-label" style={{ marginTop: 12 }}>开场画面（进入世界时显示）</label>
          <textarea className="textarea" style={{ minHeight: 110 }} value={preview.opening_scene}
            onChange={(e) => patch('opening_scene', e.target.value)} />

          <label className="field-label" style={{ marginTop: 12 }}>四个开场选项</label>
          {preview.opening_choices.map((choice, index) => (
            <div className="form-row" key={choice.id + index} style={{ marginBottom: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="chip plain">{choice.id}</span>
                <input className="input" value={choice.text} onChange={(e) => patchChoice(index, e.target.value)} />
              </div>
            </div>
          ))}

          {preview.npc_hints.length > 0 && (
            <div className="field-hint" style={{ marginTop: 10 }}>
              NPC 方向建议：{preview.npc_hints.join('；')}
            </div>
          )}

          <div className="btn-row" style={{ marginTop: 16 }}>
            <button className="btn btn-primary" disabled={busy} onClick={() => void confirm()}>
              {busy ? '写入世界文件中…' : '确认创建世界并进入'}
            </button>
            <span className="field-hint">确认前不会在 worlds/ 下创建任何目录。</span>
          </div>
        </div>
      )}

      {worlds.length === 0 ? (
        <div className="empty">还没有世界，点右上角创建一个。</div>
      ) : (
        <div className="world-grid">
          {worlds.map((world) => (
            <WorldCard key={world.world_id} world={world} onEnter={onEnter} onDelete={(id, n) => void remove(id, n)} />
          ))}
        </div>
      )}
    </div>
  )
}

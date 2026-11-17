import { useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Save, Trash2 } from 'lucide-react'
import { api } from '../services/api'
import type { FieldDefinition, FieldType, PanelsPayload, UpdatePolicy } from '../types'

const TYPES: FieldType[] = ['text', 'long_text', 'number', 'boolean', 'enum', 'list', 'key_value']
const TYPE_LABELS: Record<FieldType, string> = {
  text: '单行文本', long_text: '多行文本', number: '数值', boolean: '是 / 否',
  enum: '预设选项', list: '列表', key_value: '键值信息',
}
const POLICIES: { value: UpdatePolicy; label: string }[] = [
  { value: 'story_driven', label: '随剧情更新' },
  { value: 'mostly_static', label: '尽量不变' },
  { value: 'static', label: '固定设定（LLM 不可改）' },
]

type Group = 'protagonist' | 'npc'

interface Props { worldId: string; onChanged?: () => void }

/** 给每个字段补一个前端专用的稳定 key。
 *  以前用 field.id 当 key，而玩家在 ID 输入框每敲一个字母 id 就变，
 *  React 会整块重建编辑器、输入框失焦，于是只能一个字母一个字母地重新点。
 *  uid 不随任何输入变化，且不会发给后端（后端按白名单过滤）。
 */
function withUids(data: PanelsPayload): PanelsPayload {
  const ensure = (f: FieldDefinition): FieldDefinition => ({
    ...f, uid: f.uid || f.id || `new_${Math.random().toString(36).slice(2, 9)}`,
  })
  return {
    ...data,
    schema: {
      ...data.schema,
      protagonist: { fields: data.schema.protagonist.fields.map(ensure) },
      npc: { fields: data.schema.npc.fields.map(ensure) },
    },
  }
}

export default function PanelDesigner({ worldId, onChanged }: Props) {
  const [payload, setPayload] = useState<PanelsPayload | null>(null)
  const [group, setGroup] = useState<Group>('npc')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [baseline, setBaseline] = useState('')
  const [overrideTarget, setOverrideTarget] = useState('')

  useEffect(() => {
    setWarnings([])
    setBaseline('')
    api.getPanels(worldId)
      .then((data) => {
        const next = withUids(data)
        setPayload(next)
        setBaseline(JSON.stringify(next.schema))
      })
      .catch((e) => setError((e as Error).message))
  }, [worldId])

  // 未保存的改动在刷新页面时会丢失，这里明确拦一下，避免「以为保存了」。
  const dirty = !!payload && !!baseline && JSON.stringify(payload.schema) !== baseline
  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  const fields = payload?.schema[group].fields || []

  const setFields = (next: FieldDefinition[]) => {
    if (!payload) return
    setPayload({
      ...payload,
      schema: { ...payload.schema, [group]: { fields: next } },
    })
  }

  const addField = () => {
    // id 留空：保存时后端按字段名自动生成一个合法、稳定的 ID。
    setFields([...fields, {
      id: '', uid: `new_${Math.random().toString(36).slice(2, 9)}`,
      label: '新字段', description: '在这里写清这个字段到底指什么，LLM 会读它',
      type: 'text', visible: true, update_policy: 'story_driven', options: [],
    }])
  }

  const addOverride = () => {
    if (!payload || !overrideTarget) return
    const overrides = { ...(payload.schema.overrides || {}) } as Record<string, { add_fields: FieldDefinition[] }>
    const existing = overrides[overrideTarget] || { add_fields: [] }
    overrides[overrideTarget] = {
      ...existing,
      add_fields: [...(existing.add_fields || []), {
        id: `only_${overrideTarget}_${(existing.add_fields || []).length + 1}`,
        label: `${overrideTarget} 专属字段`, description: '仅对这个角色生效',
        type: 'text', visible: true, update_policy: 'story_driven',
      }],
    }
    setPayload({ ...payload, schema: { ...payload.schema, overrides } })
    setMessage(`已为 ${overrideTarget} 添加专属字段，记得点保存。`)
  }

  const save = async () => {
    if (!payload) return
    setSaving(true)
    setError('')
    setMessage('')
    setWarnings([])
    try {
      const result = await api.savePanels(worldId, payload.schema)
      const next = withUids(result.panels)
      setPayload(next)
      setBaseline(JSON.stringify(next.schema))
      setWarnings(result.warnings || [])
      const added = result.added_fields.length
        ? `新增 ${result.added_fields.length} 个字段，已按已有剧情自动补齐初始值。`
        : '面板配置已保存，前端已自动更新。'
      setMessage(`已写入 ${worldId}/ui/panels.yaml。${added}`)
      onChanged?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (!payload) return <div className="loading">{error || '正在读取面板配置…'}</div>

  const npcIds = Object.entries(payload.characters)
    .filter(([, data]) => !data.is_protagonist)
    .map(([id]) => id)

  return (
    <div>
      <div className="tabs">
        <button className={`tab ${group === 'npc' ? 'active' : ''}`} onClick={() => setGroup('npc')}>NPC 面板</button>
        <button className={`tab ${group === 'protagonist' ? 'active' : ''}`} onClick={() => setGroup('protagonist')}>主角面板</button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {message && <div className="notice">{message}</div>}
      {warnings.length > 0 && (
        <div className="error-banner">
          保存时的自动修正：{warnings.join('；')}
        </div>
      )}

      {/* 保存按钮置顶：字段多时不用滚到页面底部才能落盘 */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 5, display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', gap: 10, flexWrap: 'wrap',
        padding: '10px 12px', marginBottom: 14, border: '1px solid var(--line, #ddd)',
        borderRadius: 8, background: 'var(--card-bg, #fff)',
      }}>
        <span style={{ fontSize: 13, color: dirty ? '#b45309' : '#4b7a52' }}>
          {dirty ? '● 有未保存的修改，刷新页面会丢失' : '✓ 当前配置已保存'}
        </span>
        <button className="btn btn-primary btn-sm" disabled={saving || !dirty} onClick={() => void save()}>
          <Save size={13} /> {saving ? '保存中…' : '保存并应用'}
        </button>
      </div>

      <div className="switch-row">
        <div>
          <div style={{ fontSize: 13.5 }}>所有 NPC 使用同一套字段</div>
          <div className="field-hint">关掉之后，可以为单个角色单独加字段（下方「角色专属字段」）。</div>
        </div>
        <input type="checkbox" checked={payload.schema.same_fields_for_all_npcs}
          onChange={(e) => setPayload({
            ...payload,
            schema: { ...payload.schema, same_fields_for_all_npcs: e.target.checked },
          })} />
      </div>

      {fields.map((field, index) => (
        <div className="field-editor" key={field.uid || field.id}>
          <div className="head">
            <strong>{field.label || field.id}</strong>
            <div className="btn-row">
              <button className="icon-btn" title="上移" disabled={index === 0}
                onClick={() => { const next = [...fields]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; setFields(next) }}>
                <ArrowUp size={14} />
              </button>
              <button className="icon-btn" title="下移" disabled={index === fields.length - 1}
                onClick={() => { const next = [...fields]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; setFields(next) }}>
                <ArrowDown size={14} />
              </button>
              <button className="icon-btn" title="删除" onClick={() => setFields(fields.filter((_, i) => i !== index))}>
                <Trash2 size={14} />
              </button>
            </div>
          </div>
          <div className="editor-grid">
            <div>
              <label>字段名称</label>
              <input className="input" value={field.label}
                onChange={(e) => { const next = [...fields]; next[index] = { ...field, label: e.target.value }; setFields(next) }} />
            </div>
            <div>
              <label>字段 ID（系统自动生成，不用填）</label>
              <input className="input" value={field.id || '保存后自动生成'} readOnly />
              <div className="field-hint">ID 只给程序用：你写字段名，系统生成 ID 并在保存后固定。改字段名不会改 ID，已有字段值也不会丢。</div>
            </div>
            <div className="wide">
              <label>字段说明（LLM 靠这段理解该字段该填什么、什么时候该变）</label>
              <input className="input" value={field.description}
                onChange={(e) => { const next = [...fields]; next[index] = { ...field, description: e.target.value }; setFields(next) }} />
            </div>
            <div>
              <label>字段类型</label>
              <select className="select" value={field.type}
                onChange={(e) => { const next = [...fields]; next[index] = { ...field, type: e.target.value as FieldType }; setFields(next) }}>
                {TYPES.map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
              </select>
            </div>
            <div>
              <label>更新规则</label>
              <select className="select" value={field.update_policy}
                onChange={(e) => { const next = [...fields]; next[index] = { ...field, update_policy: e.target.value as UpdatePolicy }; setFields(next) }}>
                {POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
            {field.type === 'enum' && (
              <div className="wide">
                <label>预设选项（用英文逗号分隔）</label>
                <input className="input" value={(field.options || []).join(',')}
                  onChange={(e) => { const next = [...fields]; next[index] = { ...field, options: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) }; setFields(next) }} />
              </div>
            )}
            <div className="wide">
              <label className="switch-row" style={{ padding: 0 }}>
                <span>默认展示在面板上</span>
                <input type="checkbox" checked={field.visible}
                  onChange={(e) => { const next = [...fields]; next[index] = { ...field, visible: e.target.checked }; setFields(next) }} />
              </label>
            </div>
          </div>
        </div>
      ))}

      <div className="btn-row" style={{ marginTop: 12 }}>
        <button className="btn" onClick={addField}><Plus size={14} /> 新增字段</button>
        <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
          <Save size={14} /> {saving ? '保存中…' : '保存并应用'}
        </button>
      </div>

      <h3 className="section-title">角色专属字段</h3>
      <div className="card">
        <div className="field-hint" style={{ marginBottom: 10 }}>
          给某个具体 NPC 追加字段，不影响同世界其他角色。已设置 {Object.keys(payload.schema.overrides || {}).length} 个角色覆盖。
        </div>
        {npcIds.length === 0 ? (
          <div className="empty">这个世界还没有 NPC。</div>
        ) : (
          <div className="btn-row">
            <select className="select" style={{ maxWidth: 220 }} value={overrideTarget}
              onChange={(e) => setOverrideTarget(e.target.value)}>
              <option value="">选择角色…</option>
              {npcIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
            <button className="btn" disabled={!overrideTarget} onClick={addOverride}>添加专属字段</button>
          </div>
        )}
      </div>
    </div>
  )
}

import type { FieldDefinition } from '../types'

interface Props {
  field: FieldDefinition
  value: unknown
  editing?: boolean
  onChange?: (value: unknown) => void
}

/** 按字段 type 分派渲染。新增字段类型 = 在这里加一个 case，其他组件完全不用改。 */
export default function FieldRenderer({ field, value, editing = false, onChange }: Props) {
  if (!editing) {
    return <div className="field-value">{renderReadonly(field, value)}</div>
  }

  const emit = (v: unknown) => onChange?.(v)

  switch (field.type) {
    case 'text':
      return <input className="input" value={String(value ?? '')} onChange={(e) => emit(e.target.value)} autoFocus />
    case 'long_text':
      return <textarea className="textarea" value={String(value ?? '')} onChange={(e) => emit(e.target.value)} autoFocus />
    case 'number':
      return (
        <input className="input" type="number" value={Number(value ?? 0)}
          min={field.min ?? undefined} max={field.max ?? undefined}
          onChange={(e) => emit(e.target.value === '' ? 0 : Number(e.target.value))} autoFocus />
      )
    case 'boolean':
      return (
        <label className="switch-row" style={{ padding: 0 }}>
          <span className="field-hint">是 / 否</span>
          <input type="checkbox" checked={Boolean(value)} onChange={(e) => emit(e.target.checked)} autoFocus />
        </label>
      )
    case 'enum':
      return (
        <select className="select" value={String(value ?? '')} onChange={(e) => emit(e.target.value)} autoFocus>
          {(field.options || []).map((opt) => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      )
    case 'list':
      return (
        <textarea className="textarea" value={Array.isArray(value) ? value.join('\n') : ''}
          placeholder="每行一项" onChange={(e) => emit(e.target.value.split('\n').filter((s) => s.trim()))} autoFocus />
      )
    case 'key_value':
      return (
        <textarea className="textarea" value={kvToText(value)} placeholder="每行一条，格式：键: 值"
          onChange={(e) => emit(textToKv(e.target.value))} autoFocus />
      )
    default:
      // 未来出现未知类型时保守降级为只读文本，不让整个面板崩掉。
      return <div className="field-value">{String(value ?? '')}</div>
  }
}

function renderReadonly(field: FieldDefinition, value: unknown) {
  switch (field.type) {
    case 'boolean':
      return value ? '是' : '否'
    case 'list':
      return Array.isArray(value) && value.length
        ? <>{value.map((item, i) => <span className="tag" key={i}>{String(item)}</span>)}</>
        : <span className="field-hint">暂无</span>
    case 'key_value': {
      const entries = Object.entries(isRecord(value) ? value : {})
      return entries.length
        ? <>{entries.map(([k, v]) => <div key={k}>{k}：{String(v)}</div>)}</>
        : <span className="field-hint">暂无</span>
    }
    case 'number':
      return String(value ?? 0)
    default:
      return String(value ?? '—')
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function kvToText(value: unknown): string {
  if (!isRecord(value)) return ''
  return Object.entries(value).map(([k, v]) => `${k}: ${String(v)}`).join('\n')
}

function textToKv(text: string): Record<string, string> {
  const result: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const idx = line.indexOf(':')
    if (idx === -1) continue
    const key = line.slice(0, idx).trim()
    if (key) result[key] = line.slice(idx + 1).trim()
  }
  return result
}

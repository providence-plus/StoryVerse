import { useState } from 'react'
import { ChevronDown, ChevronUp, EyeOff, Pencil, X } from 'lucide-react'
import { api } from '../services/api'
import type { CharacterPanelData } from '../types'
import FieldRenderer from './FieldRenderer'

type Mode = 'expanded' | 'collapsed' | 'hidden'

interface Props {
  title: string
  characterId: string
  worldId: string
  data: CharacterPanelData
  onChanged: () => void
}

/** 完全由 panels.yaml 驱动的人物面板：字段不写死，三态独立控制。 */
export default function DynamicPanel({ title, characterId, worldId, data, onChanged }: Props) {
  const [mode, setMode] = useState<Mode>('expanded')
  const [editingField, setEditingField] = useState<string | null>(null)
  const [draft, setDraft] = useState<unknown>(null)
  const [error, setError] = useState('')

  const visibleFields = data.fields.filter((f) => f.visible)

  const startEdit = (fieldId: string) => {
    setEditingField(fieldId)
    setDraft(data.values[fieldId])
    setError('')
  }

  const save = async (fieldId: string) => {
    try {
      await api.patchField(worldId, characterId, fieldId, draft)
      setEditingField(null)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (mode === 'hidden') {
    return (
      <div className="panel-hidden">
        <span>{title}面板已隐藏</span>
        <button className="btn btn-sm" onClick={() => setMode('expanded')}>显示</button>
      </div>
    )
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h3>{title}</h3>
        <div className="controls">
          {mode === 'expanded' && (
            <>
              <button className="icon-btn" title="折叠" onClick={() => setMode('collapsed')}><ChevronUp size={15} /></button>
              <button className="icon-btn" title="隐藏" onClick={() => setMode('hidden')}><EyeOff size={15} /></button>
            </>
          )}
          {mode === 'collapsed' && (
            <>
              <button className="icon-btn" title="展开" onClick={() => setMode('expanded')}><ChevronDown size={15} /></button>
              <button className="icon-btn" title="隐藏" onClick={() => setMode('hidden')}><EyeOff size={15} /></button>
            </>
          )}
        </div>
      </div>

      {mode === 'expanded' && (
        <div className="panel-body">
          {visibleFields.length === 0 && <div className="empty">这个面板还没有设置可见字段，去 Panel Designer 加一个。</div>}
          {visibleFields.map((field) => (
            <div className="field-row" key={field.id}>
              <div className="field-label" title={field.description}>{field.label}</div>
              {editingField === field.id ? (
                <>
                  <FieldRenderer field={field} value={draft} editing onChange={setDraft} />
                  <div className="btn-row" style={{ marginTop: 8 }}>
                    <button className="btn btn-sm btn-primary" onClick={() => void save(field.id)}>保存</button>
                    <button className="btn btn-sm" onClick={() => setEditingField(null)}>取消</button>
                  </div>
                  {error && <div className="error-banner" style={{ marginTop: 8 }}>{error}</div>}
                </>
              ) : (
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  <div style={{ flex: 1, minWidth: 0 }} onClick={() => startEdit(field.id)} className="field-value editable">
                    <FieldRenderer field={field} value={data.values[field.id]} />
                  </div>
                  <button className="icon-btn" title={`编辑${field.label}`} onClick={() => startEdit(field.id)}>
                    <Pencil size={12} />
                  </button>
                </div>
              )}
            </div>
          ))}
          {Object.keys(data.orphaned || {}).length > 0 && (
            <div className="field-hint" style={{ marginTop: 10 }}>
              <X size={11} style={{ verticalAlign: -2 }} /> 已删除字段的旧值保留 {Object.keys(data.orphaned).length} 项，可在 Panel Designer 里恢复。
            </div>
          )}
        </div>
      )}
    </section>
  )
}

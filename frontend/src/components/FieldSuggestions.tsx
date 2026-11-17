import { useEffect, useState } from 'react'
import { Lightbulb } from 'lucide-react'
import { api } from '../services/api'
import type { FieldSuggestion } from '../types'

interface Props {
  worldId: string
  pendingCount: number
  onChanged?: () => void
}

/** 非阻塞的字段建议：角标攒够才亮，点开才看，绝不弹窗打断阅读。 */
export default function FieldSuggestions({ worldId, pendingCount, onChanged }: Props) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<FieldSuggestion[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = () => {
    api.getSuggestions(worldId)
      .then((result) => setItems(result.items.filter((i) => i.status === 'pending')))
      .catch((e) => setError((e as Error).message))
  }

  useEffect(() => { if (open) load() }, [open, worldId, pendingCount])

  const act = async (fieldId: string, action: 'adopt' | 'ignore') => {
    setError('')
    try {
      if (action === 'adopt') await api.adoptSuggestion(worldId, fieldId)
      else await api.ignoreSuggestion(worldId, fieldId)
      setMessage(action === 'adopt' ? `已采纳「${fieldId}」，字段已建立并按历史补齐。` : `已忽略「${fieldId}」，之后不再提醒。`)
      load()
      onChanged?.()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const showDot = pendingCount >= 3

  return (
    <div>
      <button className="btn btn-sm suggestion-badge" onClick={() => setOpen(!open)}>
        <Lightbulb size={13} /> 字段建议
        {showDot && <span className="dot">{pendingCount}</span>}
      </button>
      {open && (
        <div className="card" style={{ marginTop: 10 }}>
          <div className="field-hint" style={{ marginBottom: 8 }}>
            大模型觉得还该记一项时只留下建议，不会自己改字段定义。攒够几条集中看一次，不打断剧情。
          </div>
          {message && <div className="notice">{message}</div>}
          {error && <div className="error-banner">{error}</div>}
          {items.length === 0 && <div className="empty">暂时没有待处理的建议。</div>}
          {items.map((item) => (
            <div className="suggestion-item" key={item.field_id}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <strong style={{ fontSize: 13.5 }}>{item.label || item.field_id}</strong>
                <span className="chip plain">出现 {item.occurrences} 次</span>
              </div>
              <div className="field-hint">字段 ID：{item.field_id} · 类型：{item.type}</div>
              {item.reason && <div className="field-value" style={{ marginTop: 4 }}>{item.reason}</div>}
              <div className="btn-row" style={{ marginTop: 8 }}>
                <button className="btn btn-sm btn-primary" onClick={() => void act(item.field_id, 'adopt')}>采纳并补齐</button>
                <button className="btn btn-sm" onClick={() => void act(item.field_id, 'ignore')}>忽略</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

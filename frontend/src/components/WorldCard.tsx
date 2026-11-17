import { ArrowRight, Trash2 } from 'lucide-react'
import type { WorldSummary } from '../types'

interface Props {
  world: WorldSummary
  onEnter: (id: string, name: string) => void
  onDelete: (id: string, name: string) => void
}

export default function WorldCard({ world, onEnter, onDelete }: Props) {
  return (
    <div className="card world-card">
      <h3>{world.name}</h3>
      <p className="desc">{world.description || '还没有写下这个世界的故事。'}</p>
      <div className="meta">
        {world.genre && <span className="chip">{world.genre}</span>}
        <span className="chip plain">{world.character_count} 位人物</span>
        <span className="chip plain">{world.save_count} 个存档</span>
      </div>
      {world.current_location && <div className="field-hint">最近进度：{world.current_location}</div>}
      <div className="actions">
        <button className="btn btn-primary" onClick={() => onEnter(world.world_id, world.name)}>
          进入世界 <ArrowRight size={14} />
        </button>
        <button className="btn btn-sm btn-danger" onClick={() => onDelete(world.world_id, world.name)}>
          <Trash2 size={13} /> 删除
        </button>
      </div>
    </div>
  )
}

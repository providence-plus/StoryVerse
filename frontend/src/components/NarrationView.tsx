import type { Dialogue } from '../types'

interface Props {
  narration: string
  dialogues: Dialogue[]
  names: Record<string, string>
  warning?: string | null
}

export default function NarrationView({ narration, dialogues, names, warning }: Props) {
  return (
    <article className="card">
      {warning && <div className="notice">{warning}</div>}
      <div className="narration">
        {narration.split(/\n+/).filter(Boolean).map((para, i) => <p key={i}>{para}</p>)}
      </div>
      {dialogues.length > 0 && (
        <div className="dialogue-list">
          {dialogues.map((d, i) => (
            <div className="dialogue" key={i}>
              <span className="who">{names[d.speaker_id] || d.speaker_id}</span>
              {d.text}
            </div>
          ))}
        </div>
      )}
    </article>
  )
}

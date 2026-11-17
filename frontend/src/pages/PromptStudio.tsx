import { useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { api } from '../services/api'
import type { PromptInfo, WorldSummary } from '../types'

const WORLD_PROMPT = '__world_system__'

export default function PromptStudio() {
  const [scope, setScope] = useState('global')
  const [worlds, setWorlds] = useState<WorldSummary[]>([])
  const [prompts, setPrompts] = useState<PromptInfo[]>([])
  const [active, setActive] = useState('')
  const [content, setContent] = useState('')
  const [path, setPath] = useState('')
  const [globalSize, setGlobalSize] = useState(0)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const select = (name: string) => {
    setMessage(''); setError('')
    if (scope !== 'global') {
      api.getWorldSystemPrompt(scope).then((result) => {
        setActive(WORLD_PROMPT)
        setContent(result.content)
        setPath(`worlds/${scope}/system_prompt.md`)
        setGlobalSize(result.global_size)
      }).catch((e) => setError((e as Error).message))
      return
    }
    api.getPrompt(name).then((result) => {
      setActive(name); setContent(result.content); setPath(result.path)
    }).catch((e) => setError((e as Error).message))
  }

  useEffect(() => {
    api.listWorlds().then((r) => setWorlds(r.worlds)).catch(() => setWorlds([]))
  }, [])

  useEffect(() => {
    if (scope === 'global') {
      api.listPrompts().then((result) => {
        setPrompts(result.prompts)
        if (result.prompts.length) select(result.prompts[0].name)
      }).catch((e) => setError((e as Error).message))
      return
    }
    api.getWorldSystemPrompt(scope).then((result) => {
      const item: PromptInfo = {
        name: WORLD_PROMPT,
        label: '世界专属 System Prompt',
        path: `worlds/${scope}/system_prompt.md`,
        size: result.content.length,
      }
      setPrompts([item])
      setActive(WORLD_PROMPT)
      setContent(result.content)
      setPath(item.path)
      setGlobalSize(result.global_size)
      setMessage(''); setError('')
    }).catch((e) => setError((e as Error).message))
    // select 只在本组件内使用，scope 变化时才需要重新拉取。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope])

  const save = async () => {
    setSaving(true); setError(''); setMessage('')
    try {
      if (scope === 'global') {
        await api.savePrompt(active, content)
        setMessage(`已保存 ${path}。全局写作规则对所有世界生效，从下一次生成开始；正在进行的那一轮不受影响，也不需要重启服务。`)
      } else {
        await api.saveWorldSystemPrompt(scope, content)
        setMessage(`已保存 ${path}。本世界下一轮生成立即生效，不需要退出游戏或重启服务；其他世界不受影响。`)
      }
    } catch (e) { setError((e as Error).message) } finally { setSaving(false) }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Prompt Studio</h1>
          <p className="page-sub">全局写作铁律永远在前；每个世界可以再追加自己的写手风格。改完保存，下一次生成立刻用新版本。</p>
        </div>
        <button className="btn btn-primary" disabled={saving || !active} onClick={() => void save()}>
          <Save size={14} /> {saving ? '保存中…' : '保存当前文件'}
        </button>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="form-row two">
          <div>
            <label className="field-label">编辑范围</label>
            <select className="select" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="global">全局（所有世界共用）</option>
              {worlds.map((w) => <option key={w.world_id} value={w.world_id}>世界 · {w.name}</option>)}
            </select>
          </div>
          <div className="field-hint" style={{ alignSelf: 'end', paddingBottom: 6 }}>
            {scope === 'global'
              ? '这里放基础写作规则与任务模板。改动会影响所有世界的下一次生成。'
              : '这里只写该世界的文风与题材腔调，基础规则由全局文件自动拼在它前面。'}
          </div>
        </div>
        {scope !== 'global' && (
          <div className="notice" style={{ marginTop: 10 }}>
            拼装顺序：<strong>全局 System Prompt（{globalSize} 字，永远在前）→ 本世界风格追加</strong>。
            世界风格不能推翻全局的玩家自主权、设定只读与严格 JSON 输出协议。
            两者都是每轮从磁盘重新读，所以游戏进行中修改也会在<strong>下一轮生成</strong>直接生效；读档不会把改动回滚。
          </div>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}
      {message && <div className="notice">{message}</div>}

      <div className="studio">
        <div className="card">
          <div className="field-hint" style={{ marginBottom: 10 }}>Prompt 文件（{prompts.length}）</div>
          <div className="prompt-list">
            {prompts.map((prompt) => (
              <button key={prompt.name} className={active === prompt.name ? 'active' : ''}
                onClick={() => select(prompt.name)}>
                {prompt.label}
                <div className="field-hint">{prompt.path}</div>
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="field-hint" style={{ marginBottom: 8 }}>
            正在编辑：{path || '—'}
            {scope === 'global' && <> · 模板里的 {'{{占位符}}'} 由后端注入，不要在模板里写死任何人物字段名。</>}
            {scope !== 'global' && <> · 留空表示只用全局铁律，不追加任何风格。</>}
          </div>
          <textarea className="code-editor" value={content} onChange={(e) => setContent(e.target.value)} spellCheck={false} />
        </div>
      </div>
    </div>
  )
}

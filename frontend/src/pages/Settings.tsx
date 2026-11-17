import { useEffect, useState } from 'react'
import { PlugZap, Save } from 'lucide-react'
import { api } from '../services/api'
import type { AppSettings } from '../types'

export default function SettingsPage() {
  const [form, setForm] = useState<AppSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api.getSettings().then(setForm).catch((e) => setError((e as Error).message))
  }, [])

  const set = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setForm((prev) => prev ? { ...prev, [key]: value } : prev)
  }

  const save = async () => {
    if (!form) return
    setSaving(true); setError(''); setMessage('')
    try {
      const updated = await api.saveSettings(form)
      setForm(updated)
      setMessage(updated.mock_mode
        ? '已保存：当前使用 Mock 模拟数据，不会请求真实模型。'
        : `已保存：当前使用真实模型 ${updated.model}（${updated.base_url}）。API Key 不会返回给前端。`)

    } catch (e) { setError((e as Error).message) } finally { setSaving(false) }
  }

  const test = async () => {
    setTesting(true); setError(''); setMessage('')
    try {
      const result = await api.testConnection()
      setMessage(result.message)
      if (!result.ok) setError(result.message)
    } catch (e) { setError((e as Error).message) } finally { setTesting(false) }
  }

  if (!form) return <div className="loading">{error || '正在读取设置…'}</div>

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">设置</h1>
          <p className="page-sub">兼容 OpenAI Chat Completions 的本地推理服务：KoboldCpp、LM Studio、Ollama 都可以。</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {message && <div className="notice">{message}</div>}

      <div className="split">
        <div className="card">
          <h3 className="section-title" style={{ marginTop: 0 }}>模型连接</h3>
          <div className="setting-row">
            <label>API Base URL</label>
            <input className="input" value={form.base_url} onChange={(e) => set('base_url', e.target.value)} />
            <div className="field-hint">例如 http://127.0.0.1:5001/v1（KoboldCpp）、http://127.0.0.1:1234/v1（LM Studio）、http://127.0.0.1:11434/v1（Ollama）。这个默认值没有写死在业务逻辑里。</div>
          </div>
          <div className="setting-row">
            <label>Model Name</label>
            <input className="input" value={form.model} onChange={(e) => set('model', e.target.value)} />
            <div className="field-hint">填本地服务里实际加载的模型名；LM Studio / Ollama 可在各自界面里查看。</div>
          </div>
          <div className="setting-row">
            <label>API Key（可选）</label>
            <input className="input" type="password" value={form.api_key} placeholder={form.api_key_set ? '已设置，留空表示不修改' : '本地服务通常不需要'}
              onChange={(e) => set('api_key', e.target.value)} />
            <div className="field-hint">只保存在后端 config/settings.yaml，前端永远拿不到明文。</div>
          </div>
        </div>

        <div className="card">
          <h3 className="section-title" style={{ marginTop: 0 }}>生成参数</h3>
          <div className="setting-row">
            <label>Temperature：{form.temperature}</label>
            <input type="range" min={0} max={2} step={0.05} value={form.temperature}
              onChange={(e) => set('temperature', Number(e.target.value))} style={{ width: '100%' }} />
            <div className="field-hint">越高越发散，写小说一般 0.7–1.0 比较合适。</div>
          </div>
          <div className="setting-row">
            <label>Max Tokens</label>
            <input className="input" type="number" min={64} max={32768} value={form.max_tokens}
              onChange={(e) => set('max_tokens', Number(e.target.value))} />
          </div>
          <div className="setting-row">
            <label>请求超时（秒）</label>
            <input className="input" type="number" min={5} max={600} value={form.timeout_seconds}
              onChange={(e) => set('timeout_seconds', Number(e.target.value))} />
          </div>
          <div className="switch-row">
            <div>
              <div style={{ fontSize: 13.5 }}>Mock Mode（模拟模式）</div>
              <div className="field-hint">不连模型也能跑完整流程。关掉才会请求上面的真实服务。</div>
            </div>
            <input type="checkbox" checked={form.mock_mode}
              onChange={(e) => set('mock_mode', e.target.checked)} />
          </div>
        </div>
      </div>

      <div className="btn-row" style={{ marginTop: 18 }}>
        <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>
          <Save size={14} /> {saving ? '保存中…' : '保存设置'}
        </button>
        <button className="btn" disabled={testing} onClick={() => void test()}>
          <PlugZap size={14} /> {testing ? '测试中…' : 'Test Connection'}
        </button>
      </div>

      <div className="notice" style={{ marginTop: 20 }}>
        安全说明：后端默认只监听 127.0.0.1；用 start_lan.bat 时也只有前端开发服务器对可信局域网开放，后端仍留在本机。请不要把这些端口映射到公网。
      </div>
    </div>
  )
}

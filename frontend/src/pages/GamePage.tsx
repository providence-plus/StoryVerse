import { useState } from 'react'
import { ArrowLeft, History as HistoryIcon, Play, RefreshCw, Save, Send } from 'lucide-react'
import ChoiceList from '../components/ChoiceList'
import DynamicPanel from '../components/DynamicPanel'
import FieldSuggestions from '../components/FieldSuggestions'
import NarrationView from '../components/NarrationView'
import { useGameState } from '../hooks/useGameState'
import { api } from '../services/api'
import type { Choice, Dialogue } from '../types'

// 兜底选项保持题材中立，不带任何具体世界的地名或剧情。
const NEUTRAL_CHOICES: Choice[] = [
  { id: 'A', text: '向在场的人问一句眼下发生了什么' },
  { id: 'B', text: '不急着开口，把四周仔细看一遍' },
  { id: 'C', text: '查看手边最显眼的那件东西' },
  { id: 'D', text: '暂时离开，等一个更合适的时机' },
]

interface Props { worldId: string; worldName: string; onBack: () => void }

export default function GamePage({ worldId, worldName, onBack }: Props) {
  const game = useGameState(worldId)
  const [input, setInput] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const [showSaves, setShowSaves] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [starting, setStarting] = useState(false)
  // 结构化场景表单：开局 + 「进入下个事件」换场景共用。
  const [sceneForm, setSceneForm] = useState(false)
  const [scene, setScene] = useState('')
  const [sceneLocation, setSceneLocation] = useState('')
  const [sceneIds, setSceneIds] = useState<string[]>([])

  const history = game.history
  const lastTurn = history?.history.length ? history.history[history.history.length - 1] : null
  const currentOutput = game.current?.output

  const turn = game.current?.turn ?? lastTurn?.turn ?? 0
  const hasPlayed = turn > 0 || (history?.history.length ?? 0) > 0
  // 已开局 = 显式点过「开始」，或已经产生过剧情（兼容旧世界）。
  const storyStarted = game.world?.story_started === true || hasPlayed
  const openingScene = game.world?.opening_scene?.trim() || ''
  const narration = currentOutput?.narration ?? lastTurn?.narration
    ?? (turn === 0 && openingScene ? openingScene : history?.summary)
    ?? '故事即将开始。'
  const dialogues: Dialogue[] = currentOutput?.dialogues ?? lastTurn?.dialogues ?? []
  const openingChoices = game.world?.opening_choices?.length ? game.world.opening_choices : NEUTRAL_CHOICES
  // 是否处于「上一幕已收束」状态，以后端明确返回的 scene_ended 为准，不再靠
  // choices 长度猜——收尾轮的 options 是空的，会被兜底链填回 4 个默认选项，
  // 正是这个旧逻辑让「进入下个事件」永远弹不出表单。刷新后 current 为空则看历史末轮。
  const sceneEnded = game.current?.scene_ended === true
    || (turn > 0 && !game.current && !!lastTurn && lastTurn.choices.length === 0)
  const choices: Choice[] = sceneEnded ? []
    : (currentOutput?.choices?.length ? currentOutput.choices
      : (lastTurn?.choices?.length ? lastTurn.choices : openingChoices))
  const warning = currentOutput?.parse_warning ?? null
  // 收尾完成后直接亮出下一幕的结构化表单，玩家不必再多点一次按钮。
  // 开局（turn 0）同样显示表单。
  const showScene = sceneForm || sceneEnded || (storyStarted && turn === 0)

  const names: Record<string, string> = {}
  for (const character of game.characters) names[character.profile.character_id] = character.profile.name

  const panels = game.panels
  const protagonist = panels ? Object.entries(panels.characters).find(([, d]) => d.is_protagonist) : undefined
  const npcs = panels ? Object.entries(panels.characters).filter(([, d]) => !d.is_protagonist) : []
  const protagonistName = protagonist ? (names[protagonist[0]] || '') : ''
  const activeCharacters = game.characters.filter((c) => !c.state.retired)

  const startStory = async () => {
    setStarting(true)
    try {
      await api.startWorld(worldId)
      setNotice('故事已开始。现在定义一下开场的场景、地点和在场角色（都可选），然后开始第一幕。')
      await game.refresh()
    } catch (e) { game.setError((e as Error).message) } finally { setStarting(false) }
  }

  const submitScene = async () => {
    if (game.loading) return
    // 只有生成成功才清空表单；失败时玩家填的场景/地点/在场角色必须原样留着。
    const response = await game.send(scene.trim() || '决定从这里开始这段故事', {
      sceneDescription: scene.trim(),
      location: sceneLocation.trim(),
      activeCharacterIds: sceneIds,
    })
    if (!response) return
    setSceneForm(false); setScene(''); setSceneLocation(''); setSceneIds([])
    setNotice('')
  }

  const endScene = async () => {
    if (game.loading) return
    // 后端返回 scene_ended 后，game.current 更新，showScene 会自动亮出下一幕表单。
    const response = await game.send('决定结束眼前这一幕', { endScene: true })
    if (response?.scene_ended) {
      setNotice('这一幕已经收束。填好下一幕的场景、地点与在场角色，就能继续。')
    }
  }

  const submit = () => {
    const text = input.trim()
    if (!text || game.loading) return
    void game.send(text)
    setInput('')
  }

  const pick = (choice: Choice) => {
    if (game.loading) return
    void game.send(choice.text, { choiceId: choice.id })
  }

  const createSave = async () => {
    setBusy(true)
    try {
      await api.createSave(worldId, saveName.trim() || `分支 ${new Date().toLocaleString('zh-CN')}`)
      setSaveName('')
      setNotice('已创建新存档，之后的剧情会写进这条分支。')
      await game.refresh()
    } catch (e) { game.setError((e as Error).message) } finally { setBusy(false) }
  }

  const loadSave = async (saveId: string) => {
    setBusy(true)
    try {
      await api.loadSave(worldId, saveId)
      // 旧分支的回合结果（含 scene_ended）不能继续驱动读档后的 UI。
      game.clearCurrent()
      setSceneForm(false)
      setNotice('已读取存档，剧情回到那条分支。')
      await game.refresh()
    } catch (e) { game.setError((e as Error).message) } finally { setBusy(false) }
  }

  const reconcile = async () => {
    setBusy(true)
    try {
      let total = 0
      for (const character of game.characters) {
        const result = await api.reconcile(worldId, character.profile.character_id)
        total += result.corrections
      }
      setNotice(total ? `校准完成，修正了 ${total} 个字段值。` : '校准完成，当前字段值与剧情一致，无需修正。')
      await game.refresh()
    } catch (e) { game.setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div>
      <div className="story-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <button className="btn btn-ghost btn-sm" onClick={onBack}><ArrowLeft size={15} /> 世界列表</button>
          <div style={{ minWidth: 0 }}>
            <h2>{worldName}</h2>
            <div className="loc">{game.current?.current_location || game.world?.opening_location || '故事起点'} · {storyStarted ? `第 ${turn} 轮` : '筹备中'}</div>
          </div>
        </div>
        <div className="btn-row">
          <span className="chip plain">存档：{game.current?.save_id ?? 'auto'}</span>
          <button className="btn btn-sm" onClick={() => setShowHistory(!showHistory)}><HistoryIcon size={13} /> 历史</button>
          <button className="btn btn-sm" onClick={() => setShowSaves(!showSaves)}><Save size={13} /> 存档</button>
        </div>
      </div>

      {game.error && <div className="error-banner" style={{ marginTop: 14 }}>{game.error}</div>}
      {notice && <div className="notice" style={{ marginTop: 14 }}>{notice}</div>}

      <div className="game-layout" style={{ marginTop: 16 }}>
        <div className="story-column">
          {!storyStarted && (
            <div className="card">
              <h3 className="section-title" style={{ marginTop: 0 }}>筹备中 · 还没正式开始</h3>
              <p className="field-hint" style={{ lineHeight: 1.7 }}>
                这个世界的故事还没开始。你可以在正式开局前随意增删改世界设定和人物；
                <strong>点「开始」之后，人物设定会上锁</strong>——不能再编辑或删除，只能「退场」（不再出现、可撤回），新增人物仍然可以。
              </p>
              <div className="field-hint" style={{ margin: '10px 0' }}>
                已有角色 {game.characters.length} 位{protagonist ? '，含主角' : '，还没有主角'}
                {openingScene ? '；开场画面已设定' : '；开场画面还没设定（可去世界页补，或开局时在表单里写）'}
              </div>
              <button className="btn btn-primary" disabled={starting} onClick={() => void startStory()}>
                <Play size={15} /> {starting ? '开始中…' : '开始该世界的故事'}
              </button>
            </div>
          )}

          {showScene && (
            <>
              {/* 收尾轮生成的旁白要留下来：玩家点了「进入下个事件」，就是想看这一幕怎么收的。 */}
              {sceneEnded && turn > 0 && (
                <div className="card" style={{ marginBottom: 12 }}>
                  <h3 className="section-title" style={{ marginTop: 0 }}>上一幕收束</h3>
                  <NarrationView narration={narration} dialogues={dialogues} names={names} warning={warning} />
                </div>
              )}
            <div className="card">
              <h3 className="section-title" style={{ marginTop: 0 }}>{turn === 0 ? '开局这一幕' : '下一幕'}</h3>
              <label className="field-label">这一幕发生在哪、是什么氛围（可选）</label>
              <textarea className="textarea" value={scene}
                placeholder="例如：入夜后的旧码头，雨还没停，只有一盏灯还亮着"
                onChange={(e) => setScene(e.target.value)} />
              <label className="field-label" style={{ marginTop: 12 }}>地点名（可选）</label>
              <input className="input" value={sceneLocation} placeholder="例如：旧码头"
                onChange={(e) => setSceneLocation(e.target.value)} />
              <label className="field-label" style={{ marginTop: 12 }}>这一幕谁在场（不勾选则默认全员）</label>
              <div style={{ marginTop: 6 }}>
                {activeCharacters.map((c) => (
                  <label className="switch-row" key={c.profile.character_id}>
                    <span>{c.profile.name}（{c.profile.is_protagonist ? '主角' : 'NPC'}）</span>
                    <input type="checkbox" checked={sceneIds.includes(c.profile.character_id)}
                      onChange={(e) => setSceneIds((prev) => e.target.checked
                        ? [...prev, c.profile.character_id]
                        : prev.filter((id) => id !== c.profile.character_id))} />
                  </label>
                ))}
                {game.characters.length === 0 && (
                  <div className="field-hint">还没有人物。可以先直接开始，或去「人物」页造几个再回来。</div>
                )}
              </div>
              <div className="btn-row" style={{ marginTop: 16 }}>
                <button className="btn btn-primary" disabled={game.loading} onClick={submitScene}>
                  <Play size={15} /> {game.loading ? '生成中…' : (turn === 0 ? '开始这一幕' : '进入下一幕')}
                </button>
                <span className="field-hint">场景、地点、在场角色都可留空，交给 AI 自然展开。</span>
              </div>
            </div>
            </>
          )}

          {storyStarted && !showScene && (
            <>
              <NarrationView narration={narration} dialogues={dialogues} names={names} warning={warning} />
              {turn > 0 && choices.length === 0 ? (
                <div className="card" style={{ marginTop: 12 }}>
                  <div className="field-hint" style={{ marginBottom: 10 }}>这一幕没有可点选的走向。想往下走，就开下一幕。</div>
                  <button className="btn btn-primary" onClick={() => setSceneForm(true)}>
                    <Play size={14} /> 填写下一幕场景
                  </button>
                </div>
              ) : (
                <>
                  <div className="field-hint" style={{ marginBottom: 8 }}>
                    下面 A / B / C / D 四个选项，以及输入框里你自己写的行动，<strong>发起方都是主角{protagonistName ? `「${protagonistName}」` : ''}</strong>，不是任何 NPC。
                  </div>
                  <ChoiceList choices={choices} disabled={game.loading} onPick={pick} />
                  <div className="btn-row" style={{ marginTop: 12 }}>
                    <button className="btn" disabled={game.loading} onClick={endScene}>
                      进入下个事件（收束这一幕）
                    </button>
                  </div>
                </>
              )}

              {showHistory && (
                <div className="card">
                  <h3 className="section-title" style={{ marginTop: 0 }}>故事历史</h3>
                  <div className="field-hint" style={{ marginBottom: 8 }}>剧情摘要</div>
                  <div className="field-value" style={{ marginBottom: 14 }}>{history?.summary || '暂无摘要'}</div>
                  <div className="field-hint" style={{ marginBottom: 8 }}>
                    时间线（{history?.timeline.length ?? 0} 条）
                  </div>
                  {(history?.timeline || []).slice().reverse().map((event, i) => (
                    <div className="history-item" key={i}>第 {event.turn} 轮 · {event.text}</div>
                  ))}
                  {(history?.history || []).length > 0 && (
                    <>
                      <h3 className="section-title">对话记录</h3>
                      {(history?.history || []).slice().reverse().map((turnItem) => (
                        <div className="history-item" key={turnItem.turn}>
                          <div className="action">第 {turnItem.turn} 轮 · 你：{turnItem.player_action}</div>
                          {turnItem.narration.slice(0, 160)}{turnItem.narration.length > 160 ? '…' : ''}
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}

              {showSaves && (
                <div className="card">
                  <h3 className="section-title" style={{ marginTop: 0 }}>存档分支</h3>
                  <div className="field-hint" style={{ marginBottom: 10 }}>不同存档各自保存一份完整状态，读取旧存档会回到那条分支，不会覆盖别的分支。</div>
                  <div className="btn-row" style={{ marginBottom: 12 }}>
                    <input className="input" style={{ maxWidth: 220 }} value={saveName}
                      placeholder="新存档名称" onChange={(e) => setSaveName(e.target.value)} />
                    <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => void createSave()}>新建分支</button>
                  </div>
                  {game.saves.length === 0 && <div className="empty">还没有存档，第一次互动后会自动生成。</div>}
                  {game.saves.map((save) => (
                    <div className="character-row" key={save.save_id}>
                      <div>
                        <div style={{ fontSize: 13.5 }}>{save.name}</div>
                        <div className="field-hint">第 {save.turn ?? 0} 轮 · {save.updated_at || ''}</div>
                      </div>
                      <button className="btn btn-sm" disabled={busy} onClick={() => void loadSave(save.save_id)}>读取</button>
                    </div>
                  ))}
                </div>
              )}

              <div className="composer">
                <div className="composer-inner">
                  <textarea className="textarea" value={input} placeholder={`以主角${protagonistName ? `「${protagonistName}」` : ''}的身份写下你自己的行动……选项只是建议，你可以做任何事`}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }} />
                  <button className="btn btn-primary" disabled={game.loading || !input.trim()} onClick={submit}>
                    <Send size={15} /> {game.loading ? '生成中' : '发送'}
                  </button>
                </div>
                <div className="field-hint">
                  这里写下的行动和四个选项一样，发起方都是主角{protagonistName ? `「${protagonistName}」` : ''}，不是 NPC。
                  Enter 发送，Shift + Enter 换行。玩家行动会在调用模型之前先存盘。
                </div>
              </div>
            </>
          )}
        </div>

        <aside className="side-column">
          <div className="btn-row">
            <FieldSuggestions worldId={worldId} pendingCount={game.current?.suggestions_pending ?? 0}
              onChanged={() => void game.refresh()} />
            <button className="btn btn-sm" disabled={busy} onClick={() => void reconcile()}>
              <RefreshCw size={13} /> 校准字段
            </button>
          </div>
          {game.reconcileEvery > 0 && (
            <div className="field-hint">字段值随每轮剧情更新，另有每 {game.reconcileEvery} 轮一次的兜底校准；新增字段、读档、切换世界会立即触发，不等轮数。</div>
          )}
          {protagonist && panels && (
            <DynamicPanel title="主角" worldId={worldId} characterId={protagonist[0]}
              data={protagonist[1]} onChanged={() => void game.refresh()} />
          )}
          {npcs.map(([id, data]) => (
            <DynamicPanel key={id} title={names[id] || 'NPC'} worldId={worldId} characterId={id}
              data={data} onChanged={() => void game.refresh()} />
          ))}
          {!panels && <div className="loading">正在读取人物面板…</div>}
        </aside>
      </div>
    </div>
  )
}

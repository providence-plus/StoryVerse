import { useCallback, useEffect, useState } from 'react'
import { api } from '../services/api'
import type { CharacterView, HistoryPayload, PanelsPayload, SaveInfo, TurnResponse, WorldSummary } from '../types'

/** 游戏主页的共享状态：只负责取数与提交，渲染交给各组件。 */
export function useGameState(worldId: string | null) {
  const [panels, setPanels] = useState<PanelsPayload | null>(null)
  const [characters, setCharacters] = useState<CharacterView[]>([])
  const [history, setHistory] = useState<HistoryPayload | null>(null)
  const [current, setCurrent] = useState<TurnResponse | null>(null)
  const [saves, setSaves] = useState<SaveInfo[]>([])
  const [reconcileEvery, setReconcileEvery] = useState(8)
  const [world, setWorld] = useState<WorldSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    if (!worldId) return
    try {
      const [chars, hist, cfg, saveList, meta] = await Promise.all([
        api.getCharacters(worldId),
        api.getHistory(worldId),
        api.getStoryConfig(worldId),
        api.getSaves(worldId),
        api.getWorld(worldId),
      ])
      setCharacters(chars.characters)
      setPanels(cfg.panels)
      setHistory(hist)
      setSaves(saveList.saves)
      setWorld(meta)
      setReconcileEvery(Number(cfg.field_system.field_reconcile_every ?? 8))
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }, [worldId])

  // 换世界时必须清掉上一个世界的回合结果，否则 scene_ended 之类的即时标志会串到新世界。
  useEffect(() => {
    setCurrent(null)
    void refresh()
  }, [refresh])

  /** 读档后调用：历史已经换分支了，不能再拿旧分支的回合结果驱动 UI。 */
  const clearCurrent = useCallback(() => setCurrent(null), [])

  const send = useCallback(async (action: string, options?: {
    choiceId?: string; sceneDescription?: string; location?: string;
    activeCharacterIds?: string[]; endScene?: boolean;
  }): Promise<TurnResponse | null> => {
    if (!worldId || !action.trim()) return null
    setLoading(true)
    setError('')
    try {
      const response = await api.turn(worldId, {
        action,
        choice_id: options?.choiceId || null,
        scene_description: options?.sceneDescription || '',
        location: options?.location || '',
        active_character_ids: options?.activeCharacterIds || [],
        end_scene: options?.endScene || false,
      })
      setCurrent(response)
      setPanels(response.panels)
      setCharacters(response.characters)
      await refresh()
      // 返回完整回合结果，调用方（例如「进入下个事件」）才能据 scene_ended 决定下一步 UI。
      return response
    } catch (e) {
      setError((e as Error).message)
      return null
    } finally {
      setLoading(false)
    }
  }, [worldId, refresh])

  return {
    panels, characters, history, current, saves, reconcileEvery, world,
    loading, error, refresh, send, clearCurrent, setError, setPanels,
  }
}

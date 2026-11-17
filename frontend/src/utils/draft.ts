/** 未提交表单的本地草稿。刻意只存表单、不存 AI 预览长文本（localStorage 配额约 5MB）。 */

export const DRAFT_VERSION = 1

interface StoredDraft<T> {
  schema_version: number
  saved_at: number
  value: T
}

export function draftKey(kind: 'world' | 'character', scope: string): string {
  return `storyverse:${kind}-draft:${scope}`
}

/** 返回 false 表示配额不足或被禁用，调用方要给出可见提示。 */
export function saveDraft<T>(key: string, value: T): boolean {
  try {
    const payload: StoredDraft<T> = {
      schema_version: DRAFT_VERSION,
      saved_at: Date.now(),
      value,
    }
    localStorage.setItem(key, JSON.stringify(payload))
    return true
  } catch {
    return false
  }
}

/** 结构不兼容时也要让 UI 知道「这里有一份草稿」，而不是悄悄返回 null。 */
export type DraftResult<T> =
  | { kind: 'ok'; value: T; savedAt: number }
  | { kind: 'corrupt'; savedAt: number }

/**
 * normalize 负责把旧版本 / 缺字段的草稿**补全**成可用结构，返回 null 才表示真没法用。
 * 任何情况下都不主动删数据：只有玩家点「丢弃」才会 clearDraft，
 * 否则组件一挂载就把玩家写的长文静默删掉，属于不可接受的数据丢失。
 */
export function readDraft<T>(
  key: string,
  normalize?: (value: unknown) => T | null,
): DraftResult<T> | null {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredDraft<T>
    const savedAt = Number(parsed?.saved_at) || 0
    const bad = !parsed || parsed.schema_version !== DRAFT_VERSION
      || !('value' in parsed) || !parsed.value || typeof parsed.value !== 'object'
      || Array.isArray(parsed.value)
    if (bad) return { kind: 'corrupt', savedAt }
    const value = normalize ? normalize(parsed.value) : (parsed.value as T)
    if (value === null || value === undefined) return { kind: 'corrupt', savedAt }
    return { kind: 'ok', value, savedAt }
  } catch {
    // JSON 解析失败同样只标记、不删，交给玩家决定要不要丢弃。
    return { kind: 'corrupt', savedAt: 0 }
  }
}

export function clearDraft(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    /* 浏览器禁用存储时忽略 */
  }
}

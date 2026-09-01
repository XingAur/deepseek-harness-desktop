const STORAGE_KEY = 'dsh-harness-task-enabled'

function readStoredEnabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

let enabled = readStoredEnabled()
const listeners = new Set<() => void>()

export function isHarnessEntryEnabled(): boolean {
  return enabled
}

export function setHarnessEntryEnabled(value: boolean): void {
  if (enabled === value) return
  enabled = value
  persist(value)
  listeners.forEach((listener) => listener())
}

export function subscribeHarnessEntry(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function persist(value: boolean): void {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, 'true')
    else window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // localStorage 不可用时，开关仅在当前会话内生效。
  }
}

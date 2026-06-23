import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  BROWSER_STORAGE_PRESSURE_EVENT,
  safeGetJson,
  safeSetItem,
} from './browserStorage'

function buildStorage(overrides: Partial<Storage> = {}): Storage {
  const values = new Map<string, string>()

  return {
    get length() {
      return values.size
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => Array.from(values.keys())[index] ?? null,
    removeItem: (key: string) => {
      values.delete(key)
    },
    setItem: (key: string, value: string) => {
      values.set(key, value)
    },
    ...overrides,
  } as Storage
}

describe('browserStorage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('classifies quota writes and emits a workflow storage-pressure event', () => {
    const quotaError = new DOMException('The quota has been exceeded.', 'QuotaExceededError')
    const storage = buildStorage({
      setItem: () => {
        throw quotaError
      },
    })
    const pressureEvents: Event[] = []
    window.addEventListener(BROWSER_STORAGE_PRESSURE_EVENT, (event) => {
      pressureEvents.push(event)
    }, { once: true })

    const result = safeSetItem(() => storage, 'chat-cache:v1:user:messages', 'payload', {
      owner: 'chat',
      workflowCritical: true,
      quiet: true,
    })

    expect(result).toMatchObject({ ok: false, reason: 'quota_exceeded' })
    expect(pressureEvents).toHaveLength(1)
    expect((pressureEvents[0] as CustomEvent).detail).toMatchObject({
      owner: 'chat',
      key: 'chat-cache:v1:user:messages',
      operation: 'set',
      reason: 'quota_exceeded',
      workflowCritical: true,
    })
  })

  it('returns parse_error for invalid JSON without throwing', () => {
    const storage = buildStorage()
    storage.setItem('invalid', '{')

    const result = safeGetJson(() => storage, 'invalid', {
      owner: 'chat',
      quiet: true,
    })

    expect(result).toMatchObject({ ok: false, reason: 'parse_error' })
  })

  it('classifies lazy storage access failures as security errors', () => {
    const result = safeSetItem(
      () => {
        throw new DOMException('Access denied', 'SecurityError')
      },
      'key',
      'value',
      {
        owner: 'workflow',
        workflowCritical: true,
        quiet: true,
      },
    )

    expect(result).toMatchObject({ ok: false, reason: 'security_error' })
  })
})

import { getEnvInt } from '@/utils/env'

import {
  safeGetJson,
  safeListStorageKeys,
  safeRemoveItem,
  safeSetJson,
  type BrowserStorageAccessor,
  type BrowserStorageResult,
} from './browserStorage'
import {
  clearLegacyChatLocalStorage,
  isNamespacedChatLocalStorageKey,
  pruneChatMessageCacheMessages,
} from './chatCacheKeys'

const DEFAULT_CHAT_RENDER_CACHE_MAX_ENTRIES = 40
const DEFAULT_BATCH_AUDIT_CACHE_MAX_ENTRIES = 20

const BATCH_AUDIT_STORAGE_PREFIX = 'batch_audit_'
const DOCUMENT_LOADING_STORAGE_KEY = 'document-loading'
const AI_CURATION_SESSION_STORAGE_KEYS = [DOCUMENT_LOADING_STORAGE_KEY] as const

interface ParsedCacheEntry {
  key: string
  lastSeenMs: number
}

export interface AiCurationCacheCleanupResult {
  removedKeys: string[]
  failedKeys: string[]
}

export function buildBatchAuditStorageKey(batchId: string): string {
  return `${BATCH_AUDIT_STORAGE_PREFIX}${batchId}`
}

export function getAiCurationChatRenderCacheMaxEntries(): number {
  return Math.max(
    0,
    getEnvInt(
      ['VITE_AI_CURATION_CHAT_RENDER_CACHE_MAX_ENTRIES', 'AI_CURATION_CHAT_RENDER_CACHE_MAX_ENTRIES'],
      DEFAULT_CHAT_RENDER_CACHE_MAX_ENTRIES,
    ),
  )
}

export function getAiCurationBatchAuditCacheMaxEntries(): number {
  return Math.max(
    0,
    getEnvInt(
      ['VITE_AI_CURATION_BATCH_AUDIT_CACHE_MAX_ENTRIES', 'AI_CURATION_BATCH_AUDIT_CACHE_MAX_ENTRIES'],
      DEFAULT_BATCH_AUDIT_CACHE_MAX_ENTRIES,
    ),
  )
}

function getTimestampMs(value: unknown): number | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const record = value as Record<string, unknown>
  const candidates = [
    record.lastInteraction,
    record.timestamp,
    record.loadedAt,
  ]

  for (const candidate of candidates) {
    if (typeof candidate === 'string') {
      const parsed = Date.parse(candidate)
      if (Number.isFinite(parsed)) {
        return parsed
      }
    }
  }

  return null
}

function newestTimestampMs(value: unknown): number {
  if (Array.isArray(value)) {
    const timestamps = value
      .map(getTimestampMs)
      .filter((timestamp): timestamp is number => timestamp !== null)
    return timestamps.length > 0 ? Math.max(...timestamps) : 0
  }

  return getTimestampMs(value) ?? 0
}

function isValidStoredJsonForKey(key: string, value: unknown): boolean {
  if (key.includes(':messages')) {
    return Boolean(
      value
      && typeof value === 'object'
      && Array.isArray((value as { messages?: unknown }).messages),
    )
  }

  if (key.includes(':active-document')) {
    return Boolean(
      value
      && typeof value === 'object'
      && typeof (value as { id?: unknown }).id === 'string',
    )
  }

  if (key.includes(':pdf-viewer-session')) {
    return Boolean(
      value
      && typeof value === 'object'
      && typeof (value as { documentId?: unknown }).documentId === 'string',
    )
  }

  if (key.includes(':audit-events:') || key.startsWith(BATCH_AUDIT_STORAGE_PREFIX)) {
    return Array.isArray(value)
  }

  return true
}

function removeKey(
  storage: BrowserStorageAccessor,
  key: string,
  result: AiCurationCacheCleanupResult,
): void {
  const removal = safeRemoveItem(storage, key, {
    owner: 'workflow',
    key,
    quiet: true,
  })
  if (removal.ok) {
    result.removedKeys.push(key)
  } else {
    result.failedKeys.push(key)
  }
}

function removeOverflowEntries(
  storage: BrowserStorageAccessor,
  entries: ParsedCacheEntry[],
  maxEntries: number,
  result: AiCurationCacheCleanupResult,
): void {
  if (entries.length <= maxEntries) {
    return
  }

  entries
    .sort((left, right) => left.lastSeenMs - right.lastSeenMs || left.key.localeCompare(right.key))
    .slice(0, entries.length - maxEntries)
    .forEach((entry) => removeKey(storage, entry.key, result))
}

export function cleanupAiCurationLocalCache(
  storage: BrowserStorageAccessor = () => window.localStorage,
): AiCurationCacheCleanupResult {
  const result: AiCurationCacheCleanupResult = {
    removedKeys: [],
    failedKeys: [],
  }

  clearLegacyChatLocalStorage(storage)

  const keysResult = safeListStorageKeys(storage, {
    owner: 'workflow',
    quiet: true,
  })
  if (!keysResult.ok) {
    return result
  }

  const chatAuditEntries: ParsedCacheEntry[] = []
  const batchAuditEntries: ParsedCacheEntry[] = []

  for (const key of keysResult.value) {
    const isChatCache = isNamespacedChatLocalStorageKey(key)
    const isBatchAuditCache = key.startsWith(BATCH_AUDIT_STORAGE_PREFIX)
    if (!isChatCache && !isBatchAuditCache) {
      continue
    }

    if (
      key.includes(':messages')
      || key.includes(':active-document')
      || key.includes(':pdf-viewer-session')
      || key.includes(':audit-events:')
      || isBatchAuditCache
    ) {
      const parsed = safeGetJson<unknown>(storage, key, {
        owner: isBatchAuditCache ? 'batch' : 'chat',
        key,
        quiet: true,
      })

      if (!parsed.ok || !isValidStoredJsonForKey(key, parsed.value)) {
        removeKey(storage, key, result)
        continue
      }

      if (key.includes(':messages')) {
        const storedData = parsed.value as { messages: unknown[] }
        const prunedMessages = pruneChatMessageCacheMessages(storedData.messages)
        if (prunedMessages.length !== storedData.messages.length) {
          if (prunedMessages.length === 0) {
            removeKey(storage, key, result)
          } else {
            const writeResult = safeSetJson(storage, key, {
              ...storedData,
              messages: prunedMessages,
            }, {
              owner: 'chat',
              key,
              quiet: true,
            })
            if (!writeResult.ok) {
              result.failedKeys.push(key)
            }
          }
        }
      } else if (key.includes(':audit-events:')) {
        chatAuditEntries.push({ key, lastSeenMs: newestTimestampMs(parsed.value) })
      } else if (isBatchAuditCache) {
        batchAuditEntries.push({ key, lastSeenMs: newestTimestampMs(parsed.value) })
      }
    }
  }

  removeOverflowEntries(
    storage,
    chatAuditEntries,
    getAiCurationChatRenderCacheMaxEntries(),
    result,
  )
  removeOverflowEntries(
    storage,
    batchAuditEntries,
    getAiCurationBatchAuditCacheMaxEntries(),
    result,
  )

  return result
}

export function clearAiCurationLocalCache(
  localStorageArea: BrowserStorageAccessor = () => window.localStorage,
  sessionStorageArea: BrowserStorageAccessor = () => window.sessionStorage,
): AiCurationCacheCleanupResult {
  const result: AiCurationCacheCleanupResult = {
    removedKeys: [],
    failedKeys: [],
  }

  const localKeys = safeListStorageKeys(localStorageArea, {
    owner: 'workflow',
    quiet: true,
  })

  if (localKeys.ok) {
    localKeys.value
      .filter((key) => isNamespacedChatLocalStorageKey(key) || key.startsWith(BATCH_AUDIT_STORAGE_PREFIX))
      .forEach((key) => removeKey(localStorageArea, key, result))
  }

  clearLegacyChatLocalStorage(localStorageArea)

  if (sessionStorageArea) {
    AI_CURATION_SESSION_STORAGE_KEYS.forEach((key) => {
      const removal: BrowserStorageResult = safeRemoveItem(sessionStorageArea, key, {
        owner: 'workflow',
        key,
        quiet: true,
      })
      if (removal.ok) {
        result.removedKeys.push(`session:${key}`)
      } else {
        result.failedKeys.push(`session:${key}`)
      }
    })
  }

  return result
}

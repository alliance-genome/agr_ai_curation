export type BrowserStorageFailureReason =
  | 'quota_exceeded'
  | 'security_error'
  | 'storage_unavailable'
  | 'parse_error'
  | 'unknown_error'

export type BrowserStorageOwner =
  | 'chat'
  | 'pdf-viewer'
  | 'audit'
  | 'batch'
  | 'preferences'
  | 'auth'
  | 'debug'
  | 'workflow'

export type BrowserStorageOperation =
  | 'get'
  | 'set'
  | 'remove'
  | 'list'
  | 'parse'

export type BrowserStorageResult<T = void> =
  | { ok: true; value: T }
  | { ok: false; reason: BrowserStorageFailureReason; error?: unknown }

export type BrowserStorageAccessor = Storage | null | undefined | (() => Storage | null | undefined)

export interface BrowserStorageContext {
  owner: BrowserStorageOwner
  key?: string
  operation?: BrowserStorageOperation
  workflowCritical?: boolean
  quiet?: boolean
}

export interface BrowserStoragePressureEventDetail {
  owner: BrowserStorageOwner
  key?: string
  operation: BrowserStorageOperation
  reason: BrowserStorageFailureReason
  workflowCritical: boolean
}

export const BROWSER_STORAGE_PRESSURE_EVENT = 'agr-browser-storage-pressure'

function resolveStorage(
  storage: BrowserStorageAccessor,
  context: BrowserStorageContext,
): BrowserStorageResult<Storage> {
  try {
    const resolved = typeof storage === 'function' ? storage() : storage
    if (!resolved) {
      const reason: BrowserStorageFailureReason = 'storage_unavailable'
      warnStorageFailure(reason, undefined, {
        ...context,
        operation: context.operation ?? 'get',
      })
      return { ok: false, reason }
    }

    return { ok: true, value: resolved }
  } catch (error) {
    const reason = classifyStorageError(error)
    warnStorageFailure(reason, error, {
      ...context,
      operation: context.operation ?? 'get',
    })
    return { ok: false, reason, error }
  }
}

function classifyStorageError(error: unknown): BrowserStorageFailureReason {
  if (typeof DOMException !== 'undefined' && error instanceof DOMException) {
    if (
      error.name === 'QuotaExceededError'
      || error.name === 'NS_ERROR_DOM_QUOTA_REACHED'
      || error.code === 22
      || error.code === 1014
    ) {
      return 'quota_exceeded'
    }

    if (error.name === 'SecurityError') {
      return 'security_error'
    }
  }

  return 'unknown_error'
}

function warnStorageFailure(
  reason: BrowserStorageFailureReason,
  error: unknown,
  context: BrowserStorageContext,
): void {
  if (!context.quiet) {
    console.warn('Browser storage operation failed', {
      owner: context.owner,
      key: context.key,
      operation: context.operation,
      reason,
      error,
    })
  }

  if (
    typeof window === 'undefined'
    || !context.workflowCritical
    || !['quota_exceeded', 'security_error', 'storage_unavailable'].includes(reason)
  ) {
    return
  }

  window.dispatchEvent(new CustomEvent<BrowserStoragePressureEventDetail>(
    BROWSER_STORAGE_PRESSURE_EVENT,
    {
      detail: {
        owner: context.owner,
        key: context.key,
        operation: context.operation ?? 'set',
        reason,
        workflowCritical: true,
      },
    },
  ))
}

export function safeGetItem(
  storage: BrowserStorageAccessor,
  key: string,
  context: BrowserStorageContext,
): BrowserStorageResult<string | null> {
  const resolved = resolveStorage(storage, { ...context, key, operation: 'get' })
  if (!resolved.ok) {
    return resolved
  }

  try {
    return { ok: true, value: resolved.value.getItem(key) }
  } catch (error) {
    const reason = classifyStorageError(error)
    warnStorageFailure(reason, error, { ...context, key, operation: 'get' })
    return { ok: false, reason, error }
  }
}

export function safeSetItem(
  storage: BrowserStorageAccessor,
  key: string,
  value: string,
  context: BrowserStorageContext,
): BrowserStorageResult {
  const resolved = resolveStorage(storage, { ...context, key, operation: 'set' })
  if (!resolved.ok) {
    return resolved
  }

  try {
    resolved.value.setItem(key, value)
    return { ok: true, value: undefined }
  } catch (error) {
    const reason = classifyStorageError(error)
    warnStorageFailure(reason, error, { ...context, key, operation: 'set' })
    return { ok: false, reason, error }
  }
}

export function safeRemoveItem(
  storage: BrowserStorageAccessor,
  key: string,
  context: BrowserStorageContext,
): BrowserStorageResult {
  const resolved = resolveStorage(storage, { ...context, key, operation: 'remove' })
  if (!resolved.ok) {
    return resolved
  }

  try {
    resolved.value.removeItem(key)
    return { ok: true, value: undefined }
  } catch (error) {
    const reason = classifyStorageError(error)
    warnStorageFailure(reason, error, { ...context, key, operation: 'remove' })
    return { ok: false, reason, error }
  }
}

export function safeSetJson(
  storage: BrowserStorageAccessor,
  key: string,
  value: unknown,
  context: BrowserStorageContext,
): BrowserStorageResult {
  try {
    return safeSetItem(storage, key, JSON.stringify(value), context)
  } catch (error) {
    warnStorageFailure('unknown_error', error, { ...context, key, operation: 'set' })
    return { ok: false, reason: 'unknown_error', error }
  }
}

export function safeGetJson<T>(
  storage: BrowserStorageAccessor,
  key: string,
  context: BrowserStorageContext,
): BrowserStorageResult<T | null> {
  const itemResult = safeGetItem(storage, key, context)
  if (!itemResult.ok) {
    return itemResult
  }

  if (!itemResult.value) {
    return { ok: true, value: null }
  }

  try {
    return { ok: true, value: JSON.parse(itemResult.value) as T }
  } catch (error) {
    warnStorageFailure('parse_error', error, { ...context, key, operation: 'parse' })
    return { ok: false, reason: 'parse_error', error }
  }
}

export function safeListStorageKeys(
  storage: BrowserStorageAccessor,
  context: BrowserStorageContext,
): BrowserStorageResult<string[]> {
  const resolved = resolveStorage(storage, { ...context, operation: 'list' })
  if (!resolved.ok) {
    return resolved
  }

  try {
    const keys: string[] = []
    for (let index = 0; index < resolved.value.length; index += 1) {
      const key = resolved.value.key(index)
      if (key) {
        keys.push(key)
      }
    }
    return { ok: true, value: keys }
  } catch (error) {
    const reason = classifyStorageError(error)
    warnStorageFailure(reason, error, { ...context, operation: 'list' })
    return { ok: false, reason, error }
  }
}

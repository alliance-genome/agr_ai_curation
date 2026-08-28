import {
  safeGetJson,
  safeRemoveItem,
  safeSetJson,
} from '@/lib/browserStorage'

export const DOCUMENT_TABLE_PREFERENCES_VERSION = 1

export type DocumentTableDensity = 'compact' | 'standard'

export interface DocumentTablePreferences {
  version: typeof DOCUMENT_TABLE_PREFERENCES_VERSION
  columnVisibilityModel: Record<string, boolean>
  columnOrder: string[]
  columnWidths: Record<string, number>
  density: DocumentTableDensity
}

const STORAGE_PREFIX = 'ai-curation:preferences:v1'

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
)

export const getDocumentTablePreferencesStorageKey = (userId: string): string => (
  `${STORAGE_PREFIX}:${encodeURIComponent(userId)}:documents-table`
)

export const defaultDocumentTablePreferences = (
  columnFields: readonly string[],
): DocumentTablePreferences => ({
  version: DOCUMENT_TABLE_PREFERENCES_VERSION,
  columnVisibilityModel: {},
  columnOrder: [...columnFields],
  columnWidths: {},
  density: 'standard',
})

export const normalizeDocumentTablePreferences = (
  value: unknown,
  columnFields: readonly string[],
): DocumentTablePreferences => {
  const defaults = defaultDocumentTablePreferences(columnFields)
  if (!isRecord(value) || value.version !== DOCUMENT_TABLE_PREFERENCES_VERSION) {
    return defaults
  }

  const knownFields = new Set(columnFields)
  const columnVisibilityModel: Record<string, boolean> = {}
  if (isRecord(value.columnVisibilityModel)) {
    Object.entries(value.columnVisibilityModel).forEach(([field, visible]) => {
      if (knownFields.has(field) && visible === false) {
        columnVisibilityModel[field] = false
      }
    })
  }

  const storedOrder = Array.isArray(value.columnOrder) ? value.columnOrder : []
  const columnOrder = storedOrder.filter((field, index): field is string => (
    typeof field === 'string'
    && knownFields.has(field)
    && storedOrder.indexOf(field) === index
  ))
  columnFields.forEach((field) => {
    if (!columnOrder.includes(field)) {
      columnOrder.push(field)
    }
  })

  const columnWidths: Record<string, number> = {}
  if (isRecord(value.columnWidths)) {
    Object.entries(value.columnWidths).forEach(([field, width]) => {
      if (knownFields.has(field) && typeof width === 'number' && Number.isFinite(width) && width > 0) {
        columnWidths[field] = width
      }
    })
  }

  const density: DocumentTableDensity = value.density === 'compact' ? 'compact' : 'standard'

  return {
    version: DOCUMENT_TABLE_PREFERENCES_VERSION,
    columnVisibilityModel,
    columnOrder,
    columnWidths,
    density,
  }
}

export const loadDocumentTablePreferences = (
  userId: string | null | undefined,
  columnFields: readonly string[],
): DocumentTablePreferences => {
  if (!userId) {
    return defaultDocumentTablePreferences(columnFields)
  }

  const key = getDocumentTablePreferencesStorageKey(userId)
  const stored = safeGetJson<unknown>(() => window.localStorage, key, {
    owner: 'preferences',
    quiet: true,
  })

  return stored.ok
    ? normalizeDocumentTablePreferences(stored.value, columnFields)
    : defaultDocumentTablePreferences(columnFields)
}

export const saveDocumentTablePreferences = (
  userId: string | null | undefined,
  preferences: DocumentTablePreferences,
  columnFields: readonly string[],
): DocumentTablePreferences => {
  const normalized = normalizeDocumentTablePreferences(preferences, columnFields)
  if (userId) {
    const key = getDocumentTablePreferencesStorageKey(userId)
    safeSetJson(() => window.localStorage, key, normalized, {
      owner: 'preferences',
      quiet: true,
    })
  }
  return normalized
}

export const clearDocumentTablePreferences = (
  userId: string | null | undefined,
): void => {
  if (!userId) {
    return
  }

  const key = getDocumentTablePreferencesStorageKey(userId)
  safeRemoveItem(() => window.localStorage, key, {
    owner: 'preferences',
    quiet: true,
  })
}

export const reorderDocumentTableColumns = (
  columnOrder: readonly string[],
  field: string,
  oldIndex: number,
  targetIndex: number,
): string[] => {
  const currentIndex = columnOrder.indexOf(field)
  if (currentIndex < 0) {
    return [...columnOrder]
  }

  const nextIndex = Math.max(
    0,
    Math.min(columnOrder.length - 1, currentIndex + targetIndex - oldIndex),
  )
  if (nextIndex === currentIndex) {
    return [...columnOrder]
  }

  const nextOrder = [...columnOrder]
  nextOrder.splice(currentIndex, 1)
  nextOrder.splice(nextIndex, 0, field)
  return nextOrder
}

export const hasCustomDocumentTablePreferences = (
  preferences: DocumentTablePreferences,
  columnFields: readonly string[],
): boolean => (
  Object.keys(preferences.columnVisibilityModel).length > 0
  || preferences.columnOrder.some((field, index) => field !== columnFields[index])
  || Object.keys(preferences.columnWidths).length > 0
  || preferences.density !== 'standard'
)

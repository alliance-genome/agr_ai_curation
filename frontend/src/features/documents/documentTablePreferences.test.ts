import { beforeEach, describe, expect, it } from 'vitest'

import {
  getDocumentTablePreferencesStorageKey,
  loadDocumentTablePreferences,
  normalizeDocumentTablePreferences,
  reorderDocumentTableColumns,
  saveDocumentTablePreferences,
} from './documentTablePreferences'

const COLUMN_FIELDS = ['filename', 'title', 'status'] as const

describe('documentTablePreferences', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('drops stale and malformed fields while appending current columns', () => {
    const normalized = normalizeDocumentTablePreferences({
      version: 1,
      columnVisibilityModel: {
        title: false,
        filename: true,
        removedColumn: false,
      },
      columnOrder: ['title', 'removedColumn', 'title', 'filename'],
    }, COLUMN_FIELDS)

    expect(normalized).toEqual({
      version: 1,
      columnVisibilityModel: { title: false },
      columnOrder: ['title', 'filename', 'status'],
    })
  })

  it('falls back to defaults when the stored schema version is unsupported', () => {
    localStorage.setItem(
      getDocumentTablePreferencesStorageKey('curator-1'),
      JSON.stringify({
        version: 99,
        columnVisibilityModel: { title: false },
        columnOrder: ['title'],
      }),
    )

    expect(loadDocumentTablePreferences('curator-1', COLUMN_FIELDS)).toEqual({
      version: 1,
      columnVisibilityModel: {},
      columnOrder: ['filename', 'title', 'status'],
    })
  })

  it('normalizes preferences before saving', () => {
    const saved = saveDocumentTablePreferences('curator-1', {
      version: 1,
      columnVisibilityModel: { title: false, stale: false },
      columnOrder: ['status', 'stale', 'filename', 'title'],
    }, COLUMN_FIELDS)

    expect(saved.columnOrder).toEqual(['status', 'filename', 'title'])
    expect(JSON.parse(
      localStorage.getItem(getDocumentTablePreferencesStorageKey('curator-1')) ?? '{}',
    )).toEqual(saved)
  })

  it('reorders by the drag index delta so injected grid columns do not skew the result', () => {
    expect(reorderDocumentTableColumns(COLUMN_FIELDS, 'status', 3, 1)).toEqual([
      'status',
      'filename',
      'title',
    ])
  })
})

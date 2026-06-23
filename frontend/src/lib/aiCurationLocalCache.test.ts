import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildBatchAuditStorageKey,
  cleanupAiCurationLocalCache,
  clearAiCurationLocalCache,
} from './aiCurationLocalCache'
import {
  getChatLocalStorageKeys,
  getChatRenderCacheKeys,
} from './chatCacheKeys'

describe('aiCurationLocalCache', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    vi.unstubAllEnvs()
  })

  it('removes invalid stale chat cache entries and prunes old audit render caches', () => {
    vi.stubEnv('VITE_AI_CURATION_CHAT_RENDER_CACHE_MAX_ENTRIES', '1')
    const scopedKeys = getChatLocalStorageKeys('user-1')
    const oldAuditKeys = getChatRenderCacheKeys('user-1', 'old-session')
    const newAuditKeys = getChatRenderCacheKeys('user-1', 'new-session')

    localStorage.setItem(scopedKeys.messages, '{')
    localStorage.setItem(scopedKeys.activeDocument, '{"filename":"missing id"}')
    localStorage.setItem(oldAuditKeys.auditEvents, JSON.stringify([
      { type: 'SUPERVISOR_START', timestamp: '2026-06-22T09:00:00.000Z' },
    ]))
    localStorage.setItem(newAuditKeys.auditEvents, JSON.stringify([
      { type: 'SUPERVISOR_COMPLETE', timestamp: '2026-06-23T09:00:00.000Z' },
    ]))

    cleanupAiCurationLocalCache()

    expect(localStorage.getItem(scopedKeys.messages)).toBeNull()
    expect(localStorage.getItem(scopedKeys.activeDocument)).toBeNull()
    expect(localStorage.getItem(oldAuditKeys.auditEvents)).toBeNull()
    expect(localStorage.getItem(newAuditKeys.auditEvents)).not.toBeNull()
  })

  it('prunes old batch audit caches and leaves unrelated localStorage alone', () => {
    vi.stubEnv('VITE_AI_CURATION_BATCH_AUDIT_CACHE_MAX_ENTRIES', '1')
    const oldBatchKey = buildBatchAuditStorageKey('batch-old')
    const newBatchKey = buildBatchAuditStorageKey('batch-new')
    localStorage.setItem(oldBatchKey, JSON.stringify([
      { type: 'TOOL_START', timestamp: '2026-06-22T09:00:00.000Z' },
    ]))
    localStorage.setItem(newBatchKey, JSON.stringify([
      { type: 'TOOL_COMPLETE', timestamp: '2026-06-23T09:00:00.000Z' },
    ]))
    localStorage.setItem('ai-curation:theme-mode', 'dark')

    cleanupAiCurationLocalCache()

    expect(localStorage.getItem(oldBatchKey)).toBeNull()
    expect(localStorage.getItem(newBatchKey)).not.toBeNull()
    expect(localStorage.getItem('ai-curation:theme-mode')).toBe('dark')
  })

  it('clears only AI Curation cache keys for the recovery action', () => {
    const scopedKeys = getChatLocalStorageKeys('user-1')
    localStorage.setItem(scopedKeys.messages, JSON.stringify({ session_id: 's1', messages: [] }))
    localStorage.setItem(buildBatchAuditStorageKey('batch-1'), '[]')
    localStorage.setItem('pdf-viewer-settings', '{"highlightOpacity":0.5}')
    localStorage.setItem('ai-curation:theme-mode', 'dark')
    sessionStorage.setItem('document-loading', 'true')
    sessionStorage.setItem('intendedPath', '/history')

    clearAiCurationLocalCache()

    expect(localStorage.getItem(scopedKeys.messages)).toBeNull()
    expect(localStorage.getItem(buildBatchAuditStorageKey('batch-1'))).toBeNull()
    expect(sessionStorage.getItem('document-loading')).toBeNull()
    expect(localStorage.getItem('pdf-viewer-settings')).not.toBeNull()
    expect(localStorage.getItem('ai-curation:theme-mode')).toBe('dark')
    expect(sessionStorage.getItem('intendedPath')).toBe('/history')
  })
})

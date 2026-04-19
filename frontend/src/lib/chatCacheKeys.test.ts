import { beforeEach, describe, expect, it } from 'vitest'

import {
  clearAllNamespacedChatLocalStorage,
  clearLegacyChatLocalStorage,
  getChatLocalStorageKeys,
} from './chatCacheKeys'

const legacyChatStorageKeys = {
  messages: 'chat-messages',
  sessionId: 'chat-session-id',
  activeDocument: 'chat-active-document',
  userId: 'chat-user-id',
  pdfViewerSession: 'pdf-viewer-session',
} as const

describe('chatCacheKeys', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('clears legacy pre-namespaced chat state in one pass', () => {
    localStorage.setItem(legacyChatStorageKeys.userId, 'user-123')
    localStorage.setItem(legacyChatStorageKeys.messages, '[{"role":"user","content":"hello"}]')
    localStorage.setItem(legacyChatStorageKeys.sessionId, 'session-123')
    localStorage.setItem(legacyChatStorageKeys.activeDocument, '{"id":"doc-123"}')
    localStorage.setItem(legacyChatStorageKeys.pdfViewerSession, '{"documentId":"doc-123"}')

    clearLegacyChatLocalStorage()

    expect(localStorage.getItem(legacyChatStorageKeys.messages)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.sessionId)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.activeDocument)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.pdfViewerSession)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.userId)).toBeNull()
  })

  it('does not touch namespaced chat state when clearing legacy keys', () => {
    const scopedKeys = getChatLocalStorageKeys('user-123')

    localStorage.setItem(scopedKeys.messages, '[{"role":"assistant","content":"hi"}]')
    localStorage.setItem(legacyChatStorageKeys.messages, '[{"role":"user","content":"other"}]')
    localStorage.setItem(legacyChatStorageKeys.sessionId, 'session-999')
    localStorage.setItem(legacyChatStorageKeys.activeDocument, '{"id":"doc-999"}')
    localStorage.setItem(legacyChatStorageKeys.pdfViewerSession, '{"documentId":"doc-999"}')

    clearLegacyChatLocalStorage()

    expect(localStorage.getItem(scopedKeys.messages)).toBe('[{"role":"assistant","content":"hi"}]')
    expect(localStorage.getItem(legacyChatStorageKeys.messages)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.sessionId)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.activeDocument)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.pdfViewerSession)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.userId)).toBeNull()
  })

  it('clears all namespaced chat state in one pass', () => {
    const firstUserKeys = getChatLocalStorageKeys('user-1')
    const secondUserKeys = getChatLocalStorageKeys('user-2')

    localStorage.setItem(firstUserKeys.messages, '[]')
    localStorage.setItem(secondUserKeys.pdfViewerSession, '{"documentId":"doc-2"}')

    clearAllNamespacedChatLocalStorage()

    expect(localStorage.getItem(firstUserKeys.messages)).toBeNull()
    expect(localStorage.getItem(secondUserKeys.pdfViewerSession)).toBeNull()
  })
})

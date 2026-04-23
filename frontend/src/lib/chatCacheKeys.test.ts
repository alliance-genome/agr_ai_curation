import { beforeEach, describe, expect, it } from 'vitest'

import {
  DEFAULT_CHAT_HISTORY_LIST_LIMIT,
  DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
  chatCacheKeys,
  clearChatRenderCacheForSession,
  clearAllNamespacedChatLocalStorage,
  clearLegacyChatLocalStorage,
  getChatLocalStorageKeys,
  getChatRenderCacheKeys,
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

  it('builds auth-scoped render cache keys for session-bound audit state', () => {
    expect(getChatRenderCacheKeys(' user-1 ', ' session-42 ')).toEqual({
      auditEvents: 'chat-cache:v1:user-1:audit-events:session-42',
    })
  })

  it('clears auth-scoped render cache entries for one session', () => {
    const firstSessionKeys = getChatRenderCacheKeys('user-1', 'session-1')
    const secondSessionKeys = getChatRenderCacheKeys('user-1', 'session-2')

    localStorage.setItem(firstSessionKeys.auditEvents, '[{"type":"SUPERVISOR_START"}]')
    localStorage.setItem(secondSessionKeys.auditEvents, '[{"type":"SUPERVISOR_COMPLETE"}]')

    clearChatRenderCacheForSession('user-1', 'session-1')

    expect(localStorage.getItem(firstSessionKeys.auditEvents)).toBeNull()
    expect(localStorage.getItem(secondSessionKeys.auditEvents)).not.toBeNull()
  })

  it('builds stable chat history list query keys from normalized request values', () => {
    expect(
      chatCacheKeys.history.list({
        chatKind: 'assistant_chat',
        query: '  TP53  ',
        cursor: ' cursor-1 ',
        documentId: ' doc-1 ',
      }),
    ).toEqual([
      'chat',
      'history',
      'lists',
      {
        chatKind: 'assistant_chat',
        limit: DEFAULT_CHAT_HISTORY_LIST_LIMIT,
        cursor: 'cursor-1',
        query: 'TP53',
        documentId: 'doc-1',
      },
    ])

    expect(
      chatCacheKeys.history.list({
        chatKind: 'assistant_chat',
        limit: DEFAULT_CHAT_HISTORY_LIST_LIMIT,
        query: '   ',
        cursor: null,
        documentId: '',
      }),
    ).toEqual(
      chatCacheKeys.history.list({
        chatKind: 'assistant_chat',
      }),
    )
  })

  it('builds session-scoped chat history detail keys with default message pagination', () => {
    expect(
      chatCacheKeys.history.detail({
        sessionId: ' session-123 ',
      }),
    ).toEqual([
      'chat',
      'history',
      'details',
      'session-123',
      {
        messageLimit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
        messageCursor: null,
      },
    ])

    expect(
      chatCacheKeys.history.detail({
        sessionId: 'session-123',
        messageLimit: 25,
        messageCursor: ' cursor-2 ',
      }),
    ).toEqual([
      'chat',
      'history',
      'details',
      'session-123',
      {
        messageLimit: 25,
        messageCursor: 'cursor-2',
      },
    ])
  })
})

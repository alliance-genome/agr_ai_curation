import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import { AuthProvider, useAuth } from './AuthContext'

const legacyChatStorageKeys = {
  messages: 'chat-messages',
  sessionId: 'chat-session-id',
  activeDocument: 'chat-active-document',
  userId: 'chat-user-id',
  pdfViewerSession: 'pdf-viewer-session',
} as const

vi.mock('@/services/logger', () => ({
  logger: {
    debug: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
  },
}))

function AuthProbe() {
  const { isAuthenticated, isLoading, user } = useAuth()

  return (
    <>
      <div data-testid="auth-status">{isAuthenticated ? 'authenticated' : 'anonymous'}</div>
      <div data-testid="auth-loading">{isLoading ? 'loading' : 'ready'}</div>
      <div data-testid="auth-user">{user?.uid ?? 'none'}</div>
    </>
  )
}

describe('AuthProvider dev-mode bootstrap', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    vi.mocked(global.fetch).mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
  })

  it('clears legacy chat storage during dev-mode bootstrap without touching namespaced state', async () => {
    vi.stubEnv('VITE_DEV_MODE', 'true')

    const scopedKeys = getChatLocalStorageKeys('dev-user-123')

    localStorage.setItem(scopedKeys.messages, '[{"role":"assistant","content":"hi"}]')
    localStorage.setItem(scopedKeys.sessionId, 'durable-session-42')
    localStorage.setItem(scopedKeys.activeDocument, '{"id":"doc-42"}')
    localStorage.setItem(scopedKeys.pdfViewerSession, '{"documentId":"doc-42"}')
    localStorage.setItem(legacyChatStorageKeys.messages, '[{"role":"user","content":"stale"}]')
    localStorage.setItem(legacyChatStorageKeys.sessionId, 'legacy-session')
    localStorage.setItem(legacyChatStorageKeys.activeDocument, '{"id":"legacy-doc"}')
    localStorage.setItem(legacyChatStorageKeys.userId, 'legacy-user')
    localStorage.setItem(legacyChatStorageKeys.pdfViewerSession, '{"documentId":"legacy-doc"}')

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    expect(screen.getByTestId('auth-status')).toHaveTextContent('authenticated')
    expect(screen.getByTestId('auth-loading')).toHaveTextContent('ready')
    expect(screen.getByTestId('auth-user')).toHaveTextContent('dev-user-123')

    await waitFor(() => {
      expect(localStorage.getItem(legacyChatStorageKeys.messages)).toBeNull()
    })

    expect(localStorage.getItem(legacyChatStorageKeys.sessionId)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.activeDocument)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.userId)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.pdfViewerSession)).toBeNull()

    expect(localStorage.getItem(scopedKeys.messages)).toBe('[{"role":"assistant","content":"hi"}]')
    expect(localStorage.getItem(scopedKeys.sessionId)).toBe('durable-session-42')
    expect(localStorage.getItem(scopedKeys.activeDocument)).toBe('{"id":"doc-42"}')
    expect(localStorage.getItem(scopedKeys.pdfViewerSession)).toBe('{"documentId":"doc-42"}')

    expect(vi.mocked(global.fetch)).not.toHaveBeenCalled()
  })

  it('does not repeat legacy chat cleanup on the periodic dev-mode auth refresh', async () => {
    vi.useFakeTimers()
    vi.stubEnv('VITE_DEV_MODE', 'true')

    Object.values(legacyChatStorageKeys).forEach((key) => {
      localStorage.setItem(key, `${key}-value`)
    })

    const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem')

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    await act(async () => {
      await Promise.resolve()
    })

    expect(removeItemSpy.mock.calls.map(([key]) => key)).toEqual(Object.values(legacyChatStorageKeys))

    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000)
      await Promise.resolve()
    })

    expect(removeItemSpy.mock.calls.map(([key]) => key)).toEqual(Object.values(legacyChatStorageKeys))
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalled()

    removeItemSpy.mockRestore()
  })
})

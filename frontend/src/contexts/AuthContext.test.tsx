import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import { RUNTIME_CONFIG_GLOBAL } from '@/utils/env'
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
    delete window[RUNTIME_CONFIG_GLOBAL]
    vi.mocked(global.fetch).mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
    delete window[RUNTIME_CONFIG_GLOBAL]
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

  it('does not allow runtime configuration to enable the auth bypass', async () => {
    vi.stubEnv('VITE_DEV_MODE', 'false')
    window[RUNTIME_CONFIG_GLOBAL] = { VITE_DEV_MODE: 'true' }
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 401 }))

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    )

    expect(screen.getByTestId('auth-status')).toHaveTextContent('anonymous')
    await waitFor(() => {
      expect(vi.mocked(global.fetch)).toHaveBeenCalledWith('/api/users/me', expect.any(Object))
    })
  })

  it.each(['empty', 'session-only', 'complete'])(
    'does not create conversations or clear chat state during repeated production auth checks (%s cache)',
    async (cacheState) => {
      vi.useFakeTimers()
      vi.stubEnv('VITE_DEV_MODE', 'false')
      const keys = getChatLocalStorageKeys('curator-1')
      if (cacheState !== 'empty') localStorage.setItem(keys.sessionId, 'existing-session')
      if (cacheState === 'complete') {
        localStorage.setItem(keys.messages, JSON.stringify({
          session_id: 'existing-session',
          messages: [{ id: 'message-1', role: 'user', content: 'Keep this', timestamp: new Date().toISOString() }],
        }))
      }
      const originalSession = localStorage.getItem(keys.sessionId)
      const originalMessages = localStorage.getItem(keys.messages)
      vi.mocked(global.fetch).mockImplementation(async (url) => {
        if (String(url) === '/api/users/me') {
          return new Response(JSON.stringify({ auth_sub: 'curator-1', email: 'curator@example.org' }))
        }
        // Reproduce the old trigger: durable history exists regardless of local cache.
        return new Response(JSON.stringify({ total_sessions: 1053 }))
      })

      render(<AuthProvider><AuthProbe /></AuthProvider>)
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      for (let poll = 0; poll < 3; poll += 1) {
        await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60 * 1000) })
      }

      expect(screen.getByTestId('auth-status')).toHaveTextContent('authenticated')
      expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(4)
      expect(vi.mocked(global.fetch).mock.calls.every(([url, options]) =>
        String(url) === '/api/users/me' && options?.method === 'GET',
      )).toBe(true)
      expect(localStorage.getItem(keys.sessionId)).toBe(originalSession)
      expect(localStorage.getItem(keys.messages)).toBe(originalMessages)
    },
  )
})

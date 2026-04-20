import { StrictMode } from 'react'
import { ThemeProvider } from '@mui/material/styles'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import { DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT, getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import HomePage from './HomePage'

const mockUseAuth = vi.hoisted(() => vi.fn())
const mockUseChatStream = vi.hoisted(() => vi.fn())
const chatRenderSpy = vi.hoisted(() => vi.fn())
const rightPanelRenderSpy = vi.hoisted(() => vi.fn())

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}))

vi.mock('@/hooks/useChatStream', () => ({
  useChatStream: () => mockUseChatStream(),
}))

vi.mock('@/components/Chat', () => ({
  default: (props: { sessionId: string | null }) => {
    chatRenderSpy(props)
    return <div data-testid="chat-session">{props.sessionId ?? 'none'}</div>
  },
}))

vi.mock('@/components/RightPanel', () => ({
  default: (props: { sessionId: string | null; currentDocumentId?: string }) => {
    rightPanelRenderSpy(props)
    return (
      <div data-testid="right-panel-session">
        {props.sessionId ?? 'none'}::{props.currentDocumentId ?? 'no-document'}
      </div>
    )
  },
}))

const chatStreamStub = {
  events: [],
  isLoading: false,
  sendMessage: vi.fn(),
  stopStream: vi.fn(),
  executeFlow: vi.fn(),
}

function jsonResponse(payload: unknown, status: number = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search}</div>
}

function renderHomePage(initialEntry: string = '/'): ReturnType<typeof render> {
  return renderHomePageWithOptions(initialEntry)
}

function renderHomePageWithOptions(
  initialEntry: string = '/',
  options: { strictMode?: boolean } = {},
): ReturnType<typeof render> {
  const content = (
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/"
            element={(
              <>
                <HomePage />
                <LocationProbe />
              </>
            )}
          />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  )

  return render(options.strictMode ? <StrictMode>{content}</StrictMode> : content)
}

describe('HomePage durable session bootstrap', () => {
  const chatStorageKeys = getChatLocalStorageKeys('user-1')
  let authState: { user: { uid: string } | null }

  beforeEach(() => {
    authState = { user: { uid: 'user-1' } }
    localStorage.clear()
    sessionStorage.clear()
    chatRenderSpy.mockReset()
    rightPanelRenderSpy.mockReset()
    vi.mocked(global.fetch).mockReset()
    mockUseAuth.mockImplementation(() => authState)
    mockUseChatStream.mockReturnValue(chatStreamStub)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('restores the requested session before mounting the chat surface and rehydrates document state', async () => {
    localStorage.setItem(chatStorageKeys.sessionId, 'stale-local-session')
    localStorage.setItem(chatStorageKeys.messages, JSON.stringify({
      session_id: 'stale-local-session',
      messages: [
        {
          role: 'assistant',
          content: 'stale',
          timestamp: '2026-04-20T00:00:00Z',
          type: 'text',
        },
      ],
    }))

    const pdfDocumentChangedSpy = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === `/api/chat/history/session-42?message_limit=${DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT}`) {
        return jsonResponse({
          session: {
            session_id: 'session-42',
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:05:00Z',
            recent_activity_at: '2026-04-20T00:05:00Z',
          },
          active_document: {
            id: 'doc-42',
            filename: 'resume.pdf',
          },
          messages: [
            {
              message_id: 'msg-1',
              session_id: 'session-42',
              role: 'assistant',
              message_type: 'text',
              content: 'Restored response',
              trace_id: 'trace-1',
              created_at: '2026-04-20T00:01:00Z',
            },
          ],
          message_limit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
          next_message_cursor: null,
        })
      }

      if (url === '/api/chat/document/load') {
        expect(init?.method).toBe('POST')
        return jsonResponse({
          active: true,
          document: {
            id: 'doc-42',
            filename: 'resume.pdf',
          },
        })
      }

      if (url === '/api/pdf-viewer/documents/doc-42') {
        return jsonResponse({
          filename: 'resume.pdf',
          page_count: 7,
        })
      }

      if (url === '/api/pdf-viewer/documents/doc-42/url') {
        return jsonResponse({
          viewer_url: '/viewer/doc-42',
        })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-42')

    expect(screen.getByText('Restoring chat session...')).toBeInTheDocument()
    expect(chatRenderSpy).not.toHaveBeenCalled()

    expect(await screen.findByText('session-42')).toBeInTheDocument()

    expect(chatRenderSpy).toHaveBeenCalledTimes(1)
    expect(chatRenderSpy.mock.calls[0][0].sessionId).toBe('session-42')
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalledWith(
      '/api/chat/session',
      expect.anything(),
    )

    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('session-42')
    expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('"documentId":"doc-42"')
    expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('"id":"doc-42"')

    const storedMessages = JSON.parse(localStorage.getItem(chatStorageKeys.messages) ?? '{}')
    expect(storedMessages).toEqual({
      session_id: 'session-42',
      messages: [
        {
          role: 'assistant',
          content: 'Restored response',
          timestamp: '2026-04-20T00:01:00Z',
          id: 'msg-1',
          traceIds: ['trace-1'],
          type: 'text',
        },
      ],
    })

    await waitFor(() => {
      expect(pdfDocumentChangedSpy).toHaveBeenCalledTimes(1)
    })

    const pdfEvent = pdfDocumentChangedSpy.mock.calls[0][0] as CustomEvent
    expect(pdfEvent.detail).toMatchObject({
      documentId: 'doc-42',
      viewerUrl: '/viewer/doc-42',
      filename: 'resume.pdf',
      pageCount: 7,
    })

    window.removeEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)
  })

  it('waits for user.uid before hydrating a requested durable session', async () => {
    authState = { user: null }

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === `/api/chat/history/session-auth?message_limit=${DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT}`) {
        return jsonResponse({
          session: {
            session_id: 'session-auth',
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:03:00Z',
            recent_activity_at: '2026-04-20T00:03:00Z',
          },
          active_document: null,
          messages: [],
          message_limit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
          next_message_cursor: null,
        })
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    const view = renderHomePage('/?session=session-auth')

    expect(screen.getByText('Restoring chat session...')).toBeInTheDocument()
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalled()

    authState = { user: { uid: 'user-1' } }
    view.rerender(
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={['/?session=session-auth']}>
          <Routes>
            <Route
              path="/"
              element={(
                <>
                  <HomePage />
                  <LocationProbe />
                </>
              )}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    )

    expect(await screen.findByText('session-auth')).toBeInTheDocument()

    expect(
      vi.mocked(global.fetch).mock.calls.some(
        ([url]) => String(url) === `/api/chat/history/session-auth?message_limit=${DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT}`,
      ),
    ).toBe(true)
  })

  it('creates only one durable session during StrictMode fresh bootstrap', async () => {
    let resolveCreateSession: ((response: Response) => void) | null = null
    const createSessionPromise = new Promise<Response>((resolve) => {
      resolveCreateSession = resolve
    })

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/api/chat/session') {
        return createSessionPromise
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePageWithOptions('/', { strictMode: true })

    await waitFor(() => {
      expect(
        vi.mocked(global.fetch).mock.calls.filter(
          ([url]) => String(url) === '/api/chat/session',
        ),
      ).toHaveLength(1)
    })

    resolveCreateSession?.(jsonResponse({
      session_id: 'strict-session',
      created_at: '2026-04-20T02:00:00Z',
      updated_at: '2026-04-20T02:00:00Z',
      active_document: null,
    }))

    expect(await screen.findByText('strict-session')).toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('strict-session')
    expect(
      vi.mocked(global.fetch).mock.calls.filter(
        ([url]) => String(url) === '/api/chat/session',
      ),
    ).toHaveLength(1)
  })

  it('shows a warning for a missing requested session and starts a new durable chat on demand', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === `/api/chat/history/deleted-session?message_limit=${DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT}`) {
        return jsonResponse({ detail: 'Chat session not found' }, 404)
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      if (url === '/api/chat/session') {
        return jsonResponse({
          session_id: 'new-session-1',
          created_at: '2026-04-20T01:00:00Z',
          updated_at: '2026-04-20T01:00:00Z',
          active_document: null,
        })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=deleted-session')

    expect(
      await screen.findByText('This chat session is unavailable. It may have been deleted.'),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('chat-session')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Start new chat' }))

    expect(await screen.findByText('new-session-1')).toBeInTheDocument()
    expect(screen.getByTestId('location-search')).toHaveTextContent('')
    expect(screen.queryByText(/deleted-session is unavailable/)).not.toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('new-session-1')
  })

  it('recovers from a fresh bootstrap failure when starting a new durable chat', async () => {
    let createSessionAttempts = 0

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/api/chat/session') {
        createSessionAttempts += 1

        if (createSessionAttempts === 1) {
          return jsonResponse({ detail: 'Initial session bootstrap failed' }, 500)
        }

        return jsonResponse({
          session_id: 'retry-session',
          created_at: '2026-04-20T03:00:00Z',
          updated_at: '2026-04-20T03:00:00Z',
          active_document: null,
        })
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/')

    expect(await screen.findByText('Initial session bootstrap failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start new chat' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Start new chat' }))

    expect(await screen.findByText('retry-session')).toBeInTheDocument()
    expect(screen.queryByText('Preparing chat session...')).not.toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('retry-session')
    expect(createSessionAttempts).toBe(2)
  })
})

import { type ComponentProps, StrictMode } from 'react'
import { ThemeProvider } from '@mui/material/styles'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import { DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT, getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import {
  DOCUMENT_LOADING_STORAGE_KEY,
  DOCUMENT_LOAD_ERROR_EVENT,
} from '@/features/documents/documentLoadEvents'
import { beginChatDocumentIntent } from '@/features/documents/chatDocumentIntent'
import {
  dispatchChatDocumentChanged,
  loadDocumentForChat,
} from '@/features/documents/pdfUploadFlow'
import { CHAT_HISTORY_TRANSCRIPT_PAGE_SIZE } from '@/features/history/chatHistoryTranscript'
import HistoryPage from '@/features/history/HistoryPage'
import HomePage from './HomePage'

const mockUseAuth = vi.hoisted(() => vi.fn())
const mockUseChatStream = vi.hoisted(() => vi.fn())
const chatRenderSpy = vi.hoisted(() => vi.fn())
const rightPanelRenderSpy = vi.hoisted(() => vi.fn())
const actualChatMode = vi.hoisted(() => ({ enabled: false }))

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}))

vi.mock('@/hooks/useChatStream', () => ({
  useChatStream: () => mockUseChatStream(),
}))

vi.mock('@/components/Chat', async () => {
  const actual = await vi.importActual<typeof import('@/components/Chat')>('@/components/Chat')

  return {
    default: (props: ComponentProps<typeof actual.default>) => {
      chatRenderSpy(props)

      if (actualChatMode.enabled) {
        const ActualChat = actual.default
        return <ActualChat {...props} />
      }

      return <div data-testid="chat-session">{props.sessionId ?? 'none'}</div>
    },
  }
})

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
  eventStreamVersion: 0,
  processedEventCount: 0,
  isLoading: false,
  sendMessage: vi.fn(),
  markEventsProcessed: vi.fn(),
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

function buildAssistantHistoryDetailUrl(sessionId: string): string {
  return `/api/chat/history/${sessionId}?chat_kind=assistant_chat&message_limit=${CHAT_HISTORY_TRANSCRIPT_PAGE_SIZE}`
}

function LocationProbe() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <div data-testid="location-search">{location.search}</div>
      <button type="button" onClick={() => navigate('/?session=session-a')}>Restore A</button>
      <button type="button" onClick={() => navigate('/?session=session-b')}>Restore B</button>
    </>
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

type HomeInitialEntry = NonNullable<ComponentProps<typeof MemoryRouter>['initialEntries']>[number]

function renderHomePage(initialEntry: HomeInitialEntry = '/'): ReturnType<typeof render> {
  return renderHomePageWithOptions(initialEntry)
}

function renderHomePageWithOptions(
  initialEntry: HomeInitialEntry = '/',
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
    actualChatMode.enabled = false
    localStorage.clear()
    sessionStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    chatRenderSpy.mockReset()
    rightPanelRenderSpy.mockReset()
    vi.mocked(global.fetch).mockReset()
    mockUseAuth.mockImplementation(() => authState)
    mockUseChatStream.mockReturnValue(chatStreamStub)
  })

  afterEach(() => {
    actualChatMode.enabled = false
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('opens Tools when a flow is routed from Agent Studio', async () => {
    vi.mocked(global.fetch).mockResolvedValue(jsonResponse({ session_id: 'my-session' }))
    renderHomePage('/?flow=shared-flow')
    await waitFor(() => expect(rightPanelRenderSpy).toHaveBeenCalled())
    expect(rightPanelRenderSpy.mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({ activeTabIndex: 1 }))
    expect(chatStreamStub.executeFlow).not.toHaveBeenCalled()
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

      if (url === buildAssistantHistoryDetailUrl('session-42')) {
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

  it('keeps the latest history restore across reverse-order completion', async () => {
    const firstHistory = deferred<Response>()
    const loadBodies: Array<Record<string, unknown>> = []
    const pdfDocumentChangedSpy = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)

    vi.mocked(global.fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === buildAssistantHistoryDetailUrl('session-a')) {
        return firstHistory.promise
      }
      if (url === buildAssistantHistoryDetailUrl('session-b')) {
        return Promise.resolve(jsonResponse({
          session: {
            session_id: 'session-b',
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:05:00Z',
            recent_activity_at: '2026-04-20T00:05:00Z',
          },
          active_document: { id: 'doc-b', filename: 'b.pdf' },
          messages: [{
            message_id: 'message-b',
            session_id: 'session-b',
            role: 'assistant',
            message_type: 'text',
            content: 'Latest history',
            created_at: '2026-04-20T00:01:00Z',
          }],
          message_limit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
          next_message_cursor: null,
        }))
      }
      if (url === '/api/chat/document/load') {
        loadBodies.push(JSON.parse(String(init?.body)))
        return Promise.resolve(jsonResponse({
          active: true,
          document: { id: 'doc-b', filename: 'b.pdf' },
        }))
      }
      if (url === '/api/pdf-viewer/documents/doc-b') {
        return Promise.resolve(jsonResponse({ filename: 'b.pdf', page_count: 2 }))
      }
      if (url === '/api/pdf-viewer/documents/doc-b/url') {
        return Promise.resolve(jsonResponse({ viewer_url: '/viewer/doc-b' }))
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-a')
    fireEvent.click(screen.getByRole('button', { name: 'Restore B' }))

    expect(await screen.findByText('session-b')).toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('session-b')
    expect(localStorage.getItem(chatStorageKeys.messages)).toContain('Latest history')
    expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('doc-b')
    expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('doc-b')
    expect(loadBodies).toHaveLength(1)
    expect(loadBodies[0]).toMatchObject({ document_id: 'doc-b' })

    firstHistory.resolve(jsonResponse({
      session: {
        session_id: 'session-a',
        created_at: '2026-04-20T00:00:00Z',
        updated_at: '2026-04-20T00:02:00Z',
        recent_activity_at: '2026-04-20T00:02:00Z',
      },
      active_document: { id: 'doc-a', filename: 'a.pdf' },
      messages: [],
      message_limit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
      next_message_cursor: null,
    }))
    await act(async () => firstHistory.promise)

    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('session-b')
    expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('doc-b')
    expect(pdfDocumentChangedSpy).toHaveBeenCalledTimes(1)
    expect((pdfDocumentChangedSpy.mock.calls[0][0] as CustomEvent).detail.documentId).toBe('doc-b')

    window.removeEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)
  })

  it('keeps a successful latest restore when an older failed restore finishes clearing last', async () => {
    const staleDocumentClear = deferred<Response>()
    const loadBodies: Array<Record<string, unknown>> = []
    const pdfDocumentChangedSpy = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)

    vi.mocked(global.fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === buildAssistantHistoryDetailUrl('session-a')) {
        return Promise.resolve(jsonResponse({ detail: 'Chat session not found' }, 404))
      }
      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return staleDocumentClear.promise
      }
      if (url === buildAssistantHistoryDetailUrl('session-b')) {
        return Promise.resolve(jsonResponse({
          session: {
            session_id: 'session-b',
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:05:00Z',
            recent_activity_at: '2026-04-20T00:05:00Z',
          },
          active_document: { id: 'doc-b', filename: 'b.pdf' },
          messages: [{
            message_id: 'message-b',
            session_id: 'session-b',
            role: 'assistant',
            message_type: 'text',
            content: 'Latest history',
            created_at: '2026-04-20T00:01:00Z',
          }],
          message_limit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
          next_message_cursor: null,
        }))
      }
      if (url === '/api/chat/document/load') {
        loadBodies.push(JSON.parse(String(init?.body)))
        return Promise.resolve(jsonResponse({
          active: true,
          document: { id: 'doc-b', filename: 'b.pdf' },
        }))
      }
      if (url === '/api/pdf-viewer/documents/doc-b') {
        return Promise.resolve(jsonResponse({ filename: 'b.pdf', page_count: 2 }))
      }
      if (url === '/api/pdf-viewer/documents/doc-b/url') {
        return Promise.resolve(jsonResponse({ viewer_url: '/viewer/doc-b' }))
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-a')

    await waitFor(() => {
      expect(vi.mocked(global.fetch)).toHaveBeenCalledWith(
        '/api/chat/document',
        expect.objectContaining({ method: 'DELETE' }),
      )
    })

    fireEvent.click(screen.getByRole('button', { name: 'Restore B' }))

    expect(await screen.findByText('session-b')).toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('session-b')
    expect(localStorage.getItem(chatStorageKeys.messages)).toContain('Latest history')
    expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('doc-b')
    expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('doc-b')
    expect(loadBodies).toEqual([expect.objectContaining({ document_id: 'doc-b' })])
    const staleClearCall = vi.mocked(global.fetch).mock.calls.find(
      ([url, init]) => String(url) === '/api/chat/document' && init?.method === 'DELETE',
    )
    const staleClearHeaders = staleClearCall?.[1]?.headers as Record<string, string>
    expect(loadBodies[0].intent_owner).toBe(staleClearHeaders['X-Chat-Document-Intent-Owner'])
    expect(Number(loadBodies[0].intent_generation)).toBeGreaterThan(
      Number(staleClearHeaders['X-Chat-Document-Intent-Generation']),
    )

    staleDocumentClear.resolve(jsonResponse({ active: false, document: null }))
    await act(async () => staleDocumentClear.promise)

    expect(screen.getByText('session-b')).toBeInTheDocument()
    expect(screen.queryByText('This chat session is unavailable. It may have been deleted.'))
      .not.toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('session-b')
    expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('doc-b')
    expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('doc-b')
    expect(pdfDocumentChangedSpy).toHaveBeenCalledTimes(1)
    expect((pdfDocumentChangedSpy.mock.calls[0][0] as CustomEvent).detail.documentId).toBe('doc-b')

    window.removeEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)
  })

  it('loads a Documents tab route-state handoff after Home and Chat are mounted', async () => {
    const chatDocumentChangedSpy = vi.fn()
    window.addEventListener('chat-document-changed', chatDocumentChangedSpy as EventListener)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/api/chat/session') {
        expect(init?.method).toBe('POST')
        return jsonResponse({
          session_id: 'route-session',
          created_at: '2026-05-07T15:00:00Z',
          updated_at: '2026-05-07T15:00:00Z',
          active_document: null,
        })
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      if (url === '/api/chat/document/load') {
        expect(init?.method).toBe('POST')
        expect(JSON.parse(String(init?.body))).toMatchObject({ document_id: 'doc-route' })
        return jsonResponse({
          active: true,
          document: {
            id: 'doc-route',
            filename: 'route.pdf',
          },
        })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage({
      pathname: '/',
      state: {
        loadForChatDocument: {
          id: 'doc-route',
          filename: 'route.pdf',
        },
      },
    })

    expect(await screen.findByText('route-session')).toBeInTheDocument()

    await waitFor(() => {
      expect(
        chatDocumentChangedSpy.mock.calls.filter(
          ([event]) => (event as CustomEvent).detail?.active === true,
        ),
      ).toHaveLength(1)
    })

    expect(chatRenderSpy).toHaveBeenCalled()
    const dispatchedEvent = chatDocumentChangedSpy.mock.calls.find(
      ([event]) => (event as CustomEvent).detail?.active === true,
    )?.[0] as CustomEvent
    expect(dispatchedEvent.detail).toMatchObject({
      active: true,
      document: {
        id: 'doc-route',
        filename: 'route.pdf',
      },
    })

    window.removeEventListener('chat-document-changed', chatDocumentChangedSpy as EventListener)
  })

  it('keeps a newer upload document when an older route load resolves last', async () => {
    actualChatMode.enabled = true
    const staleRouteLoad = deferred<Response>()
    const loadBodies: Array<Record<string, unknown>> = []
    const viewerDocumentChangedSpy = vi.fn()
    window.addEventListener(
      'pdf-viewer-document-changed',
      viewerDocumentChangedSpy as EventListener,
    )

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/api/chat/session') {
        return jsonResponse({
          session_id: 'route-session',
          created_at: '2026-05-07T15:00:00Z',
          updated_at: '2026-05-07T15:00:00Z',
          active_document: null,
        })
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({ active: false, document: null })
      }

      if (url === '/api/chat/document') {
        return jsonResponse({ active: false, document: null })
      }

      if (url === '/api/chat/document/load') {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>
        loadBodies.push(body)
        if (body.document_id === 'doc-route') {
          return staleRouteLoad.promise
        }
        if (body.document_id === 'doc-upload') {
          return jsonResponse({
            active: true,
            document: { id: 'doc-upload', filename: 'upload.pdf' },
          })
        }
      }

      if (url === '/api/chat/conversation/reset') {
        return jsonResponse({ session_id: 'route-session-reset' })
      }

      if (url === '/api/chat/conversation') {
        return jsonResponse({
          is_active: true,
          memory_stats: { memory_sizes: { short_term: { file_count: 0, size_mb: 0 } } },
        })
      }

      if (url === '/health/deep') {
        return jsonResponse({ services: { weaviate: 'connected', curation_db: 'connected' } })
      }

      if (url === '/api/pdf-viewer/documents/doc-upload') {
        return jsonResponse({ filename: 'upload.pdf', page_count: 4 })
      }

      if (url === '/api/pdf-viewer/documents/doc-upload/url') {
        return jsonResponse({ viewer_url: '/viewer/doc-upload' })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage({
      pathname: '/',
      state: {
        loadForChatDocument: { id: 'doc-route', filename: 'route.pdf' },
      },
    })

    await waitFor(() => {
      expect(loadBodies).toEqual([
        expect.objectContaining({ document_id: 'doc-route' }),
      ])
    })

    const uploadOperation = beginChatDocumentIntent()
    const uploadPayload = await loadDocumentForChat('doc-upload', {
      signal: uploadOperation.signal,
      intentOwner: uploadOperation.owner,
      intentGeneration: uploadOperation.generation,
    })
    expect(uploadOperation.ownsLatest()).toBe(true)
    dispatchChatDocumentChanged(uploadPayload)

    expect(await screen.findByText('Active PDF: upload.pdf')).toBeInTheDocument()
    await waitFor(() => {
      expect(viewerDocumentChangedSpy).toHaveBeenCalledTimes(1)
    })

    staleRouteLoad.resolve(jsonResponse({
      active: true,
      document: { id: 'doc-route', filename: 'route.pdf' },
    }))
    await act(async () => staleRouteLoad.promise)

    expect(loadBodies).toHaveLength(2)
    expect(loadBodies.map((body) => body.document_id)).toEqual(['doc-route', 'doc-upload'])
    expect(loadBodies[0].intent_owner).toBe(loadBodies[1].intent_owner)
    expect(Number(loadBodies[1].intent_generation)).toBeGreaterThan(
      Number(loadBodies[0].intent_generation),
    )
    expect(screen.getByText('Active PDF: upload.pdf')).toBeInTheDocument()
    expect(screen.queryByText('Active PDF: route.pdf')).not.toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('doc-upload')
    expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('doc-upload')
    expect(viewerDocumentChangedSpy).toHaveBeenCalledTimes(1)
    expect((viewerDocumentChangedSpy.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      documentId: 'doc-upload',
      viewerUrl: '/viewer/doc-upload',
    })

    window.removeEventListener(
      'pdf-viewer-document-changed',
      viewerDocumentChangedSpy as EventListener,
    )
  })

  it('shows a viewer restore error instead of a stale timeout after route-state backend load succeeds', async () => {
    actualChatMode.enabled = true
    const viewerMetadataResponse = deferred<Response>()
    const viewerRestoreMessage = 'Document loaded for chat, but the PDF viewer could not be restored. Failed to fetch document viewer metadata'
    const realSetTimeout = window.setTimeout.bind(window)
    const realClearTimeout = window.clearTimeout.bind(window)
    const clearedTimeouts = new Set<number>()
    let nextSyntheticTimerId = 100000
    let loadingTimeoutId: number | undefined
    let loadingTimeoutCallback: (() => void) | undefined

    vi.spyOn(window, 'setTimeout').mockImplementation(((
      handler: TimerHandler,
      timeout?: number,
      ...args: unknown[]
    ) => {
      if (timeout === 30000) {
        const timerId = nextSyntheticTimerId
        nextSyntheticTimerId += 1
        loadingTimeoutId = timerId
        loadingTimeoutCallback = () => {
          if (typeof handler === 'function') {
            handler(...args)
          }
        }
        return timerId
      }

      return realSetTimeout(handler, timeout, ...(args as []))
    }) as typeof window.setTimeout)
    vi.spyOn(window, 'clearTimeout').mockImplementation(((timerId?: number) => {
      if (typeof timerId === 'number' && timerId >= 100000) {
        clearedTimeouts.add(timerId)
        return
      }

      realClearTimeout(timerId)
    }) as typeof window.clearTimeout)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/api/chat/session') {
        expect(init?.method).toBe('POST')
        return jsonResponse({
          session_id: 'route-session',
          created_at: '2026-05-07T15:00:00Z',
          updated_at: '2026-05-07T15:00:00Z',
          active_document: null,
        })
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      if (url === '/api/chat/document') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      if (url === '/api/chat/conversation') {
        return jsonResponse({
          is_active: true,
          memory_stats: {
            memory_sizes: {
              short_term: { file_count: 0, size_mb: 0 },
            },
          },
        })
      }

      if (url === '/health/deep') {
        return jsonResponse({
          services: {
            weaviate: 'connected',
            curation_db: 'connected',
          },
        })
      }

      if (url === '/api/chat/document/load') {
        expect(init?.method).toBe('POST')
        return jsonResponse({
          active: true,
          document: {
            id: 'doc-route',
            filename: 'route.pdf',
          },
        })
      }

      if (url === '/api/chat/conversation/reset') {
        return jsonResponse({
          session_id: 'route-session-reset',
        })
      }

      if (url === '/api/pdf-viewer/documents/doc-route') {
        return viewerMetadataResponse.promise
      }

      if (url === '/api/pdf-viewer/documents/doc-route/url') {
        return jsonResponse({
          viewer_url: '/viewer/doc-route',
        })
      }

      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage({
      pathname: '/',
      state: {
        loadForChatDocument: {
          id: 'doc-route',
          filename: 'route.pdf',
        },
      },
    })

    // Hold the response until the loading effect has armed its timer. An
    // immediately resolved mock can be batched with loading=false/error and
    // skip the timer entirely, which does not exercise stale-timeout cleanup.
    await waitFor(() => expect(loadingTimeoutId).toBeDefined())
    viewerMetadataResponse.resolve(jsonResponse({ detail: 'viewer metadata missing' }, 500))
    expect(await screen.findByText(viewerRestoreMessage)).toBeInTheDocument()
    expect(clearedTimeouts.has(loadingTimeoutId!)).toBe(true)

    await act(async () => {
      if (loadingTimeoutId === undefined || !clearedTimeouts.has(loadingTimeoutId)) {
        loadingTimeoutCallback?.()
      }
    })

    expect(screen.getByText(viewerRestoreMessage)).toBeInTheDocument()
    expect(screen.queryByText(/Document loading timed out/i)).not.toBeInTheDocument()
    expect(sessionStorage.getItem(DOCUMENT_LOADING_STORAGE_KEY)).toBeNull()
  })

  it('clears document loading storage and emits an error when the handoff safety timeout fires', async () => {
    const timeoutMessage = 'Document loading timed out before the chat handoff completed. The PDF may still be processing, unavailable, or too large.'
    const loadErrorSpy = vi.fn()
    const realSetTimeout = window.setTimeout.bind(window)
    let loadingTimeoutCallback: (() => void) | undefined

    window.addEventListener(DOCUMENT_LOAD_ERROR_EVENT, loadErrorSpy as EventListener)
    sessionStorage.setItem(DOCUMENT_LOADING_STORAGE_KEY, 'true')

    vi.spyOn(window, 'setTimeout').mockImplementation(((
      handler: TimerHandler,
      timeout?: number,
      ...args: unknown[]
    ) => {
      if (timeout === 30000) {
        loadingTimeoutCallback = () => {
          if (typeof handler === 'function') {
            handler(...args)
          }
        }
        return 30000
      }

      return realSetTimeout(handler, timeout, ...(args as []))
    }) as typeof window.setTimeout)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/api/chat/session') {
        expect(init?.method).toBe('POST')
        return jsonResponse({
          session_id: 'timeout-session',
          created_at: '2026-05-07T15:00:00Z',
          updated_at: '2026-05-07T15:00:00Z',
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

    renderHomePage()

    expect(await screen.findByText('timeout-session')).toBeInTheDocument()
    expect(loadingTimeoutCallback).toBeDefined()

    await act(async () => {
      loadingTimeoutCallback?.()
    })

    expect(await screen.findByText(timeoutMessage)).toBeInTheDocument()
    expect(sessionStorage.getItem(DOCUMENT_LOADING_STORAGE_KEY)).toBeNull()
    expect(loadErrorSpy).toHaveBeenCalledTimes(1)
    expect((loadErrorSpy.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      message: timeoutMessage,
    })

    window.removeEventListener(DOCUMENT_LOAD_ERROR_EVENT, loadErrorSpy as EventListener)
  })

  it('preserves non-text durable transcript rows when restoring a requested session', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === buildAssistantHistoryDetailUrl('session-rich')) {
        return jsonResponse({
          session: {
            session_id: 'session-rich',
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:05:00Z',
            recent_activity_at: '2026-04-20T00:05:00Z',
          },
          active_document: null,
          messages: [
            {
              message_id: 'download-1',
              session_id: 'session-rich',
              turn_id: 'turn-1',
              role: 'flow',
              message_type: 'file_download',
              content: 'Generated file: export.tsv',
              payload_json: {
                type: 'FILE_READY',
                details: {
                  file_id: 'file-1',
                  filename: 'export.tsv',
                  format: 'tsv',
                  download_url: '/api/files/file-1/download',
                  size_bytes: 128,
                  mime_type: 'text/tab-separated-values',
                  created_at: '2026-04-20T00:02:00Z',
                },
              },
              trace_id: 'trace-file',
              created_at: '2026-04-20T00:02:00Z',
            },
            {
              message_id: 'flow-1',
              session_id: 'session-rich',
              turn_id: 'turn-1',
              role: 'flow',
              message_type: 'flow_step_evidence',
              content: 'Flow step 2 captured 1 evidence quote.',
              payload_json: {
                flow_id: 'flow-1',
                flow_name: 'Evidence flow',
                flow_run_id: 'run-1',
                step: 2,
                tool_name: 'extract_evidence',
                agent_id: 'agent-1',
                agent_name: 'Evidence Agent',
                evidence_records: [
                  {
                    entity: 'TP53',
                    verified_quote: 'TP53 expression was elevated.',
                    page: 4,
                    section: 'Results',
                    chunk_id: 'chunk-1',
                  },
                ],
                evidence_count: 1,
                total_evidence_records: 1,
              },
              trace_id: 'trace-flow',
              created_at: '2026-04-20T00:03:00Z',
            },
          ],
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

    renderHomePage('/?session=session-rich')

    expect(await screen.findByText('session-rich')).toBeInTheDocument()

    const storedMessages = JSON.parse(localStorage.getItem(chatStorageKeys.messages) ?? '{}')
    expect(storedMessages).toEqual({
      session_id: 'session-rich',
      messages: [
        {
          id: 'download-1',
          role: 'assistant',
          content: 'Generated file: export.tsv',
          timestamp: '2026-04-20T00:02:00Z',
          traceIds: ['trace-file'],
          turnId: 'turn-1',
          type: 'file_download',
          fileData: {
            file_id: 'file-1',
            filename: 'export.tsv',
            format: 'tsv',
            download_url: '/api/files/file-1/download',
            size_bytes: 128,
            mime_type: 'text/tab-separated-values',
            created_at: '2026-04-20T00:02:00Z',
          },
        },
        {
          id: 'flow-1',
          role: 'flow',
          content: 'Flow step 2 captured 1 evidence quote.',
          timestamp: '2026-04-20T00:03:00Z',
          traceIds: ['trace-flow'],
          turnId: 'turn-1',
          flowStepEvidence: {
            flow_id: 'flow-1',
            flow_name: 'Evidence flow',
            flow_run_id: 'run-1',
            step: 2,
            tool_name: 'extract_evidence',
            agent_id: 'agent-1',
            agent_name: 'Evidence Agent',
            evidence_records: [
              {
                entity: 'TP53',
                verified_quote: 'TP53 expression was elevated.',
                page: 4,
                section: 'Results',
                chunk_id: 'chunk-1',
              },
            ],
            evidence_count: 1,
            total_evidence_records: 1,
          },
          evidenceRecords: [
            {
              entity: 'TP53',
              verified_quote: 'TP53 expression was elevated.',
              page: 4,
              section: 'Results',
              chunk_id: 'chunk-1',
            },
          ],
        },
      ],
    })
  })

  it('mounts the real chat and preserves session resume with PDF restore together', async () => {
    actualChatMode.enabled = true

    const pdfDocumentChangedSpy = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)

    try {
      vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)

        if (url === buildAssistantHistoryDetailUrl('session-42')) {
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

        if (url === '/api/chat/document') {
          return jsonResponse({
            active: true,
            document: {
              id: 'doc-42',
              filename: 'resume.pdf',
            },
          })
        }

        if (url === '/api/chat/conversation') {
          return jsonResponse({
            is_active: true,
            memory_stats: {
              memory_sizes: {
                short_term: { file_count: 1, size_mb: 0.1 },
              },
            },
          })
        }

        if (url === '/health/deep') {
          return jsonResponse({
            services: {
              weaviate: 'connected',
              curation_db: 'connected',
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

      expect(await screen.findByText('Restored response')).toBeInTheDocument()
      expect(await screen.findByText('Active PDF: resume.pdf')).toBeInTheDocument()

      await waitFor(() => {
        expect(pdfDocumentChangedSpy).toHaveBeenCalled()
      })

      expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('session-42')
      expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('"documentId":"doc-42"')
      expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('"id":"doc-42"')
    } finally {
      window.removeEventListener('pdf-viewer-document-changed', pdfDocumentChangedSpy as EventListener)
    }
  })

  it('waits for user.uid before hydrating a requested durable session', async () => {
    authState = { user: null }

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === buildAssistantHistoryDetailUrl('session-auth')) {
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
        ([url]) => String(url) === buildAssistantHistoryDetailUrl('session-auth'),
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
        expect(init?.method).toBe('POST')
        expect(init?.body).toBe(JSON.stringify({ chat_kind: 'assistant_chat' }))
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

    expect(resolveCreateSession).not.toBeNull()
    resolveCreateSession!(jsonResponse({
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

      if (url === buildAssistantHistoryDetailUrl('deleted-session')) {
        return jsonResponse({ detail: 'Chat session not found' }, 404)
      }

      if (url === buildAssistantHistoryDetailUrl('new-session-1')) {
        return jsonResponse(buildTranscriptPage('new-session-1', [], null))
      }

      if (url === '/api/chat/document' && init?.method === 'DELETE') {
        return jsonResponse({
          active: false,
          document: null,
        })
      }

      if (url === '/api/chat/session') {
        expect(init?.method).toBe('POST')
        expect(init?.body).toBe(JSON.stringify({ chat_kind: 'assistant_chat' }))
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
        expect(init?.method).toBe('POST')
        expect(init?.body).toBe(JSON.stringify({ chat_kind: 'assistant_chat' }))
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

function buildTranscriptMessage(sessionId: string, index: number) {
  const minute = String(Math.floor(index / 60)).padStart(2, '0')
  const second = String(index % 60).padStart(2, '0')
  return {
    message_id: `${sessionId}-message-${index}`,
    session_id: sessionId,
    turn_id: `${sessionId}-turn-${Math.floor(index / 2)}`,
    role: index % 2 === 0 ? 'user' : 'assistant',
    message_type: 'text',
    content: `${sessionId} message ${index}`,
    created_at: `2026-04-20T01:${minute}:${second}Z`,
  }
}

function buildTranscriptPage(
  sessionId: string,
  messages: Array<ReturnType<typeof buildTranscriptMessage>>,
  nextCursor: string | null,
) {
  return {
    session: {
      session_id: sessionId,
      created_at: '2026-04-20T00:00:00Z',
      updated_at: '2026-04-20T00:05:00Z',
      recent_activity_at: '2026-04-20T00:05:00Z',
    },
    active_document: null,
    messages,
    message_limit: 200,
    next_message_cursor: nextCursor,
  }
}

function parseHistoryDetailRequest(url: string): {
  sessionId: string
  cursor: string | null
  limit: number | null
} | null {
  const match = /^\/api\/chat\/history\/([^?]+)\?(.*)$/.exec(url)
  if (!match) {
    return null
  }
  const params = new URLSearchParams(match[2])
  const limit = params.get('message_limit')
  return {
    sessionId: decodeURIComponent(match[1]),
    cursor: params.get('message_cursor'),
    limit: limit ? Number(limit) : null,
  }
}

function realChatSupportResponse(url: string, init?: RequestInit): Response | null {
  if (url === '/api/chat/studio-reminder/config') {
    return jsonResponse({ enabled: false, message_chars: 1800, context_chars: 650, context_messages: 2 })
  }
  if (url === '/api/chat/document' && init?.method === 'DELETE') {
    return jsonResponse({ active: false, document: null })
  }
  if (url === '/api/chat/document') {
    return jsonResponse({ active: false, document: null })
  }
  if (url === '/api/chat/conversation') {
    return jsonResponse({ is_active: true, memory_stats: { memory_sizes: {} } })
  }
  if (url === '/health/deep') {
    return jsonResponse({ services: { weaviate: 'connected', curation_db: 'connected' } })
  }
  return null
}

/**
 * Serves `total` chronological messages for `sessionId` using the server's
 * cursor contract: each page holds at most the requested limit and the cursor
 * names the next message index.
 */
function serveLongTranscript(
  sessionId: string,
  total: number,
  requests: Array<{ cursor: string | null; limit: number | null }>,
) {
  const allMessages = Array.from({ length: total }, (_, index) => buildTranscriptMessage(sessionId, index))
  return (url: string): Response | null => {
    const request = parseHistoryDetailRequest(url)
    if (!request || request.sessionId !== sessionId) {
      return null
    }
    requests.push({ cursor: request.cursor, limit: request.limit })
    const start = request.cursor ? Number(request.cursor.replace('cursor-', '')) : 0
    const pageSize = request.limit ?? DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT
    const end = Math.min(start + pageSize, total)
    return jsonResponse(buildTranscriptPage(
      sessionId,
      allMessages.slice(start, end),
      end < total ? `cursor-${end}` : null,
    ))
  }
}

describe('HomePage resumed transcript hydration', () => {
  const chatStorageKeys = getChatLocalStorageKeys('user-1')

  beforeEach(() => {
    actualChatMode.enabled = true
    localStorage.clear()
    sessionStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    chatRenderSpy.mockReset()
    rightPanelRenderSpy.mockReset()
    vi.mocked(global.fetch).mockReset()
    chatStreamStub.sendMessage.mockReset()
    mockUseAuth.mockImplementation(() => ({ user: { uid: 'user-1' } }))
    mockUseChatStream.mockReturnValue(chatStreamStub)
  })

  afterEach(() => {
    actualChatMode.enabled = false
    vi.unstubAllEnvs()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('resumes the full transcript when Resume chat is clicked on the history page', async () => {
    const requests: Array<{ cursor: string | null; limit: number | null }> = []
    const serveTranscript = serveLongTranscript('session-history', 130, requests)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/chat/history?')) {
        return jsonResponse({
          chat_kind: 'all',
          total_sessions: 1,
          limit: 20,
          next_cursor: null,
          sessions: [{
            session_id: 'session-history',
            chat_kind: 'assistant_chat',
            title: 'Long review',
            active_document_id: null,
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:05:00Z',
            last_message_at: '2026-04-20T00:05:00Z',
            recent_activity_at: '2026-04-20T00:05:00Z',
          }],
        })
      }
      const response = serveTranscript(url) ?? realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <ThemeProvider theme={theme}>
          <MemoryRouter initialEntries={['/history']}>
            <Routes>
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/" element={<HomePage />} />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Resume chat' }))

    expect(await screen.findByText('session-history message 129')).toBeInTheDocument()
    expect(screen.getByText('session-history message 0')).toBeInTheDocument()
    expect(screen.getAllByText(/^session-history message \d+$/)).toHaveLength(130)
    expect(requests.length).toBeGreaterThan(0)
    expect(chatRenderSpy.mock.calls.at(-1)?.[0].sessionId).toBe('session-history')
  })

  it('resumes a chat with more than 100 messages completely and in order', async () => {
    const requests: Array<{ cursor: string | null; limit: number | null }> = []
    const serveTranscript = serveLongTranscript('session-long', 250, requests)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const response = serveTranscript(url) ?? realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-long')

    expect(await screen.findByText('session-long message 249')).toBeInTheDocument()

    // Every cursor page was followed until the server reported no more pages.
    expect(requests.map((request) => request.cursor)).toEqual([
      null,
      ...requests.slice(1).map((request) => request.cursor),
    ])
    expect(requests.at(-1)?.cursor).not.toBeNull()
    const fetchedCount = requests.reduce((count, request) => {
      const start = request.cursor ? Number(request.cursor.replace('cursor-', '')) : 0
      return Math.max(count, Math.min(start + (request.limit ?? 0), 250))
    }, 0)
    expect(fetchedCount).toBe(250)

    // The whole transcript renders once, in chronological order.
    for (const index of [0, 1, 99, 100, 101, 199, 200, 249]) {
      expect(screen.getAllByText(`session-long message ${index}`)).toHaveLength(1)
    }
    const renderedOrder = screen
      .getAllByText(/^session-long message \d+$/)
      .map((element) => Number(element.textContent?.replace('session-long message ', '')))
    expect(renderedOrder).toEqual(Array.from({ length: 250 }, (_, index) => index))
  })

  it('loads the full server transcript when Home opens a stored session without a session link', async () => {
    const requests: Array<{ cursor: string | null; limit: number | null }> = []
    const serveTranscript = serveLongTranscript('session-stored', 250, requests)
    localStorage.setItem(chatStorageKeys.sessionId, 'session-stored')
    // The browser cache only holds the most recent messages and must not win.
    localStorage.setItem(chatStorageKeys.messages, JSON.stringify({
      session_id: 'session-stored',
      messages: [{
        role: 'assistant',
        content: 'stale cached message',
        timestamp: '2026-04-20T00:00:00Z',
        type: 'text',
      }],
    }))

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const response = serveTranscript(url) ?? realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/')

    expect(await screen.findByText('session-stored message 249')).toBeInTheDocument()
    expect(screen.getAllByText(/^session-stored message \d+$/)).toHaveLength(250)
    expect(screen.getByText('session-stored message 0')).toBeInTheDocument()
    expect(screen.queryByText('stale cached message')).not.toBeInTheDocument()
    expect(requests.length).toBeGreaterThan(1)
    expect(chatRenderSpy.mock.calls.at(-1)?.[0].sessionId).toBe('session-stored')
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalledWith('/api/chat/session', expect.anything())
  })

  it('keeps the stored session and shows an error when its transcript cannot load', async () => {
    localStorage.setItem(chatStorageKeys.sessionId, 'session-offline')

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (parseHistoryDetailRequest(url)?.sessionId === 'session-offline') {
        return jsonResponse({ detail: 'Chat history is temporarily unavailable' }, 503)
      }
      const response = realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/')

    expect(await screen.findByText('Chat history is temporarily unavailable')).toBeInTheDocument()
    expect(chatRenderSpy).not.toHaveBeenCalled()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBe('session-offline')
  })

  it('clears a deleted stored session and shows the unavailable warning', async () => {
    localStorage.setItem(chatStorageKeys.sessionId, 'session-deleted')

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (parseHistoryDetailRequest(url)?.sessionId === 'session-deleted') {
        return jsonResponse({ detail: 'Chat session not found' }, 404)
      }
      const response = realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/')

    expect(
      await screen.findByText('This chat session is unavailable. It may have been deleted.'),
    ).toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.sessionId)).toBeNull()
    expect(chatRenderSpy).not.toHaveBeenCalled()
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalledWith('/api/chat/session', expect.anything())
  })

  it('renders the fetched transcript even when browser storage rejects writes', async () => {
    const requests: Array<{ cursor: string | null; limit: number | null }> = []
    const serveTranscript = serveLongTranscript('session-quota', 3, requests)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Quota exceeded', 'QuotaExceededError')
    })

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const response = serveTranscript(url) ?? realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-quota')

    expect(await screen.findByText('session-quota message 2')).toBeInTheDocument()
    expect(screen.getByText('session-quota message 0')).toBeInTheDocument()
    expect(localStorage.getItem(chatStorageKeys.messages)).toBeNull()
  })

  it('renders the fetched transcript even when browser storage is unavailable', async () => {
    const requests: Array<{ cursor: string | null; limit: number | null }> = []
    const serveTranscript = serveLongTranscript('session-blocked', 2, requests)
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Access denied', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Access denied', 'SecurityError')
    })

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const response = serveTranscript(url) ?? realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-blocked')

    expect(await screen.findByText('session-blocked message 1')).toBeInTheDocument()
    expect(screen.getByText('session-blocked message 0')).toBeInTheDocument()
  })

  it('shows a restore error instead of a partial chat when a later page fails', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const request = parseHistoryDetailRequest(url)
      if (request?.sessionId === 'session-broken') {
        if (!request.cursor) {
          return jsonResponse(buildTranscriptPage(
            'session-broken',
            [buildTranscriptMessage('session-broken', 0)],
            'cursor-1',
          ))
        }
        return jsonResponse({ detail: 'Chat history is temporarily unavailable' }, 503)
      }
      const response = realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-broken')

    expect(await screen.findByText('Chat history is temporarily unavailable')).toBeInTheDocument()
    expect(screen.queryByText('session-broken message 0')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Type your message...')).not.toBeInTheDocument()
    expect(chatRenderSpy).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Start new chat' })).toBeInTheDocument()
  })

  it('stops paging at the configured page limit and says so', async () => {
    vi.stubEnv('VITE_AI_CURATION_CHAT_TRANSCRIPT_MAX_PAGES', '3')
    const requests: Array<{ cursor: string | null; limit: number | null }> = []
    const serveTranscript = serveLongTranscript('session-huge', 5000, requests)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const response = serveTranscript(url) ?? realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-huge')

    expect(await screen.findByText(/This chat is too long to restore in full/)).toBeInTheDocument()
    expect(screen.getByText(/more than 600 messages/)).toBeInTheDocument()
    expect(requests).toHaveLength(3)
    expect(chatRenderSpy).not.toHaveBeenCalled()
  })

  it('keeps the latest selected session when an older restore resolves last', async () => {
    const firstPage = deferred<Response>()

    vi.mocked(global.fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const request = parseHistoryDetailRequest(url)
      if (request?.sessionId === 'session-a') {
        return firstPage.promise
      }
      if (request?.sessionId === 'session-b') {
        return Promise.resolve(jsonResponse(buildTranscriptPage(
          'session-b',
          [buildTranscriptMessage('session-b', 0)],
          null,
        )))
      }
      const response = realChatSupportResponse(url, init)
      if (response) {
        return Promise.resolve(response)
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-a')
    fireEvent.click(screen.getByRole('button', { name: 'Restore B' }))

    expect(await screen.findByText('session-b message 0')).toBeInTheDocument()

    firstPage.resolve(jsonResponse(buildTranscriptPage(
      'session-a',
      [buildTranscriptMessage('session-a', 0)],
      null,
    )))
    await act(async () => firstPage.promise)

    expect(screen.getByText('session-b message 0')).toBeInTheDocument()
    expect(screen.queryByText('session-a message 0')).not.toBeInTheDocument()
    expect(chatRenderSpy.mock.calls.at(-1)?.[0].sessionId).toBe('session-b')
  })

  it('replaces a previous session transcript when switching sessions', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const request = parseHistoryDetailRequest(url)
      if (request?.sessionId === 'session-a' || request?.sessionId === 'session-b') {
        return jsonResponse(buildTranscriptPage(
          request.sessionId,
          [buildTranscriptMessage(request.sessionId, 0), buildTranscriptMessage(request.sessionId, 1)],
          null,
        ))
      }
      const response = realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-a')
    expect(await screen.findByText('session-a message 1')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Restore B' }))

    expect(await screen.findByText('session-b message 1')).toBeInTheDocument()
    expect(screen.queryByText('session-a message 0')).not.toBeInTheDocument()
    expect(screen.queryByText('session-a message 1')).not.toBeInTheDocument()
  })

  it('appends a follow-up to the restored durable session', async () => {
    const requests: Array<{ cursor: string | null; limit: number | null }> = []
    const serveTranscript = serveLongTranscript('session-follow', 120, requests)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const response = serveTranscript(url) ?? realChatSupportResponse(url, init)
      if (response) {
        return response
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })

    renderHomePage('/?session=session-follow')
    expect(await screen.findByText('session-follow message 119')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Type your message...'), {
      target: { value: 'Follow-up question' },
    })
    fireEvent.click(document.querySelector('.send-button') as HTMLButtonElement)

    expect(await screen.findByText('Follow-up question')).toBeInTheDocument()
    expect(chatStreamStub.sendMessage).toHaveBeenCalledWith(
      'Follow-up question',
      'session-follow',
      expect.objectContaining({ turnId: expect.any(String) }),
    )
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalledWith('/api/chat/session', expect.anything())
    expect(screen.getByText('session-follow message 0')).toBeInTheDocument()
    expect(screen.getByText('session-follow message 119')).toBeInTheDocument()
  })
})

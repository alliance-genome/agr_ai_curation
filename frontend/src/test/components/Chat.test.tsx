import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { getChatLocalStorageKeys, getChatRenderCacheKeys } from '@/lib/chatCacheKeys'
import { createAppTheme, type ThemeMode } from '@/theme'
import Chat from '../../components/Chat'

const mockNavigate = vi.fn()
const openCurationWorkspaceMock = vi.fn()
const emitGlobalToastMock = vi.fn()
const mockAuthState = {
  user: { uid: 'user-1', email: 'curator@example.org' },
}

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('@/features/curation/navigation/openCurationWorkspace', () => ({
  openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
}))

vi.mock('@/lib/globalNotifications', () => ({
  emitGlobalToast: (detail: unknown) => emitGlobalToastMock(detail),
}))

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => mockAuthState,
}))

const chatStorageKeys = getChatLocalStorageKeys('user-1')
const alternateChatStorageKeys = getChatLocalStorageKeys('user-2')

const CURATION_DB_WARNING =
  'Curation database connection lost - all database queries unavailable'

function mockChatFetch(options?: {
  curationDbStatus?: string
  weaviateStatus?: string
  rejectHealth?: boolean
  prepPreview?: {
    ready: boolean
    summary_text: string
    candidate_count: number
    unscoped_candidate_count: number
    preparable_candidate_count: number
    extraction_result_count: number
    conversation_message_count: number
    adapter_keys: string[]
    discussed_adapter_keys: string[]
    blocking_reasons: string[]
  }
  prepRun?: {
    summary_text: string
    document_id: string
    candidate_count: number
    warnings: string[]
    processing_notes: string[]
    adapter_keys: string[]
    prepared_sessions: Array<{
      session_id: string
      adapter_key: string
      created: boolean
    }>
  }
  activeDocument?: {
    id: string
    filename?: string | null
  }
}) {
  const {
    curationDbStatus = 'connected',
    weaviateStatus = 'connected',
    rejectHealth = false,
    prepPreview,
    prepRun,
    activeDocument,
  } = options ?? {}

  vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)

    if (url === '/health/deep') {
      if (rejectHealth) {
        throw new Error('health fetch failed')
      }

      return {
        ok: true,
        json: async () => ({
          services: {
            weaviate: weaviateStatus,
            curation_db: curationDbStatus,
          },
        }),
      } as Response
    }

    if (url.startsWith('/api/curation-workspace/prep/preview')) {
      return {
        ok: true,
        json: async () => prepPreview ?? {
          ready: false,
          summary_text: 'No candidate annotations are available from this chat yet.',
          candidate_count: 0,
          unscoped_candidate_count: 0,
          preparable_candidate_count: 0,
          extraction_result_count: 0,
          conversation_message_count: 0,
          adapter_keys: [],
          discussed_adapter_keys: [],
          blocking_reasons: [
            'No candidate annotations are available from this chat yet.',
          ],
        },
      } as Response
    }

    if (url === '/api/curation-workspace/prep' && init?.method === 'POST') {
      return {
        ok: true,
        json: async () => prepRun ?? {
          summary_text: 'Prepared 1 candidate annotation for curation review.',
          document_id: 'doc-1',
          candidate_count: 1,
          warnings: [],
          processing_notes: [],
          adapter_keys: ['disease'],
          prepared_sessions: [],
        },
      } as Response
    }

    if (url === '/api/chat/document') {
      return {
        ok: true,
        json: async () => activeDocument
          ? {
              active: true,
              document: activeDocument,
            }
          : {
              active: false,
              document: null,
            },
      } as Response
    }

    if (activeDocument && url === `/api/pdf-viewer/documents/${activeDocument.id}`) {
      return {
        ok: true,
        json: async () => ({
          filename: activeDocument.filename ?? `${activeDocument.id}.pdf`,
          page_count: 7,
        }),
      } as Response
    }

    if (activeDocument && url === `/api/pdf-viewer/documents/${activeDocument.id}/url`) {
      return {
        ok: true,
        json: async () => ({
          viewer_url: `/viewer/${activeDocument.id}`,
        }),
      } as Response
    }

    return {
      ok: true,
      json: async () => ({}),
    } as Response
  })
}

function createDeferredResponse() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((resolvePromise) => {
    resolve = resolvePromise
  })

  return {
    promise,
    resolve,
  }
}

function renderChat(
  props?: Partial<ComponentProps<typeof Chat>>,
  options?: { themeMode?: ThemeMode },
) {
  const sendMessage = props?.sendMessage ?? vi.fn().mockResolvedValue(undefined)
  const mergedProps: ComponentProps<typeof Chat> = {
    sessionId: 'session-1',
    events: [],
    isLoading: false,
    sendMessage,
    onSessionChange: vi.fn(),
    ...props,
  }
  const chat = (
    <MemoryRouter>
      <Chat {...mergedProps} />
    </MemoryRouter>
  )
  const renderedChat = options?.themeMode ? (
    <ThemeProvider theme={createAppTheme(options.themeMode)}>
      {chat}
    </ThemeProvider>
  ) : chat

  return {
    ...render(renderedChat),
    sendMessage,
  }
}

describe('Chat persistence', () => {
  beforeEach(() => {
    mockAuthState.user = { uid: 'user-1', email: 'curator@example.org' }
    localStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    emitGlobalToastMock.mockReset()
    mockChatFetch()
    vi.useRealTimers()
  })

  it('persists pending chat data on unmount and restores it on remount', async () => {
    localStorage.setItem(chatStorageKeys.sessionId, 'session-1')
    const { unmount, sendMessage } = renderChat({ sessionId: 'session-1' })

    const input = screen.getByPlaceholderText('Type your message...')
    fireEvent.change(input, { target: { value: 'Persist me across navigation' } })
    fireEvent.keyPress(input, { key: 'Enter', code: 'Enter', charCode: 13 })

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith(
        'Persist me across navigation',
        'session-1',
        expect.objectContaining({
          turnId: expect.any(String),
        }),
      )
    })

    // Simulate navigating away from Home before debounce timer naturally fires.
    unmount()

    const storedRaw = localStorage.getItem(chatStorageKeys.messages)
    expect(storedRaw).not.toBeNull()
    const stored = JSON.parse(storedRaw || '{}')
    expect(stored.session_id).toBe('session-1')
    expect(stored.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: 'user',
          content: 'Persist me across navigation',
        }),
      ])
    )

    renderChat({ sessionId: 'session-1' })

    expect(screen.getByText('Persist me across navigation')).toBeInTheDocument()
  })

  it('does not delete stored messages when session id mismatches', () => {
    localStorage.setItem(chatStorageKeys.sessionId, 'session-2')
    localStorage.setItem(
      chatStorageKeys.messages,
      JSON.stringify({
        session_id: 'session-1',
        messages: [
          {
            role: 'user',
            content: 'Old session message',
            timestamp: new Date().toISOString(),
          },
        ],
      })
    )

    renderChat({ sessionId: 'session-2' })

    expect(localStorage.getItem(chatStorageKeys.messages)).not.toBeNull()
  })

  it('replaces restored messages that have no displayable content and logs the missing data', () => {
    const missingContentSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.setItem(chatStorageKeys.sessionId, 'session-1')
    localStorage.setItem(
      chatStorageKeys.messages,
      JSON.stringify({
        session_id: 'session-1',
        messages: [
          {
            role: 'assistant',
            timestamp: new Date().toISOString(),
            turnId: 'turn-empty-1',
          },
        ],
      }),
    )

    renderChat({ sessionId: 'session-1' })

    expect(screen.getByText('[Message content unavailable]')).toBeInTheDocument()
    expect(missingContentSpy).toHaveBeenCalledWith(
      '[Chat] Restored message was missing display content:',
      expect.objectContaining({
        turnId: 'turn-empty-1',
      }),
    )
    missingContentSpy.mockRestore()
  })

  it('clears the previous user chat state before a user switch can persist it into the next namespace', async () => {
    vi.useFakeTimers()
    localStorage.setItem(
      chatStorageKeys.messages,
      JSON.stringify({
        session_id: 'session-1',
        messages: [
          {
            role: 'user',
            content: 'User one message',
            timestamp: new Date().toISOString(),
          },
        ],
      }),
    )

    const sendMessage = vi.fn().mockResolvedValue(undefined)
    const onSessionChange = vi.fn()
    const view = render(
      <MemoryRouter>
        <Chat
          sessionId="session-1"
          events={[]}
          isLoading={false}
          sendMessage={sendMessage}
          onSessionChange={onSessionChange}
        />
      </MemoryRouter>,
    )

    expect(screen.getByText('User one message')).toBeInTheDocument()

    mockAuthState.user = { uid: 'user-2', email: 'other@example.org' }
    view.rerender(
      <MemoryRouter>
        <Chat
          sessionId="session-2"
          events={[]}
          isLoading={false}
          sendMessage={sendMessage}
          onSessionChange={onSessionChange}
        />
      </MemoryRouter>,
    )

    await vi.advanceTimersByTimeAsync(600)

    expect(screen.queryByText('User one message')).not.toBeInTheDocument()
    expect(localStorage.getItem(alternateChatStorageKeys.messages)).toBeNull()
    expect(localStorage.getItem(chatStorageKeys.messages)).not.toBeNull()
  })

  it('clears legacy generic review targets for restored evidence messages without curation metadata', async () => {
    localStorage.setItem(chatStorageKeys.sessionId, 'session-1')
    localStorage.setItem(
      chatStorageKeys.messages,
      JSON.stringify({
        session_id: 'session-1',
        messages: [
          {
            role: 'assistant',
            content: 'Legacy extraction output',
            timestamp: new Date().toISOString(),
            reviewAndCurateTarget: {
              documentId: 'doc-7',
              originSessionId: 'session-1',
            },
            evidenceRecords: [
              {
                entity: 'crb 11A22',
                verified_quote: 'Legacy unsupported evidence.',
                page: 1,
                section: 'Results and Discussion',
                chunk_id: 'chunk-legacy-1',
              },
            ],
          },
        ],
      }),
    )
    mockChatFetch({
      activeDocument: {
        id: 'doc-7',
        filename: 'doc-7.pdf',
      },
    })

    renderChat()

    fireEvent.click(await screen.findByRole('button', { name: 'crb 11A22 1' }))

    expect(
      await screen.findByText('Full evidence review with PDF highlighting →'),
    ).toBeInTheDocument()
    expect(screen.queryByText('Review & Curate')).not.toBeInTheDocument()
  })

  it('rehydrates a backend active document into local storage and the PDF viewer on mount', async () => {
    const listener = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', listener as EventListener)

    try {
      mockChatFetch({
        activeDocument: {
          id: 'doc-7',
          filename: 'doc-7.pdf',
        },
      })

      renderChat()

      expect(await screen.findByText('Active PDF: doc-7.pdf')).toBeInTheDocument()

      await waitFor(() => {
        expect(listener).toHaveBeenCalled()
      })

      expect(localStorage.getItem(chatStorageKeys.activeDocument)).toContain('"id":"doc-7"')
      expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('"documentId":"doc-7"')

      const pdfEvent = listener.mock.calls.at(-1)?.[0] as CustomEvent
      expect(pdfEvent.detail).toMatchObject({
        documentId: 'doc-7',
        viewerUrl: '/viewer/doc-7',
        filename: 'doc-7.pdf',
        pageCount: 7,
      })
    } finally {
      window.removeEventListener('pdf-viewer-document-changed', listener as EventListener)
    }
  })

  it('does not dispatch a late PDF restore after Chat unmounts', async () => {
    const listener = vi.fn()
    const detailResponse = createDeferredResponse()
    const urlResponse = createDeferredResponse()
    window.addEventListener('pdf-viewer-document-changed', listener as EventListener)

    try {
      vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input)

        if (url === '/health/deep') {
          return {
            ok: true,
            json: async () => ({
              services: {
                weaviate: 'connected',
                curation_db: 'connected',
              },
            }),
          } as Response
        }

        if (url === '/api/chat/conversation') {
          return {
            ok: true,
            json: async () => ({
              is_active: true,
              memory_stats: {
                memory_sizes: {
                  short_term: { file_count: 1, size_mb: 0.1 },
                },
              },
            }),
          } as Response
        }

        if (url === '/api/chat/document') {
          return {
            ok: true,
            json: async () => ({
              active: true,
              document: {
                id: 'doc-late',
                filename: 'doc-late.pdf',
              },
            }),
          } as Response
        }

        if (url === '/api/pdf-viewer/documents/doc-late') {
          return detailResponse.promise
        }

        if (url === '/api/pdf-viewer/documents/doc-late/url') {
          return urlResponse.promise
        }

        return {
          ok: true,
          json: async () => ({}),
        } as Response
      })

      const { unmount } = renderChat()

      expect(await screen.findByText('Active PDF: doc-late.pdf')).toBeInTheDocument()

      unmount()

      await act(async () => {
        detailResponse.resolve({
          ok: true,
          json: async () => ({
            filename: 'doc-late.pdf',
            page_count: 7,
          }),
        } as Response)
        urlResponse.resolve({
          ok: true,
          json: async () => ({
            viewer_url: '/viewer/doc-late',
          }),
        } as Response)
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(listener).not.toHaveBeenCalled()
      expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toBeNull()
    } finally {
      window.removeEventListener('pdf-viewer-document-changed', listener as EventListener)
    }
  })

  it('does not dispatch pdf overlay updates for chunk provenance metadata', async () => {
    const listener = vi.fn()
    window.addEventListener('pdf-overlay-update', listener as EventListener)

    try {
      renderChat({
        events: [
          {
            type: 'CHUNK_PROVENANCE',
            chunk_id: 'chunk-42',
            document_id: 'doc-7',
            doc_items: [
              {
                page_no: 4,
                bbox: { left: 11, top: 22, right: 33, bottom: 5, coord_origin: 'BOTTOMLEFT' },
              },
            ],
          },
          {
            type: 'TEXT_MESSAGE_CONTENT',
            content: 'Chunk provenance was ignored for viewer overlays.',
          },
        ],
      })

      expect(
        await screen.findByText('Chunk provenance was ignored for viewer overlays.'),
      ).toBeInTheDocument()
      expect(listener).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('pdf-overlay-update', listener as EventListener)
    }
  })

  it('copies user messages with the shared clipboard fallback helper path', async () => {
    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('blocked'))
    const originalExecCommand = (document as Document & { execCommand?: typeof document.execCommand }).execCommand
    const execCommandSpy = vi.fn(() => true)
    Object.assign(document, { execCommand: execCommandSpy })

    const { sendMessage } = renderChat({ sessionId: 'session-1' })

    const input = screen.getByPlaceholderText('Type your message...')
    fireEvent.change(input, { target: { value: 'Copy this user message' } })
    fireEvent.keyPress(input, { key: 'Enter', code: 'Enter', charCode: 13 })

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith(
        'Copy this user message',
        'session-1',
        expect.objectContaining({
          turnId: expect.any(String),
        }),
      )
    })

    expect(await screen.findByText('Copy this user message')).toBeInTheDocument()

    fireEvent.click(screen.getByTitle('Copy to clipboard'))

    await waitFor(() => {
      expect(writeTextSpy).toHaveBeenCalledWith('Copy this user message')
      expect(execCommandSpy).toHaveBeenCalledWith('copy')
    })

    writeTextSpy.mockRestore()
    if (originalExecCommand) {
      Object.assign(document, { execCommand: originalExecCommand })
    } else {
      Object.assign(document, {
        execCommand: undefined as unknown as typeof document.execCommand,
      })
    }
  })

  it('attaches evidence summaries to the latest assistant message', async () => {
    renderChat({
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: 'Extraction complete for the highlighted entities.',
        },
        {
          type: 'evidence_summary',
          curation_supported: true,
          curation_adapter_key: 'gene',
          evidence_records: [
            {
              entity: 'crumb',
              verified_quote: 'Crumb is essential for maintaining epithelial polarity.',
              page: 4,
              section: 'Results',
              subsection: 'Gene Expression Analysis',
              chunk_id: 'chunk-1',
              figure_reference: 'Figure 2A',
            },
          ],
        },
      ],
    })

    expect(
      await screen.findByText('Extraction complete for the highlighted entities.')
    ).toBeInTheDocument()
    expect(await screen.findByText('1 evidence quotes')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'crumb 1' }))

    expect(
      await screen.findByRole('button', {
        name: 'Highlight evidence on PDF: Crumb is essential for maintaining epithelial polarity.',
      })
    ).toBeInTheDocument()
  })

  it('removes duplicate inline evidence sections once evidence records are attached', async () => {
    renderChat({
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: [
            'The genes that are the focus of this publication are:',
            '',
            '1. **crumbs (crb)**',
            '   - Normalized ID: FB:FBgn0000368',
            '   - Evidence: Changes in molecular organization following abnormal PRC development in crumbs mutants.',
            '',
            '**Citations:**',
            '- Section: Results and Discussion, Page: 1',
            '',
            '**Sources:**',
            '- Gene Extraction Analysis',
          ].join('\n'),
        },
        {
          type: 'evidence_summary',
          evidence_records: [
            {
              entity: 'crumbs',
              verified_quote: 'Changes in molecular organization following abnormal PRC development in crumbs mutants.',
              page: 1,
              section: 'Results and Discussion',
              chunk_id: 'chunk-1',
            },
          ],
        },
      ],
    })

    expect(
      await screen.findByText(/The genes that are the focus of this publication are:/)
    ).toBeInTheDocument()
    expect(screen.queryByText(/Evidence:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Citations:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Sources:/)).not.toBeInTheDocument()
    expect(await screen.findByText('1 evidence quotes')).toBeInTheDocument()
  })
  it('shows the evidence footer action for streamed assistant messages with an active document', async () => {
    openCurationWorkspaceMock.mockResolvedValueOnce('curation-session-evidence')
    mockChatFetch({
      activeDocument: {
        id: 'doc-7',
        filename: 'doc-7.pdf',
      },
    })

    renderChat({
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: 'Extraction complete for the highlighted entities.',
        },
        {
          type: 'evidence_summary',
          curation_supported: true,
          curation_adapter_key: 'gene',
          evidence_records: [
            {
              entity: 'crumb',
              verified_quote: 'Crumb is essential for maintaining epithelial polarity.',
              page: 4,
              section: 'Results',
              subsection: 'Gene Expression Analysis',
              chunk_id: 'chunk-1',
            },
          ],
        },
      ],
    })

    fireEvent.click(await screen.findByRole('button', { name: 'crumb 1' }))

    expect(
      await screen.findByText('Full evidence review with PDF highlighting →')
    ).toBeInTheDocument()

    fireEvent.click(screen.getByText('Review & Curate'))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          documentId: 'doc-7',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })
  })

  it('shows the unsupported curation message for evidence-only generic extraction results', async () => {
    renderChat({
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: 'The publication mentions three transgenic fly lines central to the experiments.',
        },
        {
          type: 'evidence_summary',
          curation_supported: false,
          evidence_records: [
            {
              entity: 'crb 11A22',
              verified_quote: 'crb 11A22 and crb p13A9.',
              page: 1,
              section: 'Results and Discussion',
              chunk_id: 'chunk-unsupported-1',
            },
          ],
        },
      ],
    })

    fireEvent.click(await screen.findByRole('button', { name: 'crb 11A22 1' }))
    fireEvent.click(screen.getByText('Review & Curate'))

    expect(openCurationWorkspaceMock).not.toHaveBeenCalled()
    expect(emitGlobalToastMock).toHaveBeenCalledWith({
      message:
        "This data type is not supported for curation review yet. Review & Curate currently supports only findings from supported specialized agents in Agent Studio's PDF Extraction category.",
      severity: 'warning',
    })
  })

  it('does not show the curation DB outage warning when the service is not configured', async () => {
    mockChatFetch({ curationDbStatus: 'not_configured' })

    renderChat()

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/health/deep')
    })

    expect(screen.queryByText(CURATION_DB_WARNING)).not.toBeInTheDocument()
  })

  it.each(['disconnected', 'error'])(
    'shows the curation DB outage warning when /health/deep reports %s',
    async (curationDbStatus) => {
      mockChatFetch({ curationDbStatus })

      renderChat()

      await waitFor(() => {
        expect(screen.getByText(CURATION_DB_WARNING)).toBeInTheDocument()
      })
    }
  )

  it('shows the weaviate outage warning when /health/deep reports it as disconnected', async () => {
    mockChatFetch({ weaviateStatus: 'disconnected' })

    renderChat()

    await waitFor(() => {
      expect(
        screen.getByText('Weaviate database connection lost - PDF search unavailable')
      ).toBeInTheDocument()
    })
  })

  it('always shows the Prepare for Curation button', () => {
    renderChat()

    expect(
      screen.getByRole('button', { name: /prepare for curation/i })
    ).toBeInTheDocument()
  })

  it('shows the durable session ID controls in the header when a session exists', () => {
    renderChat({ sessionId: 'session-1' })

    expect(screen.getByText('Session:')).toBeInTheDocument()
    expect(screen.getByText('session-1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy session ID' })).toBeInTheDocument()
  })

  it('does not render session ID controls when there is no session ID', () => {
    renderChat({ sessionId: null })

    expect(screen.queryByText('Session:')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copy session ID' })).not.toBeInTheDocument()
  })

  it('copies the durable session ID from the header and clears the feedback after 1.5 seconds', async () => {
    const user = userEvent.setup()

    renderChat({ sessionId: 'session-1' })

    await user.click(screen.getByRole('button', { name: 'Copy session ID' }))

    expect(screen.getByText('Copied!')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.queryByText('Copied!')).not.toBeInTheDocument()
    }, { timeout: 2500 })
  })

  it('loads prep scope, confirms prep, and triggers the curation prep API', async () => {
    mockChatFetch({
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 4 candidate annotations. Prepare all for curation review?',
        candidate_count: 4,
        unscoped_candidate_count: 0,
        preparable_candidate_count: 4,
        extraction_result_count: 2,
        conversation_message_count: 6,
        adapter_keys: ['disease'],
        discussed_adapter_keys: ['disease'],
        blocking_reasons: [],
      },
      prepRun: {
        summary_text: 'Prepared 2 candidate annotations for curation review.',
        document_id: 'doc-disease-1',
        candidate_count: 2,
        warnings: ['Review warnings are available.'],
        processing_notes: ['Prep completed successfully.'],
        adapter_keys: ['disease'],
        prepared_sessions: [
          {
            session_id: 'curation-session-disease',
            adapter_key: 'disease',
            created: true,
          },
        ],
      },
    })

    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))

    expect(
      await screen.findByText('You discussed 4 candidate annotations. Prepare all for curation review?')
    ).toBeInTheDocument()

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/curation-workspace/prep/preview?session_id=session-1',
      {
        credentials: 'include',
      }
    )

    fireEvent.click(screen.getByRole('button', { name: /start prep/i }))

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/curation-workspace/prep', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          session_id: 'session-1',
          adapter_keys: ['disease'],
        }),
      })
    })

    expect(
      await screen.findByText(/Prepared 2 candidate annotations for curation review\./i)
    ).toBeInTheDocument()
  })

  it('prepares all adapters in scope and opens the first prepared session', async () => {
    openCurationWorkspaceMock.mockResolvedValueOnce('curation-session-gene')
    mockChatFetch({
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 4 candidate annotations across gene and disease adapters. Prepare all for curation review?',
        candidate_count: 4,
        unscoped_candidate_count: 0,
        preparable_candidate_count: 4,
        extraction_result_count: 2,
        conversation_message_count: 6,
        adapter_keys: ['gene', 'disease'],
        discussed_adapter_keys: ['gene', 'disease'],
        blocking_reasons: [],
      },
      prepRun: {
        summary_text: 'Prepared 4 candidate annotations for curation review across gene and disease adapters.',
        document_id: 'doc-1',
        candidate_count: 4,
        warnings: [],
        processing_notes: [],
        adapter_keys: ['gene', 'disease'],
        prepared_sessions: [
          {
            session_id: 'curation-session-gene',
            adapter_key: 'gene',
            created: true,
          },
          {
            session_id: 'curation-session-disease',
            adapter_key: 'disease',
            created: true,
          },
        ],
      },
    })

    renderChat({
      sessionId: 'session-1',
    })

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))
    fireEvent.click(await screen.findByRole('button', { name: /start prep/i }))

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/curation-workspace/prep', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          session_id: 'session-1',
          adapter_keys: ['gene', 'disease'],
        }),
      })
    })

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'curation-session-gene',
          documentId: 'doc-1',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          navigate: mockNavigate,
        }),
      )
    })

    expect(
      await screen.findByText(
        /Additional prepared sessions are available in Curation Inventory\./i,
      ),
    ).toBeInTheDocument()
    expect(emitGlobalToastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: 'info',
      }),
    )
  })

  it('warns when prep can continue but the chat also contains unsupported evidence-only findings', async () => {
    mockChatFetch({
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 4 candidate annotations. Prepare all for curation review?',
        candidate_count: 4,
        unscoped_candidate_count: 0,
        preparable_candidate_count: 4,
        extraction_result_count: 2,
        conversation_message_count: 6,
        adapter_keys: ['disease'],
        discussed_adapter_keys: ['disease'],
        blocking_reasons: [],
      },
    })

    renderChat({
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: 'Supported findings are ready.',
        },
        {
          type: 'evidence_summary',
          curation_supported: true,
          curation_adapter_key: 'disease',
          evidence_records: [
            {
              entity: 'disease example',
              verified_quote: 'Supported disease evidence.',
              page: 2,
              section: 'Results',
              chunk_id: 'chunk-supported-1',
            },
          ],
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: 'Generic PDF extraction also found unsupported data.',
        },
        {
          type: 'evidence_summary',
          curation_supported: false,
          evidence_records: [
            {
              entity: 'transgenic line',
              verified_quote: 'Unsupported transgenic line evidence.',
              page: 3,
              section: 'Methods',
              chunk_id: 'chunk-unsupported-2',
            },
          ],
        },
      ],
    })

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))

    expect(
      await screen.findByText(
        "This chat also contains data types that are not supported for curation review yet. Prepare for Curation will include only findings from supported specialized agents in Agent Studio's PDF Extraction category.",
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText('You discussed 4 candidate annotations. Prepare all for curation review?'),
    ).toBeInTheDocument()
  })

  it('replaces the empty prep message when the chat only contains unsupported evidence-only findings', async () => {
    mockChatFetch({
      prepPreview: {
        ready: false,
        summary_text: 'No candidate annotations are available from this chat yet.',
        candidate_count: 0,
        unscoped_candidate_count: 0,
        preparable_candidate_count: 0,
        extraction_result_count: 0,
        conversation_message_count: 0,
        adapter_keys: [],
        discussed_adapter_keys: [],
        blocking_reasons: ['No candidate annotations are available from this chat yet.'],
      },
    })

    renderChat({
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: 'Generic PDF extraction found unsupported content.',
        },
        {
          type: 'evidence_summary',
          curation_supported: false,
          evidence_records: [
            {
              entity: 'transgenic line',
              verified_quote: 'Unsupported transgenic line evidence.',
              page: 3,
              section: 'Methods',
              chunk_id: 'chunk-unsupported-3',
            },
          ],
        },
      ],
    })

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))

    expect(
      await screen.findByText(
        "This data type is not supported for curation review yet. Review & Curate currently supports only findings from supported specialized agents in Agent Studio's PDF Extraction category.",
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Extraction runs')
    ).toBeInTheDocument()
  })

  it('shows evidence-backed prep availability instead of contradictory discussed counts', async () => {
    mockChatFetch({
      prepPreview: {
        ready: false,
        summary_text: 'No evidence-verified candidates were available to prepare for curation review.',
        candidate_count: 5,
        unscoped_candidate_count: 0,
        preparable_candidate_count: 0,
        extraction_result_count: 2,
        conversation_message_count: 0,
        adapter_keys: [],
        discussed_adapter_keys: ['gene', 'allele'],
        blocking_reasons: ['No evidence-verified candidates were available to prepare for curation review.'],
      },
    })

    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))

    expect(
      await screen.findByText('No evidence-verified candidates were available to prepare for curation review.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Ready candidates')).toBeInTheDocument()
    expect(screen.getByText('Discussed')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('button', { name: /start prep/i })).toBeDisabled()
  })

  it('opens the curation workspace after prep completes for an active document', async () => {
    openCurationWorkspaceMock
      .mockResolvedValueOnce('curation-session-1')
      .mockResolvedValueOnce('curation-session-1')
    mockChatFetch({
      activeDocument: {
        id: 'doc-1',
        filename: 'doc-1.pdf',
      },
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 2 candidate annotations. Prepare all for curation review?',
        candidate_count: 2,
        unscoped_candidate_count: 0,
        preparable_candidate_count: 2,
        extraction_result_count: 1,
        conversation_message_count: 4,
        adapter_keys: ['gene'],
        discussed_adapter_keys: ['gene'],
        blocking_reasons: [],
      },
      prepRun: {
        summary_text: 'Prepared 2 candidate annotations for curation review.',
        document_id: 'doc-1',
        candidate_count: 2,
        warnings: [],
        processing_notes: [],
        adapter_keys: ['gene'],
        prepared_sessions: [
          {
            session_id: 'curation-session-1',
            adapter_key: 'gene',
            created: true,
          },
        ],
      },
    })

    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))
    fireEvent.click(await screen.findByRole('button', { name: /start prep/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'curation-session-1',
          documentId: 'doc-1',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })

    expect(
      await screen.findByText(/Prepared 2 candidate annotations for curation review\./i)
    ).toBeInTheDocument()

    openCurationWorkspaceMock.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i, hidden: true }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'curation-session-1',
          documentId: 'doc-1',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })
  })

  it('opens the curation workspace after prep completes even when active document state is missing', async () => {
    openCurationWorkspaceMock.mockResolvedValueOnce('curation-session-from-backend')
    mockChatFetch({
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 1 candidate annotation. Prepare all for curation review?',
        candidate_count: 1,
        unscoped_candidate_count: 0,
        preparable_candidate_count: 1,
        extraction_result_count: 1,
        conversation_message_count: 2,
        adapter_keys: ['gene'],
        discussed_adapter_keys: ['gene'],
        blocking_reasons: [],
      },
      prepRun: {
        summary_text: 'Prepared 1 candidate annotation for curation review.',
        document_id: 'doc-from-backend',
        candidate_count: 1,
        warnings: [],
        processing_notes: [],
        adapter_keys: ['gene'],
        prepared_sessions: [
          {
            session_id: 'curation-session-from-backend',
            adapter_key: 'gene',
            created: true,
          },
        ],
      },
    })

    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))
    fireEvent.click(await screen.findByRole('button', { name: /start prep/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'curation-session-from-backend',
          documentId: 'doc-from-backend',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })
  })
})

describe('Chat turn reconciliation', () => {
  beforeEach(() => {
    mockAuthState.user = { uid: 'user-1', email: 'curator@example.org' }
    localStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    emitGlobalToastMock.mockReset()
    mockChatFetch()
  })

  it('shows terminal failure notices on the assistant turn matched by turn_id', async () => {
    renderChat({
      sessionId: 'session-1',
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          session_id: 'session-1',
          turn_id: 'turn-failure-1',
          trace_id: 'trace-failure-1',
          content: 'Partial assistant reply',
        },
        {
          type: 'turn_failed',
          session_id: 'session-1',
          turn_id: 'turn-failure-1',
          trace_id: 'trace-failure-1',
          message: 'Chat completed, but durable side effects could not be saved.',
        },
      ],
    })

    expect(await screen.findByText('Partial assistant reply')).toBeInTheDocument()
    expect(
      await screen.findByText('Chat completed, but durable side effects could not be saved.'),
    ).toBeInTheDocument()
  })

  it('keeps terminal failure notices readable inside assistant bubbles in light mode', async () => {
    const theme = createAppTheme('light')
    renderChat(
      {
        sessionId: 'session-1',
        events: [
          {
            type: 'TEXT_MESSAGE_CONTENT',
            session_id: 'session-1',
            turn_id: 'turn-failure-contrast',
            trace_id: 'trace-failure-contrast',
            content: 'Partial assistant reply',
          },
          {
            type: 'turn_failed',
            session_id: 'session-1',
            turn_id: 'turn-failure-contrast',
            trace_id: 'trace-failure-contrast',
            message: 'Chat completed, but durable side effects could not be saved.',
          },
        ],
      },
      { themeMode: 'light' },
    )

    const notice = await screen.findByRole('alert')

    expect(notice).toHaveTextContent('Chat completed, but durable side effects could not be saved.')
    expect(notice).toHaveStyle({
      background: 'rgba(0, 0, 0, 0.18)',
      color: theme.palette.secondary.contrastText,
    })
  })

  it.each<ThemeMode>(['light', 'dark'])(
    'keeps limit notices readable in %s mode',
    async (themeMode) => {
      const theme = createAppTheme(themeMode)
      const expectedColor = themeMode === 'dark'
        ? theme.palette.info.light
        : theme.palette.info.dark

      renderChat(
        {
          sessionId: 'session-1',
          events: [
            {
              type: 'DOMAIN_WARNING',
              details: {
                applied_limit: 50,
                warnings: ['Filtered to mouse records.'],
              },
            },
          ],
        },
        { themeMode },
      )

      const noticeText = await screen.findByText(
        'Applied limit: 50 | Warnings: Filtered to mouse records.'
      )

      expect(noticeText.parentElement!).toHaveStyle({
        color: expectedColor,
      })
    }
  )

  it('maps RUN_ERROR to assistant failure state for the matching turn', async () => {
    renderChat({
      sessionId: 'session-1',
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          session_id: 'session-1',
          turn_id: 'turn-run-error-1',
          trace_id: 'trace-run-error-1',
          content: 'Starting risky flow',
        },
        {
          type: 'RUN_ERROR',
          session_id: 'session-1',
          turn_id: 'turn-run-error-1',
          trace_id: 'trace-run-error-1',
          message: 'The flow runner reported a timeout.',
        },
      ],
    })

    expect(await screen.findByText('Starting risky flow')).toBeInTheDocument()
    expect(
      await screen.findByText('The flow runner reported a timeout.'),
    ).toBeInTheDocument()
  })

  it('rescues turn_save_failed output exactly once per turn and preserves the streamed content', async () => {
    const rescueBodies: Array<Record<string, unknown>> = []

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/health/deep') {
        return {
          ok: true,
          json: async () => ({
            services: {
              weaviate: 'connected',
              curation_db: 'connected',
            },
          }),
        } as Response
      }

      if (url === '/api/chat/document') {
        return {
          ok: true,
          json: async () => ({
            active: false,
            document: null,
          }),
        } as Response
      }

      if (url === '/api/chat/conversation') {
        return {
          ok: true,
          json: async () => ({
            is_active: true,
          }),
        } as Response
      }

      if (url === '/api/chat/session-1/assistant-rescue' && init?.method === 'POST') {
        rescueBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return {
          ok: true,
          json: async () => ({
            session_id: 'session-1',
            turn_id: 'turn-save-failed-1',
            created: true,
            trace_id: 'trace-rescue-1',
          }),
        } as Response
      }

      return {
        ok: true,
        json: async () => ({}),
      } as Response
    })

    renderChat({
      sessionId: 'session-1',
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          session_id: 'session-1',
          turn_id: 'turn-save-failed-1',
          trace_id: 'trace-stream-1',
          content: 'Buffered assistant reply',
        },
        {
          type: 'turn_save_failed',
          session_id: 'session-1',
          turn_id: 'turn-save-failed-1',
          trace_id: 'trace-stream-1',
          message: 'Chat completed, but the assistant response could not be saved.',
        },
        {
          type: 'turn_save_failed',
          session_id: 'session-1',
          turn_id: 'turn-save-failed-1',
          trace_id: 'trace-stream-1',
          message: 'Chat completed, but the assistant response could not be saved.',
        },
      ],
    })

    expect(await screen.findByText('Buffered assistant reply')).toBeInTheDocument()

    await waitFor(() => {
      expect(rescueBodies).toHaveLength(1)
    })

    expect(rescueBodies[0]).toMatchObject({
      turn_id: 'turn-save-failed-1',
      content: 'Buffered assistant reply',
      trace_id: 'trace-stream-1',
    })

    await waitFor(() => {
      expect(screen.queryByText('Saving this response to chat history...')).not.toBeInTheDocument()
    })
  })

  it('shows a durable save error when assistant rescue fails', async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/health/deep') {
        return {
          ok: true,
          json: async () => ({
            services: {
              weaviate: 'connected',
              curation_db: 'connected',
            },
          }),
        } as Response
      }

      if (url === '/api/chat/document') {
        return {
          ok: true,
          json: async () => ({
            active: false,
            document: null,
          }),
        } as Response
      }

      if (url === '/api/chat/conversation') {
        return {
          ok: true,
          json: async () => ({
            is_active: true,
          }),
        } as Response
      }

      if (url === '/api/chat/session-1/assistant-rescue' && init?.method === 'POST') {
        return {
          ok: false,
          json: async () => ({
            detail: 'database unavailable',
          }),
        } as Response
      }

      return {
        ok: true,
        json: async () => ({}),
      } as Response
    })

    renderChat({
      sessionId: 'session-1',
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          session_id: 'session-1',
          turn_id: 'turn-save-failed-2',
          trace_id: 'trace-stream-2',
          content: 'Rescue me',
        },
        {
          type: 'turn_save_failed',
          session_id: 'session-1',
          turn_id: 'turn-save-failed-2',
          trace_id: 'trace-stream-2',
          message: 'Chat completed, but the assistant response could not be saved.',
        },
      ],
    })

    expect(await screen.findByText('Rescue me')).toBeInTheDocument()
    expect(
      await screen.findByText(
        'This response is shown above, but it could not be saved to chat history: database unavailable',
      ),
    ).toBeInTheDocument()
  })

  it('does not restore rescued assistant output after reset hands off to a new session', async () => {
    let resolveRescue: ((response: Response) => void) | null = null
    const onSessionChange = vi.fn()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/health/deep') {
        return {
          ok: true,
          json: async () => ({
            services: {
              weaviate: 'connected',
              curation_db: 'connected',
            },
          }),
        } as Response
      }

      if (url === '/api/chat/document') {
        return {
          ok: true,
          json: async () => ({
            active: false,
            document: null,
          }),
        } as Response
      }

      if (url === '/api/chat/conversation') {
        return {
          ok: true,
          json: async () => ({
            is_active: true,
          }),
        } as Response
      }

      if (url === '/api/chat/conversation/reset' && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({
            session_id: 'session-2',
          }),
        } as Response
      }

      if (url === '/api/chat/session-1/assistant-rescue' && init?.method === 'POST') {
        return await new Promise<Response>((resolve) => {
          resolveRescue = resolve
        })
      }

      return {
        ok: true,
        json: async () => ({}),
      } as Response
    })

    const user = userEvent.setup()

    renderChat({
      sessionId: 'session-1',
      onSessionChange,
      events: [
        {
          type: 'TEXT_MESSAGE_CONTENT',
          session_id: 'session-1',
          turn_id: 'turn-save-failed-3',
          trace_id: 'trace-stream-3',
          content: 'Do not restore this after reset',
        },
        {
          type: 'turn_save_failed',
          session_id: 'session-1',
          turn_id: 'turn-save-failed-3',
          trace_id: 'trace-stream-3',
          message: 'Chat completed, but the assistant response could not be saved.',
        },
      ],
    })

    expect(await screen.findByText('Do not restore this after reset')).toBeInTheDocument()

    await waitFor(() => {
      expect(resolveRescue).not.toBeNull()
    })

    await user.click(screen.getByRole('button', { name: 'Reset Chat' }))

    await waitFor(() => {
      expect(onSessionChange).toHaveBeenCalledWith('session-2')
    })

    await waitFor(() => {
      expect(screen.queryByText('Do not restore this after reset')).not.toBeInTheDocument()
    })

    await act(async () => {
      resolveRescue?.({
        ok: true,
        json: async () => ({
          session_id: 'session-1',
          turn_id: 'turn-save-failed-3',
          created: true,
          trace_id: 'trace-rescue-3',
        }),
      } as Response)
      await Promise.resolve()
    })

    expect(screen.queryByText('Do not restore this after reset')).not.toBeInTheDocument()
    expect(screen.queryByText('Saving this response to chat history...')).not.toBeInTheDocument()

    confirmSpy.mockRestore()
  })

  it('hands reset chat off through onSessionChange and clears auth-scoped audit caches', async () => {
    const oldAuditCacheKey = getChatRenderCacheKeys('user-1', 'session-1').auditEvents
    const newAuditCacheKey = getChatRenderCacheKeys('user-1', 'session-2').auditEvents
    const onSessionChange = vi.fn()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    localStorage.setItem(oldAuditCacheKey, '[{"type":"SUPERVISOR_START"}]')
    localStorage.setItem(newAuditCacheKey, '[{"type":"SUPERVISOR_COMPLETE"}]')
    localStorage.setItem(chatStorageKeys.sessionId, 'session-1')
    localStorage.setItem(
      chatStorageKeys.messages,
      JSON.stringify({
        session_id: 'session-1',
        messages: [
          {
            role: 'user',
            content: 'Reset me',
            timestamp: new Date().toISOString(),
          },
        ],
      }),
    )

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)

      if (url === '/health/deep') {
        return {
          ok: true,
          json: async () => ({
            services: {
              weaviate: 'connected',
              curation_db: 'connected',
            },
          }),
        } as Response
      }

      if (url === '/api/chat/document') {
        return {
          ok: true,
          json: async () => ({
            active: false,
            document: null,
          }),
        } as Response
      }

      if (url === '/api/chat/conversation/reset' && init?.method === 'POST') {
        return {
          ok: true,
          json: async () => ({
            session_id: 'session-2',
          }),
        } as Response
      }

      if (url === '/api/chat/conversation') {
        return {
          ok: true,
          json: async () => ({
            is_active: false,
          }),
        } as Response
      }

      return {
        ok: true,
        json: async () => ({}),
      } as Response
    })

    renderChat({
      sessionId: 'session-1',
      onSessionChange,
    })

    expect(await screen.findByText('Reset me')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /reset chat/i }))

    await waitFor(() => {
      expect(onSessionChange).toHaveBeenCalledWith('session-2')
    })

    expect(localStorage.getItem(oldAuditCacheKey)).toBeNull()
    expect(localStorage.getItem(newAuditCacheKey)).toBeNull()
    expect(screen.queryByText('Reset me')).not.toBeInTheDocument()

    confirmSpy.mockRestore()
  })
})

describe('Chat flow evidence rendering', () => {
  beforeEach(() => {
    localStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    emitGlobalToastMock.mockReset()
    mockChatFetch()
  })

  it('renders FLOW_STEP_EVIDENCE independently from assistant text events', async () => {
    const user = userEvent.setup()

    renderChat({
      sessionId: 'session-flow-evidence',
      events: [
        {
          type: 'FLOW_STEP_EVIDENCE',
          timestamp: '2026-02-26T00:00:01.000Z',
          session_id: 'session-flow-evidence',
          details: {
            flow_id: 'flow-1',
            flow_name: 'Flow Evidence',
            flow_run_id: 'run-1',
            step: 2,
            tool_name: 'ask_gene_specialist',
            agent_id: 'gene',
            agent_name: 'Gene Agent',
            evidence_records: [
              {
                entity: 'TP53',
                verified_quote: 'TP53 increased in the treated samples.',
                page: 2,
                section: 'Results',
                chunk_id: 'chunk-1',
              },
            ],
            evidence_count: 1,
            total_evidence_records: 3,
          },
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          content: 'Found TP53 support.',
        },
      ],
    })

    await waitFor(() => {
      expect(screen.getByTestId('flow-step-evidence-card')).toBeInTheDocument()
    })

    expect(screen.getByText('Step 2 / Gene Agent / ask_gene_specialist')).toBeInTheDocument()
    expect(screen.getByText('1 evidence quote captured in this step.')).toBeInTheDocument()
    expect(screen.getByText('3 evidence quotes collected so far in this run.')).toBeInTheDocument()
    expect(screen.getByText('Found TP53 support.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'TP53 1' }))

    expect(
      screen.getByRole('button', {
        name: 'Highlight evidence on PDF: TP53 increased in the treated samples.',
      }),
    ).toBeInTheDocument()
  })

  it('labels FLOW_STEP_EVIDENCE preview totals when the record list is capped', async () => {
    const user = userEvent.setup()

    renderChat({
      sessionId: 'session-flow-evidence-preview',
      events: [
        {
          type: 'FLOW_STEP_EVIDENCE',
          timestamp: '2026-02-26T00:00:01.000Z',
          session_id: 'session-flow-evidence-preview',
          details: {
            flow_id: 'flow-1',
            flow_name: 'Flow Evidence',
            flow_run_id: 'run-1',
            step: 2,
            tool_name: 'ask_gene_specialist',
            agent_id: 'gene',
            agent_name: 'Gene Agent',
            evidence_records: [
              {
                entity: 'TP53',
                verified_quote: 'TP53 increased in the treated samples.',
                page: 2,
                section: 'Results',
                chunk_id: 'chunk-1',
              },
            ],
            evidence_count: 3,
            total_evidence_records: 7,
          },
        },
      ],
    })

    await waitFor(() => {
      expect(screen.getByTestId('flow-step-evidence-card')).toBeInTheDocument()
    })

    expect(screen.getByText('Step 2 / Gene Agent / ask_gene_specialist')).toBeInTheDocument()
    expect(
      screen.getByText('Showing 1 evidence quote preview from 3 evidence quotes captured in this step.')
    ).toBeInTheDocument()
    expect(screen.getByText('7 evidence quotes collected so far in this run.')).toBeInTheDocument()
    expect(screen.getByText('1 evidence quote preview')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'TP53 1' }))

    expect(
      screen.getByRole('button', {
        name: 'Highlight evidence on PDF: TP53 increased in the treated samples.',
      }),
    ).toBeInTheDocument()
  })

  it('ignores malformed FLOW_STEP_EVIDENCE events that omit required evidence counts', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    try {
      renderChat({
        sessionId: 'session-flow-evidence',
        events: [
          {
            type: 'FLOW_STEP_EVIDENCE',
            timestamp: '2026-02-26T00:00:01.000Z',
            session_id: 'session-flow-evidence',
            details: {
              flow_id: 'flow-1',
              flow_name: 'Flow Evidence',
              flow_run_id: 'run-1',
              step: 2,
              tool_name: 'ask_gene_specialist',
              agent_id: 'gene',
              agent_name: 'Gene Agent',
              evidence_records: [],
              total_evidence_records: 3,
            },
          },
        ],
      })

      await waitFor(() => {
        expect(warnSpy).toHaveBeenCalledWith(
          '[Chat] Ignoring malformed FLOW_STEP_EVIDENCE event payload',
          expect.objectContaining({ type: 'FLOW_STEP_EVIDENCE' }),
        )
      })

      expect(screen.queryByTestId('flow-step-evidence-card')).not.toBeInTheDocument()
    } finally {
      warnSpy.mockRestore()
    }
  })

  it('ignores FLOW_STEP_EVIDENCE events without a valid timestamp', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    try {
      renderChat({
        sessionId: 'session-flow-evidence',
        events: [
          {
            type: 'FLOW_STEP_EVIDENCE',
            session_id: 'session-flow-evidence',
            details: {
              flow_id: 'flow-1',
              flow_name: 'Flow Evidence',
              flow_run_id: 'run-1',
              step: 2,
              tool_name: 'ask_gene_specialist',
              agent_id: 'gene',
              agent_name: 'Gene Agent',
              evidence_records: [],
              evidence_count: 0,
              total_evidence_records: 3,
            },
          },
        ],
      })

      await waitFor(() => {
        expect(warnSpy).toHaveBeenCalledWith(
          '[Chat] Ignoring FLOW_STEP_EVIDENCE event without a valid timestamp',
          expect.objectContaining({ type: 'FLOW_STEP_EVIDENCE' }),
        )
      })

      expect(screen.queryByTestId('flow-step-evidence-card')).not.toBeInTheDocument()
    } finally {
      warnSpy.mockRestore()
    }
  })
})

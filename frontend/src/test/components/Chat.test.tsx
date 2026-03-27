import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Chat from '../../components/Chat'

const mockNavigate = vi.fn()
const openCurationWorkspaceMock = vi.fn()

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

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { email: 'curator@example.org' },
  }),
}))

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
    extraction_result_count: number
    conversation_message_count: number
    adapter_keys: string[]
    profile_keys: string[]
    domain_keys: string[]
    blocking_reasons: string[]
  }
  prepRun?: {
    summary_text: string
    document_id: string
    candidate_count: number
    warnings: string[]
    processing_notes: string[]
    adapter_keys: string[]
    profile_keys: string[]
    domain_keys: string[]
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
          extraction_result_count: 0,
          conversation_message_count: 0,
          adapter_keys: [],
          profile_keys: [],
          domain_keys: [],
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
          profile_keys: ['primary'],
          domain_keys: ['disease'],
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

    return {
      ok: true,
      json: async () => ({}),
    } as Response
  })
}

function renderChat(props?: Partial<ComponentProps<typeof Chat>>) {
  const sendMessage = props?.sendMessage ?? vi.fn().mockResolvedValue(undefined)
  const mergedProps: ComponentProps<typeof Chat> = {
    sessionId: 'session-1',
    events: [],
    isLoading: false,
    sendMessage,
    onSessionChange: vi.fn(),
    ...props,
  }

  return {
    ...render(
      <MemoryRouter>
        <Chat {...mergedProps} />
      </MemoryRouter>
    ),
    sendMessage,
  }
}

describe('Chat persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    mockNavigate.mockReset()
    openCurationWorkspaceMock.mockReset()
    mockChatFetch()
  })

  it('persists pending chat data on unmount and restores it on remount', async () => {
    localStorage.setItem('chat-session-id', 'session-1')
    const { unmount, sendMessage } = renderChat({ sessionId: 'session-1' })

    const input = screen.getByPlaceholderText('Type your message...')
    fireEvent.change(input, { target: { value: 'Persist me across navigation' } })
    fireEvent.keyPress(input, { key: 'Enter', code: 'Enter', charCode: 13 })

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith('Persist me across navigation', 'session-1')
    })

    // Simulate navigating away from Home before debounce timer naturally fires.
    unmount()

    const storedRaw = localStorage.getItem('chat-messages')
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
    localStorage.setItem('chat-session-id', 'session-2')
    localStorage.setItem(
      'chat-messages',
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

    expect(localStorage.getItem('chat-messages')).not.toBeNull()
  })

  it('dispatches pdf overlay updates for chunk provenance events', async () => {
    const listener = vi.fn()
    window.addEventListener('pdf-overlay-update', listener as EventListener)

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
      ],
    })

    await waitFor(() => {
      expect(listener).toHaveBeenCalledTimes(1)
    })

    const event = listener.mock.calls[0][0] as CustomEvent<{
      chunkId: string
      documentId: string
      docItems: Array<{
        page_no: number
        bbox: {
          left: number
          top: number
          right: number
          bottom: number
          coord_origin: string
        }
      }>
    }>

    expect(event.detail).toEqual({
      chunkId: 'chunk-42',
      documentId: 'doc-7',
      docItems: [
        {
          page_no: 4,
          bbox: { left: 11, top: 22, right: 33, bottom: 5, coord_origin: 'BOTTOMLEFT' },
        },
      ],
    })

    window.removeEventListener('pdf-overlay-update', listener as EventListener)
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

  it('loads prep scope, confirms prep, and triggers the curation prep API', async () => {
    mockChatFetch({
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 4 candidate annotations. Prepare all for curation review?',
        candidate_count: 4,
        extraction_result_count: 2,
        conversation_message_count: 6,
        adapter_keys: ['disease'],
        profile_keys: ['primary'],
        domain_keys: ['disease'],
        blocking_reasons: [],
      },
      prepRun: {
        summary_text: 'Prepared 2 candidate annotations for curation review.',
        document_id: 'doc-disease-1',
        candidate_count: 2,
        warnings: ['Review warnings are available.'],
        processing_notes: ['Prep completed successfully.'],
        adapter_keys: ['disease'],
        profile_keys: ['primary'],
        domain_keys: ['disease'],
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
          profile_keys: ['primary'],
          domain_keys: ['disease'],
        }),
      })
    })

    expect(
      await screen.findByText(/Prepared 2 candidate annotations for curation review\./i)
    ).toBeInTheDocument()
  })

  it('opens the curation workspace after prep completes for an active document', async () => {
    openCurationWorkspaceMock
      .mockResolvedValueOnce('curation-session-1')
      .mockResolvedValueOnce('curation-session-2')
      .mockResolvedValueOnce('curation-session-2')
    mockChatFetch({
      activeDocument: {
        id: 'doc-1',
        filename: 'doc-1.pdf',
      },
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 2 candidate annotations. Prepare all for curation review?',
        candidate_count: 2,
        extraction_result_count: 1,
        conversation_message_count: 4,
        adapter_keys: ['gene'],
        profile_keys: ['primary'],
        domain_keys: ['gene'],
        blocking_reasons: [],
      },
      prepRun: {
        summary_text: 'Prepared 2 candidate annotations for curation review.',
        document_id: 'doc-1',
        candidate_count: 2,
        warnings: [],
        processing_notes: [],
        adapter_keys: ['gene'],
        profile_keys: ['primary'],
        domain_keys: ['gene'],
      },
    })

    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))
    fireEvent.click(await screen.findByRole('button', { name: /start prep/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          documentId: 'doc-1',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          profileKeys: ['primary'],
          domainKeys: ['gene'],
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
          profileKeys: ['primary'],
          domainKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })

    fireEvent.click(screen.getByRole('button', { name: /review & curate/i, hidden: true }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'curation-session-2',
          documentId: 'doc-1',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          profileKeys: ['primary'],
          domainKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })
  })

  it('opens the curation workspace after prep completes even when active document state is missing', async () => {
    openCurationWorkspaceMock.mockResolvedValueOnce('curation-session-fallback')
    mockChatFetch({
      prepPreview: {
        ready: true,
        summary_text: 'You discussed 1 candidate annotation. Prepare all for curation review?',
        candidate_count: 1,
        extraction_result_count: 1,
        conversation_message_count: 2,
        adapter_keys: ['gene'],
        profile_keys: ['primary'],
        domain_keys: ['gene'],
        blocking_reasons: [],
      },
      prepRun: {
        summary_text: 'Prepared 1 candidate annotation for curation review.',
        document_id: 'doc-from-backend',
        candidate_count: 1,
        warnings: [],
        processing_notes: [],
        adapter_keys: ['gene'],
        profile_keys: ['primary'],
        domain_keys: ['gene'],
      },
    })

    renderChat()

    fireEvent.click(screen.getByRole('button', { name: /prepare for curation/i }))
    fireEvent.click(await screen.findByRole('button', { name: /start prep/i }))

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          documentId: 'doc-from-backend',
          originSessionId: 'session-1',
          adapterKeys: ['gene'],
          profileKeys: ['primary'],
          domainKeys: ['gene'],
          navigate: mockNavigate,
        })
      )
    })
  })
})

import type { ComponentProps } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Chat from '../../components/Chat'

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { email: 'curator@example.org' },
  }),
}))

const CURATION_DB_WARNING =
  'Curation database connection lost - all database queries unavailable'

function mockChatFetch(options?: {
  curationDbStatus?: string
  rejectHealth?: boolean
}) {
  const { curationDbStatus = 'connected', rejectHealth = false } = options ?? {}

  vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input)

    if (url === '/health') {
      if (rejectHealth) {
        throw new Error('health fetch failed')
      }

      return {
        ok: true,
        json: async () => ({
          services: {
            weaviate: 'connected',
            curation_db: curationDbStatus,
          },
        }),
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
      expect(global.fetch).toHaveBeenCalledWith('/health')
    })

    expect(screen.queryByText(CURATION_DB_WARNING)).not.toBeInTheDocument()
  })

  it.each(['disconnected', 'error'])(
    'shows the curation DB outage warning when /health reports %s',
    async (curationDbStatus) => {
      mockChatFetch({ curationDbStatus })

      renderChat()

      await waitFor(() => {
        expect(screen.getByText(CURATION_DB_WARNING)).toBeInTheDocument()
      })
    }
  )
})

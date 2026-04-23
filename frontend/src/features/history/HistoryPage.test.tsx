import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { userEvent } from '@/test/test-utils'
import type {
  BulkDeleteChatSessionsRequest,
  ChatHistoryDetailResponse,
  ChatHistoryListResponse,
  ChatHistorySessionSummary,
  DeleteChatSessionRequest,
  RenameChatSessionRequest,
} from '@/services/chatHistoryApi'

import HistoryPage from './HistoryPage'

const hookMocks = vi.hoisted(() => ({
  useChatHistoryListQuery: vi.fn(),
  useChatHistoryDetailQuery: vi.fn(),
  useRenameChatSessionMutation: vi.fn(),
  useDeleteChatSessionMutation: vi.fn(),
  useBulkDeleteChatSessionsMutation: vi.fn(),
}))

vi.mock('./useChatHistoryQuery', () => ({
  useChatHistoryListQuery: hookMocks.useChatHistoryListQuery,
  useChatHistoryDetailQuery: hookMocks.useChatHistoryDetailQuery,
  useRenameChatSessionMutation: hookMocks.useRenameChatSessionMutation,
  useDeleteChatSessionMutation: hookMocks.useDeleteChatSessionMutation,
  useBulkDeleteChatSessionsMutation: hookMocks.useBulkDeleteChatSessionsMutation,
}))

const theme = createTheme()

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

function CurrentLocation() {
  const location = useLocation()

  return (
    <div data-testid="current-location">
      {location.pathname}
      {location.search}
    </div>
  )
}

function renderHistoryPage(initialEntry = '/history') {
  const queryClient = createQueryClient()

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route
              path="/history"
              element={(
                <>
                  <HistoryPage />
                  <CurrentLocation />
                </>
              )}
            />
            <Route path="/" element={<CurrentLocation />} />
            <Route path="/agent-studio" element={<CurrentLocation />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

function buildSession(overrides: Partial<ChatHistorySessionSummary> = {}): ChatHistorySessionSummary {
  return {
    session_id: 'session-1',
    chat_kind: 'assistant_chat',
    title: 'TP53 evidence review',
    active_document_id: null,
    created_at: '2026-04-20T09:00:00Z',
    updated_at: '2026-04-20T09:15:00Z',
    last_message_at: '2026-04-20T09:14:00Z',
    recent_activity_at: '2026-04-20T09:15:00Z',
    ...overrides,
  }
}

function buildListResponse(
  sessions: ChatHistorySessionSummary[],
  overrides: Partial<ChatHistoryListResponse> = {},
): ChatHistoryListResponse {
  return {
    chat_kind: 'all',
    total_sessions: sessions.length,
    limit: 100,
    query: null,
    document_id: null,
    next_cursor: null,
    sessions,
    ...overrides,
  }
}

function buildDetailResponse(
  overrides: Partial<ChatHistoryDetailResponse> = {},
): ChatHistoryDetailResponse {
  return {
    session: buildSession(),
    active_document: {
      id: 'doc-1',
      filename: 'paper.pdf',
      chunk_count: 42,
      vector_count: 84,
      metadata: null,
    },
    messages: [
      {
        message_id: 'message-user',
        session_id: 'session-1',
        chat_kind: 'assistant_chat',
        turn_id: 'turn-1',
        role: 'user',
        message_type: 'text',
        content: 'Summarize TP53 findings.',
        payload_json: null,
        trace_id: null,
        created_at: '2026-04-20T09:10:00Z',
      },
      {
        message_id: 'message-assistant',
        session_id: 'session-1',
        chat_kind: 'assistant_chat',
        turn_id: 'turn-1',
        role: 'assistant',
        message_type: 'text',
        content: 'TP53 increased in treated samples.',
        payload_json: {
          evidence_records: [
            {
              entity: 'TP53',
              verified_quote: 'TP53 increased in treated samples.',
              page: 2,
              section: 'Results',
              chunk_id: 'chunk-1',
            },
          ],
        },
        trace_id: 'trace-1',
        created_at: '2026-04-20T09:11:00Z',
      },
    ],
    message_limit: 100,
    next_message_cursor: null,
    ...overrides,
  }
}

function createMutationResult<TVariables>(mutateAsync: (variables: TVariables) => Promise<unknown>) {
  return {
    mutateAsync,
    isPending: false,
    error: null,
    reset: vi.fn(),
  }
}

describe('HistoryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    const sessions: ChatHistorySessionSummary[] = [
      buildSession(),
      buildSession({
        session_id: 'session-2',
        chat_kind: 'agent_studio',
        title: 'Agent workflow prototype',
        active_document_id: 'doc-2',
        created_at: '2026-04-19T10:00:00Z',
        updated_at: '2026-04-19T10:30:00Z',
        last_message_at: '2026-04-19T10:30:00Z',
        recent_activity_at: '2026-04-19T10:30:00Z',
      }),
    ]

    hookMocks.useChatHistoryListQuery.mockImplementation((
      request?: { chatKind?: string; query?: string | null },
    ) => {
      const requestedKind = request?.chatKind ?? 'all'
      const normalizedQuery = request?.query?.toLowerCase() ?? null
      const visibleSessions = sessions
        .filter((session) => requestedKind === 'all' || session.chat_kind === requestedKind)
        .filter((session) => {
          if (!normalizedQuery) {
            return true
          }

          return (session.title ?? '').toLowerCase().includes(normalizedQuery)
        })

      return {
        data: buildListResponse(visibleSessions, {
          chat_kind: requestedKind as ChatHistoryListResponse['chat_kind'],
          query: request?.query ?? null,
          total_sessions: visibleSessions.length,
        }),
        error: null,
        isLoading: false,
        isFetching: false,
        refetch: vi.fn(),
      }
    })

    hookMocks.useChatHistoryDetailQuery.mockImplementation((
      request: { sessionId: string },
      options?: { enabled?: boolean },
    ) => {
      const session = sessions.find((candidate) => candidate.session_id === request.sessionId)
        ?? sessions[0]

      return {
        data: options?.enabled ? buildDetailResponse({ session }) : undefined,
        error: null,
        isLoading: false,
        isFetching: false,
      }
    })

    hookMocks.useRenameChatSessionMutation.mockReturnValue(
      createMutationResult<RenameChatSessionRequest>(vi.fn().mockResolvedValue(undefined)),
    )
    hookMocks.useDeleteChatSessionMutation.mockReturnValue(
      createMutationResult<DeleteChatSessionRequest>(vi.fn().mockResolvedValue(undefined)),
    )
    hookMocks.useBulkDeleteChatSessionsMutation.mockReturnValue(
      createMutationResult<BulkDeleteChatSessionsRequest>(vi.fn().mockResolvedValue(undefined)),
    )
  })

  it('renders mixed-kind conversation cards and expands transcripts inline', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    expect(screen.getByText('TP53 evidence review')).toBeInTheDocument()
    expect(screen.getByText('Agent workflow prototype')).toBeInTheDocument()
    expect(screen.getAllByText('AI assistant chat')).not.toHaveLength(0)
    expect(screen.getAllByText('Agent Studio chat')).not.toHaveLength(0)

    await user.click(screen.getAllByRole('button', { name: 'Show transcript' })[0])

    expect(screen.getByText('Active document')).toBeInTheDocument()
    expect(screen.getByText('paper.pdf')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-message-user')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-message-assistant')).toBeInTheDocument()
    expect(screen.getByText('Summarize TP53 findings.')).toBeInTheDocument()
    expect(screen.getByText('TP53 increased in treated samples.')).toBeInTheDocument()
  })

  it('syncs the selected kind filter through the URL across all three modes', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await waitFor(() => {
      expect(hookMocks.useChatHistoryListQuery).toHaveBeenLastCalledWith(
        expect.objectContaining({
          chatKind: 'all',
          limit: 100,
          query: null,
        }),
      )
    })
    expect(screen.getByTestId('current-location')).toHaveTextContent('/history?kind=all')

    await user.click(screen.getByRole('tab', { name: 'AI assistant chat' }))

    await waitFor(() => {
      expect(hookMocks.useChatHistoryListQuery).toHaveBeenLastCalledWith(
        expect.objectContaining({
          chatKind: 'assistant_chat',
          limit: 100,
          query: null,
        }),
      )
    })
    expect(screen.getByTestId('current-location')).toHaveTextContent('/history?kind=assistant_chat')

    await user.click(screen.getByRole('tab', { name: 'Agent Studio chat' }))

    await waitFor(() => {
      expect(hookMocks.useChatHistoryListQuery).toHaveBeenLastCalledWith(
        expect.objectContaining({
          chatKind: 'agent_studio',
          limit: 100,
          query: null,
        }),
      )
    })
    expect(screen.getByTestId('current-location')).toHaveTextContent('/history?kind=agent_studio')
  })

  it('reads kind and search state from the URL and scopes search results within that kind', async () => {
    renderHistoryPage('/history?kind=agent_studio&q=workflow')

    await waitFor(() => {
      expect(hookMocks.useChatHistoryListQuery).toHaveBeenLastCalledWith(
        expect.objectContaining({
          chatKind: 'agent_studio',
          limit: 100,
          query: 'workflow',
        }),
      )
    })

    expect(screen.getByLabelText('Search chat history')).toHaveValue('workflow')
    expect(screen.getByText('Agent workflow prototype')).toBeInTheDocument()
    expect(screen.queryByText('TP53 evidence review')).not.toBeInTheDocument()
    expect(screen.getByTestId('current-location')).toHaveTextContent(
      '/history?kind=agent_studio&q=workflow',
    )
  })

  it('passes the selected kind into title searches', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await user.click(screen.getByRole('tab', { name: 'Agent Studio chat' }))
    fireEvent.change(screen.getByLabelText('Search chat history'), {
      target: { value: '  workflow  ' },
    })

    await waitFor(() => {
      expect(hookMocks.useChatHistoryListQuery).toHaveBeenLastCalledWith(
        expect.objectContaining({
          chatKind: 'agent_studio',
          limit: 100,
          query: 'workflow',
        }),
      )
    })

    expect(screen.getByText('Agent workflow prototype')).toBeInTheDocument()
    expect(screen.queryByText('TP53 evidence review')).not.toBeInTheDocument()
    expect(screen.getByTestId('current-location')).toHaveTextContent(
      '/history?kind=agent_studio&q=workflow',
    )
  })

  it('routes assistant chat restores back to the home page session param', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'Resume chat' }))

    await waitFor(() => {
      expect(screen.getByTestId('current-location')).toHaveTextContent('/?session=session-1')
    })
  })

  it('routes Agent Studio restores to the agent studio session_id param', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'Open in Agent Studio' }))

    await waitFor(() => {
      expect(screen.getByTestId('current-location')).toHaveTextContent(
        '/agent-studio?session_id=session-2',
      )
    })
  })

  it('supports renaming a conversation from the list', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue(undefined)

    hookMocks.useRenameChatSessionMutation.mockReturnValue(
      createMutationResult<RenameChatSessionRequest>(mutateAsync),
    )

    renderHistoryPage()

    await user.click(screen.getAllByRole('button', { name: 'Rename' })[0])
    fireEvent.change(screen.getByLabelText('Conversation title'), {
      target: { value: '  Renamed transcript  ' },
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mutateAsync).toHaveBeenCalledWith({
      sessionId: 'session-1',
      title: 'Renamed transcript',
    })
  }, 10000)

  it('supports deleting an individual conversation', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue(undefined)

    hookMocks.useDeleteChatSessionMutation.mockReturnValue(
      createMutationResult<DeleteChatSessionRequest>(mutateAsync),
    )

    renderHistoryPage()

    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0])
    await user.click(screen.getByRole('button', { name: 'Delete conversation' }))

    expect(mutateAsync).toHaveBeenCalledWith({
      sessionId: 'session-1',
    })
  })

  it('supports selecting all visible conversations and bulk deleting them', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue(undefined)

    hookMocks.useBulkDeleteChatSessionsMutation.mockReturnValue(
      createMutationResult<BulkDeleteChatSessionsRequest>(mutateAsync),
    )

    renderHistoryPage()

    await user.click(screen.getByLabelText('Select all visible conversations'))
    await user.click(screen.getByRole('button', { name: 'Delete selected' }))
    await user.click(screen.getByRole('button', { name: 'Delete selected conversations' }))

    expect(mutateAsync).toHaveBeenCalledWith({
      sessionIds: ['session-1', 'session-2'],
    })
  })
})

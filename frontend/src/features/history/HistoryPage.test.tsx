import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

let listQueryOverride: Record<string, unknown> | null = null

describe('HistoryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listQueryOverride = null

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
      if (listQueryOverride) {
        return listQueryOverride
      }

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

  it('renders the compact header, toolbar, and mixed-kind rows without dominating metadata', () => {
    renderHistoryPage()

    expect(screen.getByRole('heading', { level: 1, name: 'Chat History' })).toBeInTheDocument()
    expect(screen.getByText('2 conversations')).toBeInTheDocument()
    expect(screen.getByText('Showing 2 of 2')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Chat kind filter' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'All', pressed: true })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Assistant', pressed: false })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Studio', pressed: false })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Search titles' })).toBeInTheDocument()

    expect(screen.getByText('Conversation')).toBeInTheDocument()
    expect(screen.getByText('Activity')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select all conversations' })).not.toBeChecked()

    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'TP53 evidence review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Agent workflow prototype' })).toBeInTheDocument()
    expect(screen.getByText('Assistant', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('Studio', { selector: 'span' })).toBeInTheDocument()

    expect(screen.queryByText('session-1')).not.toBeInTheDocument()
    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Delete/ })).not.toBeInTheDocument()
  })

  it('expands a transcript under its row with the row button and the chevron', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'TP53 evidence review' }))

    const panel = screen.getByRole('region', { name: 'Transcript for TP53 evidence review' })
    expect(panel).toBeInTheDocument()
    expect(screen.getByText('paper.pdf · 42 chunks · 84 vectors')).toBeInTheDocument()
    expect(screen.getByText('session-1')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-message-user')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-message-assistant')).toBeInTheDocument()
    expect(screen.getByText('Summarize TP53 findings.')).toBeInTheDocument()
    expect(screen.getByText('TP53 increased in treated samples.')).toBeInTheDocument()
    expect(hookMocks.useChatHistoryDetailQuery).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'session-1' }),
      { enabled: true },
    )

    await user.click(screen.getByRole('button', { name: 'Hide transcript' }))
    expect(screen.queryByRole('region', { name: 'Transcript for TP53 evidence review' })).not.toBeInTheDocument()
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

    await user.click(screen.getByRole('button', { name: 'Assistant', pressed: false }))

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
    expect(screen.getByRole('button', { name: 'Assistant', pressed: true })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Studio', pressed: false }))

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

    expect(screen.getByRole('textbox', { name: 'Search titles' })).toHaveValue('workflow')
    expect(screen.getByRole('button', { name: 'Agent workflow prototype' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'TP53 evidence review' })).not.toBeInTheDocument()
    expect(screen.getByTestId('current-location')).toHaveTextContent(
      '/history?kind=agent_studio&q=workflow',
    )
  })

  it('passes the selected kind into title searches and clears the search from the field', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'Studio', pressed: false }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Search titles' }), {
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

    expect(screen.getByRole('button', { name: 'Agent workflow prototype' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'TP53 evidence review' })).not.toBeInTheDocument()
    expect(screen.getByTestId('current-location')).toHaveTextContent(
      '/history?kind=agent_studio&q=workflow',
    )

    await user.click(screen.getByRole('button', { name: 'Clear search text' }))

    await waitFor(() => {
      expect(screen.getByTestId('current-location')).toHaveTextContent('/history?kind=agent_studio')
    })
    expect(screen.getByRole('textbox', { name: 'Search titles' })).toHaveValue('')
  })

  it('shows the empty search state with a Clear search action', async () => {
    const user = userEvent.setup()

    renderHistoryPage('/history?kind=all&q=zebrafish')

    expect(await screen.findByText('No conversations match "zebrafish"')).toBeInTheDocument()
    expect(screen.getByText('Showing 0 of 0')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear search' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'TP53 evidence review' })).toBeInTheDocument()
    })
  })

  it('shows the empty history state when nothing is stored', () => {
    listQueryOverride = {
      data: buildListResponse([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    }

    renderHistoryPage()

    expect(screen.getByText('No stored conversations yet')).toBeInTheDocument()
    expect(screen.getByText('0 conversations')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument()
  })

  it('renders skeleton rows and a progress bar during the first load', () => {
    listQueryOverride = {
      data: undefined,
      error: null,
      isLoading: true,
      isFetching: true,
      refetch: vi.fn(),
    }

    renderHistoryPage()

    expect(screen.getByRole('status', { name: 'Loading conversations' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
    expect(screen.getByText('Loading…')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Conversations' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select all conversations' })).toBeDisabled()
  })

  it('shows an inline load error with Retry', async () => {
    const user = userEvent.setup()
    const refetch = vi.fn()
    listQueryOverride = {
      data: undefined,
      error: new Error('the server returned 502'),
      isLoading: false,
      isFetching: false,
      refetch,
    }

    renderHistoryPage()

    expect(screen.getByRole('alert')).toHaveTextContent('Could not load chat history: the server returned 502')
    expect(screen.queryByRole('list', { name: 'Conversations' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refetch).toHaveBeenCalledTimes(1)
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

  it('resumes from the expanded panel footer', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'Agent workflow prototype' }))
    const panel = screen.getByRole('region', { name: 'Transcript for Agent workflow prototype' })
    await user.click(within(panel).getByRole('button', { name: 'Open in Agent Studio' }))

    await waitFor(() => {
      expect(screen.getByTestId('current-location')).toHaveTextContent(
        '/agent-studio?session_id=session-2',
      )
    })
  })

  it('supports renaming a conversation from the overflow menu', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue(undefined)

    hookMocks.useRenameChatSessionMutation.mockReturnValue(
      createMutationResult<RenameChatSessionRequest>(mutateAsync),
    )

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'More actions for TP53 evidence review' }))
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }))
    fireEvent.change(screen.getByLabelText('Conversation title'), {
      target: { value: '  Renamed transcript  ' },
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mutateAsync).toHaveBeenCalledWith({
      sessionId: 'session-1',
      title: 'Renamed transcript',
    })
  }, 10000)

  it('copies the session ID from the overflow menu and confirms it', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'More actions for Agent workflow prototype' }))
    await user.click(screen.getByRole('menuitem', { name: 'Copy session ID' }))

    expect(await screen.findByText('Session ID copied')).toBeInTheDocument()
    expect(await navigator.clipboard.readText()).toBe('session-2')
  })

  it('supports deleting an individual conversation after confirmation', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue(undefined)

    hookMocks.useDeleteChatSessionMutation.mockReturnValue(
      createMutationResult<DeleteChatSessionRequest>(mutateAsync),
    )

    renderHistoryPage()

    await user.click(screen.getByRole('button', { name: 'More actions for TP53 evidence review' }))
    await user.click(screen.getByRole('menuitem', { name: 'Delete' }))

    expect(screen.getByRole('dialog', { name: 'Delete conversation?' })).toHaveTextContent('TP53 evidence review')
    expect(mutateAsync).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Delete conversation' }))

    expect(mutateAsync).toHaveBeenCalledWith({
      sessionId: 'session-1',
    })
  })

  it('shows the selection bar only with a selection and bulk deletes the selected rows', async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue(undefined)

    hookMocks.useBulkDeleteChatSessionsMutation.mockReturnValue(
      createMutationResult<BulkDeleteChatSessionsRequest>(mutateAsync),
    )

    renderHistoryPage()

    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Select TP53 evidence review' }))

    const selectionBar = screen.getByRole('toolbar', { name: 'Selection actions' })
    expect(selectionBar).toHaveTextContent('1 selected of 2 shown')
    expect(within(selectionBar).getByRole('checkbox', { name: 'Select all shown conversations' })).toBePartiallyChecked()

    await user.click(within(selectionBar).getByRole('button', { name: 'Select all 2' }))

    expect(selectionBar).toHaveTextContent('2 selected of 2 shown')
    expect(screen.getByRole('checkbox', { name: 'Select all conversations' })).toBeChecked()
    expect(within(selectionBar).queryByRole('button', { name: /^Select all/ })).not.toBeInTheDocument()

    await user.click(within(selectionBar).getByRole('button', { name: 'Delete 2' }))
    expect(screen.getByRole('dialog', { name: 'Delete 2 conversations?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete 2 conversations' }))

    expect(mutateAsync).toHaveBeenCalledWith({
      sessionIds: ['session-1', 'session-2'],
    })
    await waitFor(() => {
      expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument()
    })
  })

  it('selects rows from their checkbox, clears them from the selection bar, and toggles all from the column header', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    const rowCheckbox = screen.getByRole('checkbox', { name: 'Select TP53 evidence review' })
    await user.click(rowCheckbox)

    expect(rowCheckbox).toBeChecked()
    expect(screen.getByRole('toolbar', { name: 'Selection actions' })).toHaveTextContent('1 selected of 2 shown')

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select TP53 evidence review' })).not.toBeChecked()

    await user.click(screen.getByRole('checkbox', { name: 'Select all conversations' }))
    expect(screen.getByRole('toolbar', { name: 'Selection actions' })).toHaveTextContent('2 selected of 2 shown')

    await user.click(screen.getByRole('checkbox', { name: 'Select all conversations' }))
    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
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

function renderHistoryPage() {
  const queryClient = createQueryClient()

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={['/history']}>
          <HistoryPage />
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
    chat_kind: 'assistant_chat',
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

    const sessions = [
      buildSession(),
      buildSession({
        session_id: 'session-2',
        title: 'BRCA1 follow-up',
        active_document_id: 'doc-2',
        created_at: '2026-04-19T10:00:00Z',
        updated_at: '2026-04-19T10:30:00Z',
        last_message_at: '2026-04-19T10:30:00Z',
        recent_activity_at: '2026-04-19T10:30:00Z',
      }),
    ]

    hookMocks.useChatHistoryListQuery.mockImplementation((
      request?: { chatKind?: string; query?: string | null },
    ) => ({
      data: buildListResponse(
        request?.query === 'TP53'
          ? [sessions[0]]
          : sessions,
        { query: request?.query ?? null },
      ),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    }))

    hookMocks.useChatHistoryDetailQuery.mockImplementation((
      request: { sessionId: string },
      options?: { enabled?: boolean },
    ) => ({
      data: options?.enabled ? buildDetailResponse({ session: buildSession({ session_id: request.sessionId }) }) : undefined,
      error: null,
      isLoading: false,
      isFetching: false,
    }))

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

  it('renders stored conversation cards and expands transcripts inline', async () => {
    const user = userEvent.setup()

    renderHistoryPage()

    expect(screen.getByText('TP53 evidence review')).toBeInTheDocument()
    expect(screen.getByText('BRCA1 follow-up')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Show transcript' })[0])

    expect(screen.getByText('Active document')).toBeInTheDocument()
    expect(screen.getByText('paper.pdf')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-message-user')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-message-assistant')).toBeInTheDocument()
    expect(screen.getByText('Summarize TP53 findings.')).toBeInTheDocument()
    expect(screen.getByText('TP53 increased in treated samples.')).toBeInTheDocument()
  })

  it('passes trimmed search text into the history list query', async () => {
    renderHistoryPage()

    fireEvent.change(screen.getByLabelText('Search chat history'), {
      target: { value: '  TP53  ' },
    })

    await waitFor(() => {
      expect(hookMocks.useChatHistoryListQuery).toHaveBeenLastCalledWith(
        expect.objectContaining({
          chatKind: 'assistant_chat',
          limit: 100,
          query: 'TP53',
        }),
      )
    })

    expect(screen.getByText('TP53 evidence review')).toBeInTheDocument()
    expect(screen.queryByText('BRCA1 follow-up')).not.toBeInTheDocument()
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

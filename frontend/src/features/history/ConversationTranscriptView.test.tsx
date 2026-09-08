import { describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent } from '@/test/test-utils'
import type { ChatHistoryDetailResponse, ChatHistorySessionSummary } from '@/services/chatHistoryApi'

import ConversationTranscriptView from './ConversationTranscriptView'

const hookMocks = vi.hoisted(() => ({
  useChatHistoryDetailQuery: vi.fn(),
}))

vi.mock('./useChatHistoryQuery', () => ({
  useChatHistoryDetailQuery: hookMocks.useChatHistoryDetailQuery,
}))

const SESSION: ChatHistorySessionSummary = {
  session_id: 'session-1',
  chat_kind: 'assistant_chat',
  title: 'Stored conversation',
  active_document_id: null,
  created_at: '2026-04-20T09:00:00Z',
  updated_at: '2026-04-20T09:15:00Z',
  last_message_at: '2026-04-20T09:14:00Z',
  recent_activity_at: '2026-04-20T09:15:00Z',
}

function renderTranscriptView(overrides: Partial<ChatHistorySessionSummary> = {}, handlers: {
  onCopySessionId?: () => void
  onRestore?: () => void
} = {}) {
  return render(
    <ConversationTranscriptView
      expanded
      onCopySessionId={handlers.onCopySessionId ?? vi.fn()}
      onRestore={handlers.onRestore ?? vi.fn()}
      session={{ ...SESSION, ...overrides }}
    />,
  )
}

function buildDetailResponse(
  overrides: Partial<ChatHistoryDetailResponse> = {},
): ChatHistoryDetailResponse {
  return {
    session: SESSION,
    active_document: null,
    messages: [],
    message_limit: 100,
    next_message_cursor: null,
    ...overrides,
  }
}

describe('ConversationTranscriptView', () => {
  it('uses only canonical evidence_records for stored flow transcript previews', () => {
    hookMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: buildDetailResponse({
        messages: [
          {
            message_id: 'flow-message-1',
            session_id: 'session-1',
            chat_kind: 'assistant_chat',
            turn_id: 'turn-1',
            role: 'flow',
            message_type: 'flow_step_evidence',
            content: '',
            payload_json: {
              flow_id: 'flow-1',
              flow_name: 'Evidence flow',
              flow_run_id: 'run-1',
              step: 2,
              agent_name: 'Gene Agent',
              tool_name: 'ask_gene_specialist',
              evidence_count: 3,
              total_evidence_records: 7,
              evidence_preview: [
                {
                  entity: 'TP53',
                  verified_quote: 'Fallback quote preview that should be ignored.',
                  page: 2,
                  section: 'Results',
                  chunk_id: 'chunk-1',
                },
              ],
            },
            trace_id: null,
            created_at: '2026-04-20T09:11:00Z',
          },
        ],
      }),
      error: null,
      isLoading: false,
      isFetching: false,
    })

    renderTranscriptView()

    expect(screen.getByTestId('transcript-flow-step-evidence-card')).toBeInTheDocument()
    expect(screen.getByText('3 evidence quotes captured in this step.')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-flow-step-evidence-empty')).toBeInTheDocument()
    expect(
      screen.getByText('No quote previews were attached to this step.'),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('Fallback quote preview that should be ignored.'),
    ).not.toBeInTheDocument()
  })

  it('throws when the stored transcript includes an unknown message role', () => {
    hookMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: buildDetailResponse({
        messages: [
          {
            message_id: 'message-system-1',
            session_id: 'session-1',
            chat_kind: 'assistant_chat',
            turn_id: 'turn-1',
            role: 'system',
            message_type: 'text',
            content: 'Unexpected role payload',
            payload_json: null,
            trace_id: null,
            created_at: '2026-04-20T09:11:00Z',
          },
        ],
      }),
      error: null,
      isLoading: false,
      isFetching: false,
    })

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    try {
      expect(() => renderTranscriptView()).toThrow(
        'Unknown transcript message role: system',
      )
    } finally {
      consoleErrorSpy.mockRestore()
    }
  })

  it('shows the meta line, document chip, capped transcript, and footer Resume', async () => {
    const user = userEvent.setup()
    const onRestore = vi.fn()
    const onCopySessionId = vi.fn()

    hookMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: buildDetailResponse({
        active_document: {
          id: 'doc-1',
          filename: 'chen_2024_supplementary.pdf',
          chunk_count: 412,
          vector_count: 412,
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
            content: 'Extract the allele phenotypes.',
            payload_json: null,
            trace_id: null,
            created_at: '2026-04-20T09:10:00Z',
          },
        ],
        next_message_cursor: 'cursor-1',
      }),
      error: null,
      isLoading: false,
      isFetching: false,
    })

    renderTranscriptView({ session_id: 'a1f3c9e0-7b2d-4e11-9f5a-0c2b8d3e9c1f' }, { onCopySessionId, onRestore })

    const region = screen.getByRole('region', { name: 'Transcript for Stored conversation' })
    expect(region).toBeInTheDocument()
    expect(screen.getByText('Created')).toHaveTextContent(new Date('2026-04-20T09:00:00Z').toLocaleString())
    expect(screen.getByText('Last message')).toHaveTextContent(new Date('2026-04-20T09:14:00Z').toLocaleString())
    expect(screen.getByText('a1f3c9e0-7b2d-4e11-9f5a-0c2b8d3e9c1f')).toBeInTheDocument()
    expect(screen.getByText('chen_2024_supplementary.pdf · 412 chunks · 412 vectors')).toBeInTheDocument()
    expect(screen.getByTestId('transcript-message-user')).toBeInTheDocument()
    expect(screen.getByText(/Showing the newest 1 message\./)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Copy session ID' }))
    expect(onCopySessionId).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Resume chat' }))
    expect(onRestore).toHaveBeenCalledTimes(1)
  })

  it('labels the footer action for Agent Studio sessions and reports missing dates', () => {
    hookMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: buildDetailResponse(),
      error: null,
      isLoading: false,
      isFetching: false,
    })

    renderTranscriptView({ chat_kind: 'agent_studio', last_message_at: null })

    expect(screen.getByRole('button', { name: 'Open in Agent Studio' })).toBeInTheDocument()
    expect(screen.getByText('Last message')).toHaveTextContent('Unavailable')
    expect(screen.getByText('This conversation does not have any stored transcript messages yet.')).toBeInTheDocument()
    expect(screen.queryByText(/Showing the newest/)).not.toBeInTheDocument()
  })

  it('shows the loading state and the detail error inside the panel', () => {
    hookMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isFetching: true,
    })

    const { unmount } = renderTranscriptView()
    expect(screen.getByText('Loading transcript…')).toBeInTheDocument()
    unmount()

    hookMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: undefined,
      error: new Error('Transcript unavailable'),
      isLoading: false,
      isFetching: false,
    })

    renderTranscriptView()
    expect(screen.getByRole('alert')).toHaveTextContent('Transcript unavailable')
  })

  it('renders nothing and keeps the detail query disabled while collapsed', () => {
    hookMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: false,
      isFetching: false,
    })

    const { container } = render(
      <ConversationTranscriptView expanded={false} onCopySessionId={vi.fn()} onRestore={vi.fn()} session={SESSION} />,
    )

    expect(container).toBeEmptyDOMElement()
    expect(hookMocks.useChatHistoryDetailQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ sessionId: 'session-1' }),
      { enabled: false },
    )
  })
})

import { describe, expect, it, vi } from 'vitest'

import { render, screen } from '@/test/test-utils'
import type { ChatHistoryDetailResponse } from '@/services/chatHistoryApi'

import ConversationTranscriptView from './ConversationTranscriptView'

const hookMocks = vi.hoisted(() => ({
  useChatHistoryDetailQuery: vi.fn(),
}))

vi.mock('./useChatHistoryQuery', () => ({
  useChatHistoryDetailQuery: hookMocks.useChatHistoryDetailQuery,
}))

function buildDetailResponse(
  overrides: Partial<ChatHistoryDetailResponse> = {},
): ChatHistoryDetailResponse {
  return {
    session: {
      session_id: 'session-1',
      chat_kind: 'assistant_chat',
      title: 'Stored conversation',
      active_document_id: null,
      created_at: '2026-04-20T09:00:00Z',
      updated_at: '2026-04-20T09:15:00Z',
      last_message_at: '2026-04-20T09:14:00Z',
      recent_activity_at: '2026-04-20T09:15:00Z',
    },
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

    render(<ConversationTranscriptView expanded sessionId="session-1" />)

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
      expect(() => render(<ConversationTranscriptView expanded sessionId="session-1" />)).toThrow(
        'Unknown transcript message role: system',
      )
    } finally {
      consoleErrorSpy.mockRestore()
    }
  })
})

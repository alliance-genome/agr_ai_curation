import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStudioPage from './AgentStudioPage'

const serviceMocks = vi.hoisted(() => ({
  fetchPromptCatalog: vi.fn(),
  cloneAgentToWorkshop: vi.fn(),
}))

const historyMocks = vi.hoisted(() => ({
  useChatHistoryDetailQuery: vi.fn(),
  useChatHistoryTranscriptQuery: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)
vi.mock('@/features/history/useChatHistoryQuery', () => historyMocks)

vi.mock('@/components/AgentStudio/OpusChat', () => ({
  default: ({
    context,
    initialConversation,
    durableSessionId,
    sourceSessionId,
    onApplyWorkshopPromptUpdate,
    onDurableSessionIdChange,
    onConversationSnapshotChange,
  }: {
    context?: Record<string, unknown>
    initialConversation?: Array<{ content: string }>
    durableSessionId?: string | null
    sourceSessionId?: string
    onApplyWorkshopPromptUpdate?: (
      proposal: {
        prompt: string
        summary?: string
        apply_mode?: 'replace' | 'targeted_edit'
      }
    ) => void
    onDurableSessionIdChange?: (sessionId: string) => void
    onConversationSnapshotChange?: (
      messages: Array<{ role: 'user' | 'assistant'; content: string }>
    ) => void
  }) => (
    <div data-testid="opus-chat">
      Opus
      <div data-testid="opus-chat-context">{JSON.stringify(context ?? {})}</div>
      <div data-testid="opus-chat-initial-conversation">
        {(initialConversation ?? []).map((message) => message.content).join('|') || 'none'}
      </div>
      <div data-testid="opus-chat-durable-session">{durableSessionId ?? 'none'}</div>
      <div data-testid="opus-chat-source-session">{sourceSessionId ?? 'none'}</div>
      <button
        onClick={() =>
          onApplyWorkshopPromptUpdate?.({
            prompt: 'Prompt from Opus',
            summary: 'Updated from chat',
            apply_mode: 'replace',
          })
        }
      >
        apply-workshop-update
      </button>
      <button onClick={() => onDurableSessionIdChange?.('agent-studio-session-999')}>
        mint-durable-session
      </button>
      <button
        onClick={() =>
          onConversationSnapshotChange?.([
            { role: 'user', content: 'Seeded question' },
            { role: 'assistant', content: 'Seeded answer' },
            { role: 'user', content: 'Fresh Opus follow-up' },
            { role: 'assistant', content: 'Fresh Opus reply' },
          ])
        }
      >
        simulate-live-conversation
      </button>
    </div>
  ),
}))

vi.mock('@/components/AgentStudio/FlowBuilder', () => ({
  FlowBuilder: () => <div data-testid="flow-builder">Flow</div>,
}))

vi.mock('@/components/AgentStudio/AgentBrowser', () => ({
  default: ({ onCloneToWorkshop }: { onCloneToWorkshop: (agentId: string) => void }) => (
    <button onClick={() => onCloneToWorkshop('ca_source')}>clone-custom</button>
  ),
}))

vi.mock('@/components/AgentStudio/PromptWorkshop/PromptWorkshop', () => ({
  default: ({
    initialCustomAgentId,
    initialParentAgentId,
    incomingPromptUpdate,
    opusConversation,
  }: {
    initialCustomAgentId?: string | null
    initialParentAgentId?: string | null
    incomingPromptUpdate?: { prompt?: string } | null
    opusConversation?: Array<{ content: string }>
  }) => (
    <div data-testid="prompt-workshop">
      custom:{initialCustomAgentId || 'none'} parent:{initialParentAgentId || 'none'} incoming:{incomingPromptUpdate?.prompt || 'none'} conversation:{(opusConversation ?? []).map((message) => message.content).join('|') || 'none'}
    </div>
  ),
}))

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search || 'none'}</div>
}

const EMPTY_CATALOG = {
  categories: [],
  total_agents: 0,
  available_groups: [],
  last_updated: '2026-02-23T00:00:00Z',
}

function buildSessionDetail(sessionId: string, chatKind: 'assistant_chat' | 'agent_studio') {
  return {
    data: {
      session: {
        session_id: sessionId,
        chat_kind: chatKind,
        title: chatKind === 'assistant_chat' ? 'Durable assistant chat' : 'Durable Agent Studio chat',
        created_at: '2026-04-22T00:00:00Z',
        updated_at: '2026-04-22T00:00:00Z',
        recent_activity_at: '2026-04-22T00:00:00Z',
      },
      active_document: null,
      messages: [],
      message_limit: 1,
      next_message_cursor: null,
    },
    isLoading: false,
    isSuccess: true,
    error: null,
  }
}

function buildTranscript(
  sessionId: string,
  chatKind: 'assistant_chat' | 'agent_studio',
  messages: Array<{ message_id: string; role: 'user' | 'assistant' | 'flow'; message_type: string; content: string; trace_id?: string | null; payload_json?: unknown; created_at: string; turn_id?: string }>
) {
  return {
    data: {
      session: {
        session_id: sessionId,
        chat_kind: chatKind,
        title: chatKind === 'assistant_chat' ? 'Durable assistant chat' : 'Durable Agent Studio chat',
        created_at: '2026-04-22T00:00:00Z',
        updated_at: '2026-04-22T00:00:00Z',
        recent_activity_at: '2026-04-22T00:00:00Z',
      },
      active_document: null,
      message_limit: 200,
      next_message_cursor: null,
      messages: messages.map((message) => ({
        session_id: sessionId,
        chat_kind: chatKind,
        trace_id: null,
        payload_json: null,
        turn_id: 'turn-1',
        ...message,
      })),
    },
    isLoading: false,
    isSuccess: true,
    error: null,
  }
}

describe('AgentStudioPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    serviceMocks.fetchPromptCatalog.mockResolvedValue(EMPTY_CATALOG)
    serviceMocks.cloneAgentToWorkshop.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      template_source: 'gene',
    })
    historyMocks.useChatHistoryDetailQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isSuccess: false,
      error: null,
    })
    historyMocks.useChatHistoryTranscriptQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isSuccess: false,
      error: null,
    })
  })

  it('passes cloned custom agent id into PromptWorkshop after clone-to-workshop', async () => {
    render(
      <MemoryRouter>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByText('clone-custom'))

    await waitFor(() => {
      expect(serviceMocks.cloneAgentToWorkshop).toHaveBeenCalledWith('ca_source')
    })

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent(
        'custom:11111111-1111-1111-1111-111111111111 parent:gene incoming:none'
      )
    })
  })

  it('routes approved Opus workshop prompt updates to PromptWorkshop', async () => {
    render(
      <MemoryRouter>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByText('clone-custom'))

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent('incoming:none')
    })

    fireEvent.click(screen.getByText('apply-workshop-update'))

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent('incoming:Prompt from Opus')
    })
  })

  it('treats assistant-chat session_id values as seed transcript context', async () => {
    historyMocks.useChatHistoryDetailQuery.mockReturnValue(
      buildSessionDetail('assistant-session-12345678', 'assistant_chat')
    )
    historyMocks.useChatHistoryTranscriptQuery.mockReturnValue(
      buildTranscript('assistant-session-12345678', 'assistant_chat', [
        {
          message_id: 'message-1',
          role: 'user',
          message_type: 'text',
          content: 'Why did the assistant pick gene X?',
          created_at: '2026-04-22T00:00:01Z',
        },
        {
          message_id: 'message-flow',
          role: 'flow',
          message_type: 'flow_step_evidence',
          content: 'Flow evidence summary that should not seed Opus.',
          payload_json: {
            flow_id: 'flow-123',
            flow_run_id: 'run-123',
            step: 1,
            evidence_count: 1,
            total_evidence_records: 1,
            evidence_records: [
              {
                entity: 'GENE:X',
                verified_quote: 'Quoted evidence.',
                page: 1,
                section: 'Results',
                chunk_id: 'chunk-1',
              },
            ],
          },
          created_at: '2026-04-22T00:00:01.500Z',
        },
        {
          message_id: 'message-2',
          role: 'assistant',
          message_type: 'text',
          content: 'It prioritized the evidence ranking from the prior turn.',
          trace_id: 'trace-789',
          created_at: '2026-04-22T00:00:02Z',
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={['/agent-studio?session_id=assistant-session-12345678&trace_id=trace-789']}>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    expect(historyMocks.useChatHistoryDetailQuery).toHaveBeenCalledWith(
      {
        sessionId: 'assistant-session-12345678',
        chatKind: 'all',
        messageLimit: 1,
      },
      { enabled: true },
    )
    expect(historyMocks.useChatHistoryTranscriptQuery).toHaveBeenCalledWith(
      {
        sessionId: 'assistant-session-12345678',
        chatKind: 'assistant_chat',
      },
      { enabled: true },
    )
    expect(screen.getByTestId('opus-chat-context')).not.toHaveTextContent('"session_id"')
    expect(screen.getByTestId('opus-chat-context')).toHaveTextContent('"trace_id":"trace-789"')
    expect(screen.getByTestId('opus-chat-initial-conversation')).toHaveTextContent(
      'Why did the assistant pick gene X?|It prioritized the evidence ranking from the prior turn.'
    )
    expect(screen.getByTestId('opus-chat-initial-conversation')).not.toHaveTextContent(
      'Flow evidence summary that should not seed Opus.'
    )
    expect(screen.getByTestId('opus-chat-durable-session')).toHaveTextContent('none')
    expect(screen.getByTestId('opus-chat-source-session')).toHaveTextContent('assistant-session-12345678')

    fireEvent.click(screen.getByText('clone-custom'))

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent(
        'conversation:Why did the assistant pick gene X?|It prioritized the evidence ranking from the prior turn.'
      )
    })
    expect(screen.getByTestId('prompt-workshop')).not.toHaveTextContent(
      'Flow evidence summary that should not seed Opus.'
    )
  })

  it('treats agent-studio session_id values as in-place resume ids', async () => {
    historyMocks.useChatHistoryDetailQuery.mockReturnValue(
      buildSessionDetail('agent-studio-session-12345678', 'agent_studio')
    )
    historyMocks.useChatHistoryTranscriptQuery.mockReturnValue(
      buildTranscript('agent-studio-session-12345678', 'agent_studio', [
        {
          message_id: 'message-1',
          role: 'user',
          message_type: 'text',
          content: 'Please continue refining this workshop prompt.',
          created_at: '2026-04-22T00:00:01Z',
        },
        {
          message_id: 'message-2',
          role: 'assistant',
          message_type: 'text',
          content: 'Let’s tighten the output schema instructions first.',
          created_at: '2026-04-22T00:00:02Z',
        },
      ])
    )

    render(
      <MemoryRouter initialEntries={['/agent-studio?session_id=agent-studio-session-12345678&trace_id=trace-789']}>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    expect(historyMocks.useChatHistoryTranscriptQuery).toHaveBeenCalledWith(
      {
        sessionId: 'agent-studio-session-12345678',
        chatKind: 'agent_studio',
      },
      { enabled: true },
    )
    expect(screen.getByTestId('opus-chat-context')).toHaveTextContent(
      '"session_id":"agent-studio-session-12345678"'
    )
    expect(screen.getByTestId('opus-chat-durable-session')).toHaveTextContent(
      'agent-studio-session-12345678'
    )
    expect(screen.getByTestId('opus-chat-source-session')).toHaveTextContent(
      'agent-studio-session-12345678'
    )
  })

  it('adds a new session_id to the URL when Opus mints the first durable session from a clean load', async () => {
    render(
      <MemoryRouter initialEntries={['/agent-studio?trace_id=trace-789']}>
        <LocationProbe />
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    expect(screen.getByTestId('location-search')).toHaveTextContent('?trace_id=trace-789')

    fireEvent.click(screen.getByText('mint-durable-session'))

    await waitFor(() => {
      expect(screen.getByTestId('location-search')).toHaveTextContent(
        '?trace_id=trace-789&session_id=agent-studio-session-999'
      )
    })
  })

  it('replaces a seed session_id with the minted Agent Studio session id without losing the live transcript snapshot', async () => {
    historyMocks.useChatHistoryDetailQuery.mockImplementation(({ sessionId }) => {
      if (sessionId === 'assistant-seed-session') {
        return buildSessionDetail('assistant-seed-session', 'assistant_chat')
      }

      if (sessionId === 'agent-studio-session-999') {
        return buildSessionDetail('agent-studio-session-999', 'agent_studio')
      }

      return {
        data: undefined,
        isLoading: false,
        isSuccess: false,
        error: null,
      }
    })

    historyMocks.useChatHistoryTranscriptQuery.mockImplementation(({ sessionId, chatKind }) => {
      if (sessionId === 'assistant-seed-session' && chatKind === 'assistant_chat') {
        return buildTranscript('assistant-seed-session', 'assistant_chat', [
          {
            message_id: 'seed-user',
            role: 'user',
            message_type: 'text',
            content: 'Seeded question',
            created_at: '2026-04-22T00:00:01Z',
          },
          {
            message_id: 'seed-assistant',
            role: 'assistant',
            message_type: 'text',
            content: 'Seeded answer',
            created_at: '2026-04-22T00:00:02Z',
          },
        ])
      }

      if (sessionId === 'agent-studio-session-999' && chatKind === 'agent_studio') {
        return buildTranscript('agent-studio-session-999', 'agent_studio', [
          {
            message_id: 'agent-user',
            role: 'user',
            message_type: 'text',
            content: 'Persisted follow-up only',
            created_at: '2026-04-22T00:00:03Z',
          },
          {
            message_id: 'agent-assistant',
            role: 'assistant',
            message_type: 'text',
            content: 'Persisted Agent Studio reply only',
            created_at: '2026-04-22T00:00:04Z',
          },
        ])
      }

      return {
        data: undefined,
        isLoading: false,
        isSuccess: false,
        error: null,
      }
    })

    render(
      <MemoryRouter initialEntries={['/agent-studio?session_id=assistant-seed-session&trace_id=trace-789']}>
        <LocationProbe />
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByText('simulate-live-conversation'))
    fireEvent.click(screen.getByText('mint-durable-session'))

    await waitFor(() => {
      expect(screen.getByTestId('location-search')).toHaveTextContent(
        '?session_id=agent-studio-session-999&trace_id=trace-789'
      )
    })

    fireEvent.click(screen.getByText('clone-custom'))

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent(
        'conversation:Seeded question|Seeded answer|Fresh Opus follow-up|Fresh Opus reply'
      )
    })
    expect(screen.getByTestId('prompt-workshop')).not.toHaveTextContent(
      'Persisted follow-up only|Persisted Agent Studio reply only'
    )
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStudioPage from './AgentStudioPage'

const serviceMocks = vi.hoisted(() => ({
  fetchPromptCatalog: vi.fn(),
  cloneAgentToWorkshop: vi.fn(),
}))

const historyMocks = vi.hoisted(() => ({
  useChatHistoryTranscriptQuery: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)
vi.mock('@/features/history/useChatHistoryQuery', () => historyMocks)

vi.mock('@/components/AgentStudio/OpusChat', () => ({
  default: ({
    context,
    initialConversation,
    seededDurableSessionId,
    onApplyWorkshopPromptUpdate,
  }: {
    context?: Record<string, unknown>
    initialConversation?: Array<{ content: string }>
    seededDurableSessionId?: string
    onApplyWorkshopPromptUpdate?: (proposal: { prompt: string; summary?: string; apply_mode?: 'replace' | 'targeted_edit' }) => void
  }) => (
    <div data-testid="opus-chat">
      Opus
      <div data-testid="opus-chat-context">{JSON.stringify(context ?? {})}</div>
      <div data-testid="opus-chat-initial-conversation">
        {(initialConversation ?? []).map((message) => message.content).join('|') || 'none'}
      </div>
      <div data-testid="opus-chat-seeded-session">{seededDurableSessionId ?? 'none'}</div>
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

describe('AgentStudioPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    historyMocks.useChatHistoryTranscriptQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isSuccess: false,
      error: null,
    })
  })

  it('passes cloned custom agent id into PromptWorkshop after clone-to-workshop', async () => {
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [],
      total_agents: 0,
      available_groups: [],
      last_updated: '2026-02-23T00:00:00Z',
    })
    serviceMocks.cloneAgentToWorkshop.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      template_source: 'gene',
    })

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
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [],
      total_agents: 0,
      available_groups: [],
      last_updated: '2026-02-23T00:00:00Z',
    })
    serviceMocks.cloneAgentToWorkshop.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      template_source: 'gene',
    })

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

  it('hydrates Opus and workshop transcript state from durable chat session context', async () => {
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [],
      total_agents: 0,
      available_groups: [],
      last_updated: '2026-02-23T00:00:00Z',
    })
    serviceMocks.cloneAgentToWorkshop.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      template_source: 'gene',
    })
    historyMocks.useChatHistoryTranscriptQuery.mockReturnValue({
      data: {
        session: {
          session_id: 'assistant-session-12345678',
          title: 'Durable assistant chat',
          created_at: '2026-04-22T00:00:00Z',
          updated_at: '2026-04-22T00:00:00Z',
          recent_activity_at: '2026-04-22T00:00:00Z',
        },
        active_document: null,
        message_limit: 200,
        next_message_cursor: null,
        messages: [
          {
            message_id: 'message-1',
            session_id: 'assistant-session-12345678',
            turn_id: 'turn-1',
            role: 'user',
            message_type: 'text',
            content: 'Why did the assistant pick gene X?',
            payload_json: null,
            trace_id: null,
            created_at: '2026-04-22T00:00:01Z',
          },
          {
            message_id: 'message-2',
            session_id: 'assistant-session-12345678',
            turn_id: 'turn-1',
            role: 'assistant',
            message_type: 'text',
            content: 'It prioritized the evidence ranking from the prior turn.',
            payload_json: null,
            trace_id: 'trace-789',
            created_at: '2026-04-22T00:00:02Z',
          },
        ],
      },
      isLoading: false,
      isSuccess: true,
      error: null,
    })

    render(
      <MemoryRouter initialEntries={['/agent-studio?session_id=assistant-session-12345678&trace_id=trace-789']}>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    expect(historyMocks.useChatHistoryTranscriptQuery).toHaveBeenCalledWith(
      { sessionId: 'assistant-session-12345678' },
      { enabled: true },
    )
    expect(screen.getByTestId('opus-chat-context')).toHaveTextContent('"session_id":"assistant-session-12345678"')
    expect(screen.getByTestId('opus-chat-context')).toHaveTextContent('"trace_id":"trace-789"')
    expect(screen.getByTestId('opus-chat-initial-conversation')).toHaveTextContent(
      'Why did the assistant pick gene X?|It prioritized the evidence ranking from the prior turn.'
    )
    expect(screen.getByTestId('opus-chat-seeded-session')).toHaveTextContent('assistant-session-12345678')

    fireEvent.click(screen.getByText('clone-custom'))

    await waitFor(() => {
      expect(screen.getByTestId('prompt-workshop')).toHaveTextContent(
        'conversation:Why did the assistant pick gene X?|It prioritized the evidence ranking from the prior turn.'
      )
    })
  })
})

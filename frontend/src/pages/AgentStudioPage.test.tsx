import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Ref } from 'react'

import AgentStudioPage from './AgentStudioPage'

const serviceMocks = vi.hoisted(() => ({
  fetchPromptCatalog: vi.fn(),
  cloneAgentToWorkshop: vi.fn(),
}))

const historyMocks = vi.hoisted(() => ({
  useChatHistoryDetailQuery: vi.fn(),
  useChatHistoryTranscriptQuery: vi.fn(),
}))

const workshopMockState = vi.hoisted(() => ({ dirty: false }))

vi.mock('@/services/agentStudioService', () => serviceMocks)
vi.mock('@/features/history/useChatHistoryQuery', () => historyMocks)

vi.mock('@/components/AgentStudio/OpusChat', async () => {
  const React = await import('react')

  type SnapshotMessage = { role: 'user' | 'assistant'; content: string }

  function OpusChatMock({
    context,
    initialConversation,
    durableSessionId,
    sourceSessionId,
    onApplyWorkshopProposal,
    onDurableSessionIdChange,
    onConversationSnapshotChange,
    verifyMessage,
    discussMessage,
    variant,
    panelId,
    onHide,
    inputRef,
    onStreamingChange,
  }: {
    context?: Record<string, unknown>
    initialConversation?: SnapshotMessage[]
    durableSessionId?: string | null
    sourceSessionId?: string
    onApplyWorkshopProposal?: (proposal: import('@/types/promptExplorer').WorkshopAuthoringProposal) => Promise<unknown>
    onDurableSessionIdChange?: (sessionId: string) => void
    onConversationSnapshotChange?: (
      messages: Array<{ role: 'user' | 'assistant'; content: string }>
    ) => void
    verifyMessage?: string | null
    discussMessage?: string | null
    variant?: 'panel' | 'drawer'
    panelId?: string
    onHide?: () => void
    inputRef?: Ref<HTMLTextAreaElement>
    onStreamingChange?: (isStreaming: boolean) => void
  }) {
    // Mirror the real component: the current transcript is published on mount
    // and whenever the seeded conversation changes.
    const [snapshot, setSnapshot] = React.useState<SnapshotMessage[]>(initialConversation ?? [])
    // Key on content, not identity: some tests build fresh transcript objects per render.
    const seedKey = (initialConversation ?? []).map((message) => `${message.role}:${message.content}`).join('|')
    const appliedSeedKeyRef = React.useRef(seedKey)
    // The real component keeps the live transcript after it mints a session.
    const preserveLiveConversationRef = React.useRef(false)
    React.useEffect(() => {
      if (appliedSeedKeyRef.current === seedKey) {
        return
      }
      appliedSeedKeyRef.current = seedKey
      if (preserveLiveConversationRef.current) {
        return
      }
      setSnapshot(initialConversation ?? [])
    }, [seedKey, initialConversation])
    React.useEffect(() => {
      onConversationSnapshotChange?.(snapshot)
    }, [snapshot, onConversationSnapshotChange])

    return (
    <div data-testid="opus-chat">
      Opus
      <div data-testid="opus-chat-variant">{variant ?? 'none'}</div>
      <textarea aria-label="Ask about prompts" ref={inputRef} />
      <button aria-label={variant === 'drawer' ? 'Close AI Chat' : 'Hide AI Chat'} aria-expanded="true" aria-controls={panelId} onClick={onHide}>
        hide
      </button>
      <button onClick={() => onStreamingChange?.(true)}>start-streaming</button>
      <button onClick={() => onStreamingChange?.(false)}>stop-streaming</button>
      <div data-testid="opus-chat-context">{JSON.stringify(context ?? {})}</div>
      <div data-testid="opus-chat-initial-conversation">
        {(initialConversation ?? []).map((message) => message.content).join('|') || 'none'}
      </div>
      <div data-testid="opus-chat-durable-session">{durableSessionId ?? 'none'}</div>
      <div data-testid="opus-chat-source-session">{sourceSessionId ?? 'none'}</div>
      <div data-testid="opus-chat-verify-message">{verifyMessage ?? 'none'}</div>
      <div data-testid="opus-chat-discuss-message">{discussMessage ?? 'none'}</div>
      <button
        onClick={() =>
          setSnapshot((current) => [...current, { role: 'assistant', content: 'Late reply' }])
        }
      >
        append-assistant-reply
      </button>
      <button
        onClick={() =>
          onApplyWorkshopProposal?.({
            contract_version: 'workshop_authoring_proposal.v1',
            candidate: { prompt_draft: 'Prompt from Opus' },
            base_draft_fingerprint: 'base',
            candidate_draft_fingerprint: 'candidate',
            change_summary: 'Updated from chat',
            diff: [], findings: [],
          })
        }
      >
        apply-workshop-update
      </button>
      <button
        onClick={() => {
          preserveLiveConversationRef.current = true
          onDurableSessionIdChange?.('agent-studio-session-999')
        }}
      >
        mint-durable-session
      </button>
      <button
        onClick={() =>
          setSnapshot([
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
    )
  }

  return { default: OpusChatMock }
})

const flowBuilderInstances = vi.hoisted(() => ({ count: 0 }))

vi.mock('@/components/AgentStudio/FlowBuilder', async () => {
  const react = await import('react')
  return {
  FlowBuilder: ({
    onFlowChange,
    onVerifyRequest,
    active,
  }: {
    onFlowChange?: (flow: Record<string, unknown>) => void
    onVerifyRequest?: () => void
    active?: boolean
  }) => {
    // One id per mounted instance, so a test can prove the builder was not remounted.
    const [instance] = react.useState(() => {
      flowBuilderInstances.count += 1
      return flowBuilderInstances.count
    })
    return (
    <div data-testid="flow-builder" data-instance={instance} data-active={String(active ?? true)}>
      Flow
      <button
        onClick={() => onFlowChange?.({
          flowName: 'Propagation Flow',
          version: '1.1',
          entry_node_id: 'extract',
          nodes: [{
            id: 'extract',
            type: 'agent',
            agent_id: 'gene_extractor',
            agent_display_name: 'Gene Extractor',
            output_key: 'genes',
            step_goal: 'Extract genes',
            prompt_version: 11,
            validation_attachments: [],
            validation_groups: [
              { group_id: 'replacement', state: 'replaced', validator_node_id: 'custom' },
              { group_id: 'supplemental', state: 'supplemental', validator_node_id: 'extra' },
            ],
          }],
          edges: [],
        })}
      >
        emit-flow-context
      </button>
      <button onClick={() => onVerifyRequest?.()}>verify-flow</button>
    </div>
    )
  },
  }
})

vi.mock('@/components/AgentStudio/AgentBrowser', () => ({
  default: ({
    onCloneToWorkshop,
    onDiscussWithClaude,
  }: {
    onCloneToWorkshop: (agentId: string) => void
    onDiscussWithClaude: (agentId: string, agentName: string, prompt?: string) => void
  }) => (
    <>
      <button onClick={() => onCloneToWorkshop('ca_source')}>clone-custom</button>
      <button onClick={() => onDiscussWithClaude('gene', 'Gene Extractor')}>discuss-agent</button>
      <button onClick={() => onDiscussWithClaude('gene', 'Gene Extractor', 'Draft a curator guide for Gene Extractor')}>
        discuss-draft
      </button>
    </>
  ),
}))

vi.mock('@/components/AgentStudio/PromptWorkshop/PromptWorkshop', async () => {
  const React = await import('react')
  const { UnsavedChangesDialog } = await import('@/components/AgentStudio/PromptWorkshop/dialogs/ConfirmDialogs')

  function PromptWorkshopMock({
    initialCustomAgentId,
    initialParentAgentId,
    authoringContextRef,
    opusConversation,
    onViewEnvelope,
    leaveGuardRef,
  }: {
    initialCustomAgentId?: string | null
    initialParentAgentId?: string | null
    authoringContextRef?: Ref<import('@/components/AgentStudio/PromptWorkshop/PromptWorkshop').WorkshopAuthoringContextHandle>
    opusConversation?: Array<{ content: string }>
    onViewEnvelope?: (agentId: string) => void
    leaveGuardRef?: Ref<{ requestLeave: () => Promise<boolean> }>
  }) {
    const [incomingPrompt, setIncomingPrompt] = React.useState('')
    React.useImperativeHandle(authoringContextRef, () => ({
      captureAuthoringContext: () => ({ prompt_draft: incomingPrompt }),
      applyAuthoringProposal: async (proposal) => {
        setIncomingPrompt(proposal.candidate.prompt_draft ?? '')
        return { applied: true, message: 'Applied' }
      },
    }))
    const [pendingLeave, setPendingLeave] = React.useState<((leave: boolean) => void) | null>(null)
    React.useImperativeHandle(leaveGuardRef, () => ({
      requestLeave: () => {
        if (!workshopMockState.dirty) return Promise.resolve(true)
        return new Promise<boolean>((resolve) => setPendingLeave(() => resolve))
      },
    }))
    return (
      <div data-testid="prompt-workshop">
        custom:{initialCustomAgentId || 'none'} parent:{initialParentAgentId || 'none'} incoming:{incomingPrompt || 'none'} conversation:{(opusConversation ?? []).map((message) => message.content).join('|') || 'none'}
        <button type="button" onClick={() => onViewEnvelope?.('gene')}>view-envelope</button>
        <UnsavedChangesDialog
          open={Boolean(pendingLeave)}
          onDiscard={() => {
            pendingLeave?.(true)
            setPendingLeave(null)
          }}
          onKeepEditing={() => {
            pendingLeave?.(false)
            setPendingLeave(null)
          }}
        />
      </div>
    )
  }

  return { default: PromptWorkshopMock }
})

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-search">{location.search || 'none'}</div>
}

const COLLAPSED_KEY = 'agent-studio-claude-collapsed'
const STALE_PANEL_KEY = 'react-resizable-panels:agent-studio-panels'
const PANEL_KEY = 'react-resizable-panels:agent-studio-panels-v2'

function mockViewportWidth(width: number) {
  vi.mocked(window.matchMedia).mockImplementation((query: string) => {
    const match = /max-width:\s*(\d+)px/.exec(query)
    return {
      matches: match ? width <= Number(match[1]) : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    } as MediaQueryList
  })
}

function getPanelSize(panelId: string): string | null {
  return document.getElementById(panelId)?.closest('[data-panel]')?.getAttribute('data-panel-size') ?? null
}

async function renderStudio(initialEntries: string[] = ['/agent-studio']) {
  render(
    <MemoryRouter initialEntries={initialEntries}>
      <AgentStudioPage />
    </MemoryRouter>
  )
  await waitFor(() => {
    expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
  })
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

function buildEmptyHistoryQueryResult() {
  return {
    data: undefined,
    isLoading: false,
    isSuccess: false,
    error: null,
  }
}

describe('AgentStudioPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    workshopMockState.dirty = false
    mockViewportWidth(1440)
    serviceMocks.fetchPromptCatalog.mockResolvedValue(EMPTY_CATALOG)
    serviceMocks.cloneAgentToWorkshop.mockResolvedValue({
      id: '11111111-1111-1111-1111-111111111111',
      template_source: 'gene',
    })
    historyMocks.useChatHistoryDetailQuery.mockReturnValue(buildEmptyHistoryQueryResult())
    historyMocks.useChatHistoryTranscriptQuery.mockReturnValue(buildEmptyHistoryQueryResult())
  })

  it('maps verification fields from FlowBuilder state into chat context', async () => {
    render(
      <MemoryRouter>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Flows' }))
    fireEvent.click(await screen.findByText('emit-flow-context'))

    await waitFor(() => {
      const context = screen.getByTestId('opus-chat-context')
      expect(context).toHaveTextContent('"step_goal":"Extract genes"')
      expect(context).toHaveTextContent('"prompt_version":11')
      expect(context).toHaveTextContent('"state":"replaced"')
      expect(context).toHaveTextContent('"state":"supplemental"')
    })

    fireEvent.click(screen.getByRole('tab', { name: 'Agent Workshop' }))
    await waitFor(() => {
      expect(screen.getByTestId('opus-chat-context')).toHaveTextContent(
        '"flow_name":"Propagation Flow"'
      )
    })
  })

  it('sends the complete targeted verification contract from Flow Builder', async () => {
    render(
      <MemoryRouter>
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Flows' }))
    fireEvent.click(await screen.findByText('verify-flow'))

    const message = screen.getByTestId('opus-chat-verify-message')
    expect(message).toHaveTextContent('get_current_flow() first')
    expect(message).toHaveTextContent('get_current_flow_instructions')
    expect(message).toHaveTextContent('returned next_call until complete=true')
    expect(message).toHaveTextContent('get_available_agents(category="Output")')
    expect(message).toHaveTextContent('view="summary"')
    expect(message).toHaveTextContent('scheduled_validators')
    expect(message).toHaveTextContent('get_tool_inventory(agent_id=')
    expect(message).toHaveTextContent('truncated=false and no next_cursor remains')
    expect(message).toHaveTextContent('get_domain_pack_validation_plan')
    expect(message).toHaveTextContent('compacted_tool_result')
    expect(message).toHaveTextContent('not terminal control nodes')
    expect(message).toHaveTextContent('Duplicate output_key is HIGH')
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

  describe('workshop leave guard', () => {
    async function renderOnWorkshop() {
      localStorage.setItem('agent-studio-tab', 'agent_workshop')
      render(
        <MemoryRouter>
          <AgentStudioPage />
        </MemoryRouter>
      )
      await screen.findByTestId('prompt-workshop')
      expect(screen.getByRole('tab', { name: 'Agent Workshop' })).toHaveAttribute('aria-selected', 'true')
    }

    it('switches tabs at once when the Workshop draft is clean', async () => {
      await renderOnWorkshop()

      fireEvent.click(screen.getByRole('tab', { name: 'Agents' }))

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Agents' })).toHaveAttribute('aria-selected', 'true')
      })
      expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument()
      expect(screen.queryByTestId('prompt-workshop')).not.toBeInTheDocument()
      expect(localStorage.getItem('agent-studio-tab')).toBe('agents')
    })

    it('stays on the Workshop after Keep editing', async () => {
      workshopMockState.dirty = true
      await renderOnWorkshop()

      fireEvent.click(screen.getByRole('tab', { name: 'Flows' }))
      const dialog = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
      fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }))

      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument()
      })
      expect(screen.getByRole('tab', { name: 'Agent Workshop' })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByTestId('prompt-workshop')).toBeInTheDocument()
      expect(localStorage.getItem('agent-studio-tab')).toBe('agent_workshop')
    })

    it('switches tabs and drops the Workshop after Discard', async () => {
      workshopMockState.dirty = true
      await renderOnWorkshop()

      fireEvent.click(screen.getByRole('tab', { name: 'Agents' }))
      const dialog = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
      fireEvent.click(within(dialog).getByRole('button', { name: 'Discard' }))

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Agents' })).toHaveAttribute('aria-selected', 'true')
      })
      expect(screen.queryByTestId('prompt-workshop')).not.toBeInTheDocument()
      expect(localStorage.getItem('agent-studio-tab')).toBe('agents')
    })

    it('guards the programmatic switch to the Agents envelope view', async () => {
      workshopMockState.dirty = true
      await renderOnWorkshop()

      fireEvent.click(screen.getByRole('button', { name: 'view-envelope' }))
      const dialog = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
      fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }))
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument()
      })
      expect(screen.getByRole('tab', { name: 'Agent Workshop' })).toHaveAttribute('aria-selected', 'true')

      fireEvent.click(screen.getByRole('button', { name: 'view-envelope' }))
      const dialogAgain = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
      fireEvent.click(within(dialogAgain).getByRole('button', { name: 'Discard' }))
      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Agents' })).toHaveAttribute('aria-selected', 'true')
      })
      expect(screen.queryByTestId('prompt-workshop')).not.toBeInTheDocument()
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
      expect.objectContaining({
        enabled: true,
        placeholderData: undefined,
      }),
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

  it('does not reuse stale chat_kind detail data when a minted Agent Studio session replaces a seed URL session', async () => {
    historyMocks.useChatHistoryDetailQuery.mockImplementation(({ sessionId }) => {
      if (sessionId === 'assistant-seed-session') {
        return buildSessionDetail('assistant-seed-session', 'assistant_chat')
      }

      if (sessionId === 'agent-studio-session-999') {
        // Simulate react-query placeholder detail data from the previous seed session.
        return buildSessionDetail('assistant-seed-session', 'assistant_chat')
      }

      return buildEmptyHistoryQueryResult()
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

      return buildEmptyHistoryQueryResult()
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

    fireEvent.click(screen.getByText('mint-durable-session'))

    await waitFor(() => {
      expect(screen.getByTestId('location-search')).toHaveTextContent(
        '?session_id=agent-studio-session-999&trace_id=trace-789'
      )
    })

    await waitFor(() => {
      expect(historyMocks.useChatHistoryTranscriptQuery).toHaveBeenLastCalledWith(
        {
          sessionId: 'agent-studio-session-999',
          chatKind: 'all',
        },
        { enabled: false },
      )
    })

    expect(screen.getByTestId('opus-chat-durable-session')).toHaveTextContent(
      'agent-studio-session-999'
    )
  })

  it('replaces a seed session_id with the minted Agent Studio session id without losing the live transcript snapshot', async () => {
    historyMocks.useChatHistoryDetailQuery.mockImplementation(({ sessionId }) => {
      if (sessionId === 'assistant-seed-session') {
        return buildSessionDetail('assistant-seed-session', 'assistant_chat')
      }

      if (sessionId === 'agent-studio-session-999') {
        return buildSessionDetail('agent-studio-session-999', 'agent_studio')
      }

      return buildEmptyHistoryQueryResult()
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

      return buildEmptyHistoryQueryResult()
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

  it('surfaces durable transcript errors after an internal URL swap mints a new session id', async () => {
    historyMocks.useChatHistoryDetailQuery.mockImplementation(({ sessionId }) => {
      if (sessionId === 'agent-studio-session-999') {
        return {
          data: undefined,
          isLoading: false,
          isSuccess: false,
          error: new Error('Unable to hydrate the new durable session.'),
        }
      }

      return buildEmptyHistoryQueryResult()
    })

    render(
      <MemoryRouter initialEntries={['/agent-studio?trace_id=trace-789']}>
        <LocationProbe />
        <AgentStudioPage />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByText('mint-durable-session'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to hydrate the new durable session.'
    )
  })

  describe('adaptive shell', () => {
    it('places the work surface first at 70% and Claude on the right at 30%', async () => {
      await renderStudio()

      const panels = document.querySelectorAll('[data-panel]')
      expect(panels).toHaveLength(2)
      expect(panels[0]).toContainElement(screen.getByRole('tab', { name: 'Agents' }))
      expect(panels[1]).toContainElement(screen.getByTestId('opus-chat'))
      expect(panels[0]).toHaveAttribute('data-panel-size', '70.0')
      expect(panels[1]).toHaveAttribute('data-panel-size', '30.0')
      expect(panels[1]).toHaveAttribute('data-panel-collapsible', 'true')
      expect(screen.getByTestId('opus-chat-variant')).toHaveTextContent('panel')
      expect(screen.queryByRole('button', { name: 'Show AI Chat' })).not.toBeInTheDocument()
    })

    it('collapses Claude to a rail and restores it without remounting the chat', async () => {
      await renderStudio()
      const chatBefore = screen.getByTestId('opus-chat')

      const hideButton = screen.getByRole('button', { name: 'Hide AI Chat' })
      expect(hideButton).toHaveAttribute('aria-controls', 'agent-studio-claude-panel')
      fireEvent.click(hideButton)

      const showButton = await screen.findByRole('button', { name: 'Show AI Chat' })
      expect(showButton).toHaveAttribute('aria-expanded', 'false')
      expect(showButton).toHaveAttribute('aria-controls', 'agent-studio-claude-panel')
      expect(getPanelSize('agent-studio-claude-panel')).toBe('0.0')
      expect(screen.getByTestId('opus-chat')).toBe(chatBefore)
      expect(document.getElementById('agent-studio-claude-panel')).toHaveAttribute('aria-hidden', 'true')
      await waitFor(() => {
        expect(showButton).toHaveFocus()
      })
      expect(localStorage.getItem(COLLAPSED_KEY)).toBe('true')

      fireEvent.click(showButton)

      await waitFor(() => {
        expect(screen.queryByRole('button', { name: 'Show AI Chat' })).not.toBeInTheDocument()
      })
      expect(getPanelSize('agent-studio-claude-panel')).toBe('30.0')
      expect(screen.getByTestId('opus-chat')).toBe(chatBefore)
      expect(document.getElementById('agent-studio-claude-panel')).not.toHaveAttribute('aria-hidden')
      await waitFor(() => {
        expect(screen.getByRole('textbox', { name: 'Ask about prompts' })).toHaveFocus()
      })
      expect(localStorage.getItem(COLLAPSED_KEY)).toBe('false')
    })

    it('shows unread and streaming indicators on the rail and clears unread on show', async () => {
      await renderStudio()

      fireEvent.click(screen.getByRole('button', { name: 'Hide AI Chat' }))
      const showButton = await screen.findByRole('button', { name: 'Show AI Chat' })
      expect(showButton).not.toHaveAccessibleDescription()
      expect(screen.queryByRole('progressbar', { name: 'AI Chat is responding' })).not.toBeInTheDocument()

      fireEvent.click(screen.getByText('start-streaming'))
      expect(screen.getByRole('progressbar', { name: 'AI Chat is responding' })).toBeInTheDocument()

      fireEvent.click(screen.getByText('simulate-live-conversation'))
      await waitFor(() => {
        expect(showButton).toHaveAccessibleDescription('2 new messages from AI Chat')
      })

      fireEvent.click(screen.getByText('stop-streaming'))
      expect(screen.queryByRole('progressbar', { name: 'AI Chat is responding' })).not.toBeInTheDocument()

      fireEvent.click(showButton)
      fireEvent.click(await screen.findByRole('button', { name: 'Hide AI Chat' }))
      expect(await screen.findByRole('button', { name: 'Show AI Chat' })).not.toHaveAccessibleDescription()
    })

    it('does not count assistant messages that arrive while Claude is visible', async () => {
      await renderStudio()

      fireEvent.click(screen.getByText('simulate-live-conversation'))
      fireEvent.click(screen.getByRole('button', { name: 'Hide AI Chat' }))

      expect(await screen.findByRole('button', { name: 'Show AI Chat' })).not.toHaveAccessibleDescription()
    })

    it('toggles Claude with Ctrl+. and Cmd+.', async () => {
      await renderStudio()

      fireEvent.keyDown(window, { key: '.', ctrlKey: true })
      expect(await screen.findByRole('button', { name: 'Show AI Chat' })).toBeInTheDocument()

      fireEvent.keyDown(window, { key: '.', metaKey: true })
      await waitFor(() => {
        expect(screen.queryByRole('button', { name: 'Show AI Chat' })).not.toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: 'Hide AI Chat' })).toBeInTheDocument()

      fireEvent.keyDown(window, { key: '.' })
      expect(screen.queryByRole('button', { name: 'Show AI Chat' })).not.toBeInTheDocument()
    })

    it('applies a persisted collapsed flag at desktop widths', async () => {
      localStorage.setItem(COLLAPSED_KEY, 'true')
      await renderStudio()

      expect(screen.getByRole('button', { name: 'Show AI Chat' })).toBeInTheDocument()
      expect(getPanelSize('agent-studio-claude-panel')).toBe('0.0')
      expect(screen.getByTestId('opus-chat')).toBeInTheDocument()
    })

    it('discards the stale 40/60 split once and saves the new layout under the new key', async () => {
      localStorage.setItem(STALE_PANEL_KEY, JSON.stringify({ '40:60': { layout: [40, 60] } }))
      await renderStudio()

      expect(localStorage.getItem(STALE_PANEL_KEY)).toBeNull()
      await waitFor(() => {
        expect(localStorage.getItem(PANEL_KEY)).not.toBeNull()
      })
      expect(getPanelSize('agent-studio-claude-panel')).toBe('30.0')
    })

    it('keeps the Flow Builder mounted and inactive while another tab is open', async () => {
      await renderStudio()
      expect(screen.queryByTestId('flow-builder')).not.toBeInTheDocument()

      fireEvent.click(screen.getByRole('tab', { name: 'Flows' }))
      const builder = screen.getByTestId('flow-builder')
      const instance = builder.getAttribute('data-instance')
      expect(builder).toHaveAttribute('data-active', 'true')

      fireEvent.click(screen.getByRole('tab', { name: 'Agents' }))
      expect(screen.getByTestId('flows-tab-panel')).not.toBeVisible()
      expect(screen.getByTestId('flow-builder')).toHaveAttribute('data-active', 'false')

      fireEvent.click(screen.getByRole('tab', { name: 'Flows' }))
      expect(screen.getByTestId('flows-tab-panel')).toBeVisible()
      expect(screen.getByTestId('flow-builder')).toHaveAttribute('data-instance', instance)
      expect(screen.getByTestId('flow-builder')).toHaveAttribute('data-active', 'true')
    })

    it('keeps the active tab across collapse and restore', async () => {
      await renderStudio()

      fireEvent.click(screen.getByRole('tab', { name: 'Flows' }))
      expect(screen.getByTestId('flow-builder')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Hide AI Chat' }))
      fireEvent.click(await screen.findByRole('button', { name: 'Show AI Chat' }))

      expect(screen.getByRole('tab', { name: 'Flows' })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByTestId('flow-builder')).toBeInTheDocument()
      expect(localStorage.getItem('agent-studio-tab')).toBe('flows')
    })

    it('replaces the panel with a drawer below 1100px and keeps the chat mounted across open and close', async () => {
      mockViewportWidth(1000)
      localStorage.setItem(COLLAPSED_KEY, 'true')
      await renderStudio()

      expect(document.querySelectorAll('[data-panel]')).toHaveLength(1)
      expect(screen.queryByRole('button', { name: 'Show AI Chat' })).not.toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: 'AI Chat' })).not.toBeInTheDocument()

      const launcher = screen.getByRole('button', { name: 'AI Chat' })
      expect(launcher).toHaveAttribute('aria-expanded', 'false')
      expect(launcher).toHaveAttribute('aria-controls', 'agent-studio-claude-drawer')
      const chatBefore = screen.getByTestId('opus-chat')
      expect(screen.getByTestId('opus-chat-variant')).toHaveTextContent('drawer')

      fireEvent.click(screen.getByText('simulate-live-conversation'))
      await waitFor(() => {
        expect(launcher).toHaveAccessibleDescription('2 new messages from AI Chat')
      })

      fireEvent.click(launcher)

      const dialog = await screen.findByRole('dialog', { name: 'AI Chat' })
      expect(dialog).toHaveAttribute('aria-modal', 'true')
      expect(launcher).toHaveAttribute('aria-expanded', 'true')
      expect(launcher).not.toHaveAccessibleDescription()
      expect(within(dialog).getByTestId('opus-chat')).toBe(chatBefore)
      await waitFor(() => {
        expect(screen.getByRole('textbox', { name: 'Ask about prompts' })).toHaveFocus()
      })

      fireEvent.click(within(dialog).getByRole('button', { name: 'Close AI Chat' }))
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'AI Chat' })).not.toBeInTheDocument()
      })
      expect(screen.getByTestId('opus-chat')).toBe(chatBefore)
      await waitFor(() => {
        expect(launcher).toHaveFocus()
      })

      fireEvent.click(launcher)
      const reopened = await screen.findByRole('dialog', { name: 'AI Chat' })
      fireEvent.keyDown(reopened, { key: 'Escape' })
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'AI Chat' })).not.toBeInTheDocument()
      })

      fireEvent.keyDown(window, { key: '.', ctrlKey: true })
      expect(await screen.findByRole('dialog', { name: 'AI Chat' })).toBeInTheDocument()
      // The drawer is never persisted; the collapsed flag is untouched at narrow widths.
      expect(localStorage.getItem(COLLAPSED_KEY)).toBe('true')
    })

    it('closes the drawer on scrim click', async () => {
      mockViewportWidth(1000)
      await renderStudio()

      fireEvent.click(screen.getByRole('button', { name: 'AI Chat' }))
      await screen.findByRole('dialog', { name: 'AI Chat' })

      const backdrop = document.querySelector('#agent-studio-claude-drawer .MuiBackdrop-root')
      expect(backdrop).not.toBeNull()
      await act(async () => {
        fireEvent.click(backdrop as Element)
      })
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'AI Chat' })).not.toBeInTheDocument()
      })
    })
    it('does not count a restored conversation as unread when Claude starts collapsed', async () => {
      localStorage.setItem(COLLAPSED_KEY, 'true')
      historyMocks.useChatHistoryDetailQuery.mockReturnValue(
        buildSessionDetail('agent-studio-session-12345678', 'agent_studio')
      )
      historyMocks.useChatHistoryTranscriptQuery.mockReturnValue(
        buildTranscript('agent-studio-session-12345678', 'agent_studio', [
          {
            message_id: 'message-1',
            role: 'user',
            message_type: 'text',
            content: 'Restored question',
            created_at: '2026-04-22T00:00:01Z',
          },
          {
            message_id: 'message-2',
            role: 'assistant',
            message_type: 'text',
            content: 'Restored answer',
            created_at: '2026-04-22T00:00:02Z',
          },
        ])
      )

      await renderStudio(['/agent-studio?session_id=agent-studio-session-12345678'])

      await waitFor(() => {
        expect(screen.getByTestId('opus-chat-initial-conversation')).toHaveTextContent(
          'Restored question|Restored answer'
        )
      })
      const showButton = screen.getByRole('button', { name: 'Show AI Chat' })
      expect(showButton).not.toHaveAccessibleDescription()

      fireEvent.click(screen.getByText('append-assistant-reply'))
      await waitFor(() => {
        expect(showButton).toHaveAccessibleDescription('1 new message from AI Chat')
      })
    })

    it('does not count a transcript that hydrates after mount as unread when Claude starts collapsed', async () => {
      localStorage.setItem(COLLAPSED_KEY, 'true')
      historyMocks.useChatHistoryDetailQuery.mockReturnValue(
        buildSessionDetail('agent-studio-session-12345678', 'agent_studio')
      )
      let transcriptResolved = false
      historyMocks.useChatHistoryTranscriptQuery.mockImplementation(() => (
        transcriptResolved
          ? buildTranscript('agent-studio-session-12345678', 'agent_studio', [
              {
                message_id: 'message-1',
                role: 'user',
                message_type: 'text',
                content: 'Restored question',
                created_at: '2026-04-22T00:00:01Z',
              },
              {
                message_id: 'message-2',
                role: 'assistant',
                message_type: 'text',
                content: 'Restored answer',
                created_at: '2026-04-22T00:00:02Z',
              },
            ])
          : { data: undefined, isLoading: true, isSuccess: false, error: null }
      ))

      const view = render(
        <MemoryRouter initialEntries={['/agent-studio?session_id=agent-studio-session-12345678']}>
          <AgentStudioPage />
        </MemoryRouter>
      )
      await waitFor(() => {
        expect(serviceMocks.fetchPromptCatalog).toHaveBeenCalledTimes(1)
      })
      expect(screen.getByTestId('opus-chat-initial-conversation')).toHaveTextContent('none')

      transcriptResolved = true
      view.rerender(
        <MemoryRouter initialEntries={['/agent-studio?session_id=agent-studio-session-12345678']}>
          <AgentStudioPage />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(screen.getByTestId('opus-chat-initial-conversation')).toHaveTextContent(
          'Restored question|Restored answer'
        )
      })
      const showButton = screen.getByRole('button', { name: 'Show AI Chat' })
      expect(showButton).not.toHaveAccessibleDescription()

      fireEvent.click(screen.getByText('append-assistant-reply'))
      await waitFor(() => {
        expect(showButton).toHaveAccessibleDescription('1 new message from AI Chat')
      })
    })

    it('reveals a collapsed Claude and focuses the input when a discuss request arrives', async () => {
      localStorage.setItem(COLLAPSED_KEY, 'true')
      await renderStudio()
      expect(screen.getByRole('button', { name: 'Show AI Chat' })).toBeInTheDocument()

      fireEvent.click(screen.getByText('discuss-agent'))

      await waitFor(() => {
        expect(screen.queryByRole('button', { name: 'Show AI Chat' })).not.toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: 'Hide AI Chat' })).toBeInTheDocument()
      expect(screen.getByTestId('opus-chat-discuss-message')).toHaveTextContent('Agent ID: gene')
      await waitFor(() => {
        expect(screen.getByRole('textbox', { name: 'Ask about prompts' })).toHaveFocus()
      })
    })

    it('opens the drawer and focuses the input when a discuss request arrives at narrow width', async () => {
      mockViewportWidth(1000)
      await renderStudio()
      expect(screen.queryByRole('dialog', { name: 'AI Chat' })).not.toBeInTheDocument()

      fireEvent.click(screen.getByText('discuss-agent'))

      expect(await screen.findByRole('dialog', { name: 'AI Chat' })).toBeInTheDocument()
      expect(screen.getByTestId('opus-chat-discuss-message')).toHaveTextContent('Agent ID: gene')
      await waitFor(() => {
        expect(screen.getByRole('textbox', { name: 'Ask about prompts' })).toHaveFocus()
      })
    })

    it('reveals a collapsed Claude when Flow Builder requests verification', async () => {
      localStorage.setItem(COLLAPSED_KEY, 'true')
      await renderStudio()

      fireEvent.click(screen.getByRole('tab', { name: 'Flows' }))
      fireEvent.click(await screen.findByText('verify-flow'))

      await waitFor(() => {
        expect(screen.queryByRole('button', { name: 'Show AI Chat' })).not.toBeInTheDocument()
      })
      expect(screen.getByTestId('opus-chat-verify-message')).toHaveTextContent('get_current_flow() first')
    })

    it('sends the drafting prompt when one is supplied and the generic message otherwise', async () => {
      await renderStudio()

      fireEvent.click(await screen.findByText('discuss-draft'))
      await waitFor(() => {
        expect(screen.getByTestId('opus-chat-discuss-message')).toHaveTextContent('Draft a curator guide for Gene Extractor')
      })
      expect(screen.getByTestId('opus-chat-discuss-message')).not.toHaveTextContent("I'd like to discuss")

      fireEvent.click(screen.getByText('discuss-agent'))
      await waitFor(() => {
        expect(screen.getByTestId('opus-chat-discuss-message')).toHaveTextContent("I'd like to discuss the **Gene Extractor** agent")
      })
      expect(screen.getByTestId('opus-chat-discuss-message')).toHaveTextContent('Agent ID: gene')
      expect(screen.getByTestId('opus-chat-discuss-message')).not.toHaveTextContent('Draft a curator guide')
    })
  })
})

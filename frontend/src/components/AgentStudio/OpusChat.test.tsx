import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'

import OpusChat, { resetSharedOpusChatStateForTests } from './OpusChat'
import type { ChatContext, PromptInfo } from '@/types/promptExplorer'
import { logger } from '@/services/logger'

const DISEASE_VALIDATOR: PromptInfo = {
  agent_id: 'disease_validator',
  agent_name: 'Disease Validator',
  description: 'Checks disease terms.',
  base_prompt: 'Validate diseases.',
  source_file: 'database',
  has_group_rules: false,
  group_rules: {},
  tools: [],
}

const serviceMocks = vi.hoisted(() => ({
  createAgentStudioSession: vi.fn(),
  streamOpusChat: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)

describe('OpusChat', () => {
  beforeEach(() => {
    resetSharedOpusChatStateForTests()
    vi.clearAllMocks()
    serviceMocks.createAgentStudioSession.mockResolvedValue({
      session_id: 'agent-studio-session-12345678',
      created_at: '2026-04-23T00:00:00Z',
      updated_at: '2026-04-23T00:00:00Z',
    })
  })

  it('starts a separate chat while preserving the editor context and previous conversation', async () => {
    Element.prototype.scrollIntoView = vi.fn()
    serviceMocks.createAgentStudioSession.mockResolvedValue({ session_id: 'fresh-chat' })
    serviceMocks.streamOpusChat.mockImplementation(async function* () { yield { type: 'DONE' } })
    const context: ChatContext = { active_tab: 'flows', flow_name: 'Unsaved stock flow' }
    function Harness() {
      const [session, setSession] = useState('old-chat')
      return <>
        <button onClick={() => setSession('old-chat')}>Reopen previous</button>
        <OpusChat context={context} durableSessionId={session}
          sourceSessionId="old-chat" initialConversation={[{ role: 'user', content: 'Previous discussion' }]}
          onDurableSessionIdChange={setSession} />
      </>
    }
    render(<Harness />)
    expect(screen.getByText('Previous discussion')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))
    await waitFor(() => expect(screen.queryByText('Previous discussion')).not.toBeInTheDocument())
    const input = screen.getByPlaceholderText('Ask about flows...')
    fireEvent.change(input, { target: { value: 'Help with this draft' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    await waitFor(() => expect(serviceMocks.streamOpusChat).toHaveBeenCalledWith(
      [expect.objectContaining({ role: 'user', content: 'Help with this draft' })], context, 'fresh-chat'))
    fireEvent.click(screen.getByRole('button', { name: 'Reopen previous' }))
    expect(await screen.findByText('Previous discussion')).toBeInTheDocument()
  })

  it('shows progress and blocks duplicate resets and sending while the new chat is created', async () => {
    let finish!: (value: { session_id: string }) => void
    serviceMocks.createAgentStudioSession.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const changeSession = vi.fn()
    render(<OpusChat context={{ active_tab: 'flows' }} durableSessionId="old-chat"
      onDurableSessionIdChange={changeSession} />)
    const button = screen.getByRole('button', { name: 'New chat' })
    fireEvent.click(button)
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(within(button).getByRole('progressbar')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Ask about flows...')).toBeDisabled()
    fireEvent.click(button)
    expect(serviceMocks.createAgentStudioSession).toHaveBeenCalledTimes(1)
    finish({ session_id: 'fresh-chat' })
    await waitFor(() => expect(changeSession).toHaveBeenCalledWith('fresh-chat'))
  })

  it('blocks an old Workshop action during reset and ignores completion after unmount', async () => {
    Element.prototype.scrollIntoView = vi.fn()
    let finish!: (value: { session_id: string }) => void
    serviceMocks.createAgentStudioSession.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TOOL_RESULT', tool_name: 'request_workshop_action', result: {
        success: true, contract_version: 'workshop_action.v1',
        request: { action: 'open_agent', agent_id: 'ca_stock' }, label: 'Open Stock reader',
      } }
      yield { type: 'DONE' }
    })
    const changeSession = vi.fn()
    const open = vi.fn()
    const { unmount } = render(<OpusChat context={{ active_tab: 'flows' }} durableSessionId="old-chat"
      onWorkshopAction={open} onDurableSessionIdChange={changeSession} />)
    const input = screen.getByPlaceholderText('Ask about flows...')
    fireEvent.change(input, { target: { value: 'Edit stock reader' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    const action = await screen.findByRole('button', { name: 'Open Stock reader' })
    await waitFor(() => expect(action).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))
    expect(action).toBeDisabled()
    fireEvent.click(action)
    expect(open).not.toHaveBeenCalled()
    unmount()
    finish({ session_id: 'fresh-chat' })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(changeSession).not.toHaveBeenCalled()
  })

  it('keeps the current conversation and unsent message when creating a chat fails', async () => {
    Element.prototype.scrollIntoView = vi.fn()
    serviceMocks.createAgentStudioSession.mockRejectedValue(new Error('Unavailable'))
    const changeSession = vi.fn()
    render(<OpusChat context={{ active_tab: 'flows' }} durableSessionId="old-chat"
      initialConversation={[{ role: 'user', content: 'Previous discussion' }]}
      onDurableSessionIdChange={changeSession} />)
    fireEvent.change(screen.getByPlaceholderText('Ask about flows...'), { target: { value: 'Unsent message' } })
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))
    expect(await screen.findByText(/Could not start a new chat/)).toBeInTheDocument()
    expect(screen.getByText('Previous discussion')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Ask about flows...')).toHaveValue('Unsent message')
    expect(changeSession).not.toHaveBeenCalled()
  })

  it('uses the send-time context capture rather than the render projection', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'DONE' }
    })
    const capturedContext: ChatContext = {
      active_tab: 'flows',
      flow_name: 'Latest unsaved name',
      flow_draft_fingerprint: `sha256:${'a'.repeat(64)}`,
    }
    const captureContext = vi.fn(() => Promise.resolve(capturedContext))

    render(
      <OpusChat
        context={{ active_tab: 'flows', flow_name: 'Stale render name' }}
        captureContext={captureContext}
      />
    )
    const input = screen.getByPlaceholderText('Ask about flows...')
    fireEvent.change(input, { target: { value: 'Review this exact draft' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    expect(captureContext).toHaveBeenCalledTimes(1)
    await waitFor(() => {
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledWith(
        expect.any(Array),
        capturedContext,
        'agent-studio-session-12345678'
      )
    })
  })

  it.each(['flows', 'agent_workshop'] as const)('continues once after Apply in %s with the updated draft and shows both busy states', async (activeTab) => {
    let finishStream!: () => void
    const streaming = new Promise<void>((resolve) => { finishStream = resolve })
    let finishApply!: (value: { applied: boolean; message: string }) => void
    const applying = new Promise<{ applied: boolean; message: string }>((resolve) => { finishApply = resolve })
    const workshop = activeTab === 'agent_workshop'
    const proposal = {
      contract_version: workshop ? 'workshop_authoring_proposal.v1' : 'flow_authoring_proposal.v1',
      success: true, valid: true, pending_user_approval: true,
      base_draft_fingerprint: 'sha256:base', candidate_draft_fingerprint: 'sha256:candidate',
      change_summary: 'Update instructions', findings: [], diff: [],
      candidate: workshop ? { draft_name: 'Reader' } : { name: 'Flow', description: '', flow_definition: { nodes: [], edges: [] } },
    }
    serviceMocks.streamOpusChat.mockImplementationOnce(async function* () {
      yield { type: 'TOOL_RESULT', tool_name: workshop ? 'propose_workshop_draft_update' : 'propose_flow_draft_update', result: proposal }
      await streaming
      yield { type: 'DONE' }
    }).mockImplementation(async function* () { yield { type: 'DONE' } })
    let current: ChatContext = { active_tab: activeTab, flow_name: 'Before Apply' }
    const captureContext = vi.fn(async () => current)
    const apply = vi.fn(async () => {
      const result = await applying
      current = { active_tab: activeTab, flow_name: 'After Apply' }
      return result
    })
    render(<OpusChat context={current} captureContext={captureContext}
      onApplyFlowProposal={apply} onApplyWorkshopProposal={apply} />)
    const input = screen.getByPlaceholderText(workshop ? 'Ask about your workshop draft...' : 'Ask about flows...')
    fireEvent.change(input, { target: { value: 'Update the instructions, then help with the next step.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    expect(await screen.findByRole('progressbar', { name: 'Preparing changes' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Preparing…/ })).toBeDisabled()
    finishStream()
    const button = await screen.findByRole('button', { name: 'Apply changes' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    expect(await screen.findByRole('progressbar', { name: 'Applying changes' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Applying…/ })).toBeDisabled()
    finishApply({ applied: true, message: 'Applied to draft.' })
    await waitFor(() => expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(2))
    expect(serviceMocks.streamOpusChat.mock.calls[1][1]).toEqual(current)
    expect(JSON.stringify(serviceMocks.streamOpusChat.mock.calls[1][0])).toContain('Continue with the next step we discussed')
    expect(apply).toHaveBeenCalledTimes(1)
    expect(captureContext).toHaveBeenCalledTimes(2)
  })

  it('sends an explicit Flow continuation with current context and permits a later retry', async () => {
    serviceMocks.streamOpusChat.mockImplementation(async function* () { yield { type: 'DONE' } })
    const context: ChatContext = { active_tab: 'flows', flow_name: 'Preserved Flow' }
    const captureContext = vi.fn().mockResolvedValue(context)
    const message = 'Propose adding saved agent ca_saved to this Flow; review before Apply.'
    function Harness() {
      const [request, setRequest] = useState<string | null>(null)
      return <>
        <button onClick={() => setRequest(message)}>Review in Flow</button>
        <OpusChat context={context} captureContext={captureContext} discussMessage={request}
          onDiscussMessageSent={() => setRequest(null)} />
      </>
    }
    render(<Harness />)
    fireEvent.click(screen.getByText('Review in Flow'))
    await waitFor(() => expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1))
    expect(serviceMocks.streamOpusChat.mock.calls[0][0]).toEqual([
      expect.objectContaining({ role: 'user', content: message }),
    ])
    expect(serviceMocks.streamOpusChat.mock.calls[0][1]).toEqual(context)
    await waitFor(() => expect(screen.getByPlaceholderText('Ask about flows...')).not.toBeDisabled())
    fireEvent.click(screen.getByText('Review in Flow'))
    await waitFor(() => expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(2))
  })

  it('loads the complete targeted flow verification contract from the quick action', () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    render(<OpusChat context={{ active_tab: 'flows' }} />)

    fireEvent.click(screen.getByText('Verify my flow'))

    const prompt = (screen.getByPlaceholderText('Ask about flows...') as HTMLInputElement).value
    expect(prompt).toContain('get_current_flow() first')
    expect(prompt).toContain('get_current_flow_instructions')
    expect(prompt).toContain('returned next_call until complete=true')
    expect(prompt).toContain('get_available_agents(category="Output")')
    expect(prompt).toContain('view="summary"')
    expect(prompt).toContain('scheduled_validators')
    expect(prompt).toContain('get_tool_inventory(agent_id=')
    expect(prompt).toContain('truncated=false and no next_cursor remains')
    expect(prompt).toContain('get_domain_pack_validation_plan')
    expect(prompt).toContain('compacted_tool_result')
    expect(prompt).toContain('not terminal control nodes')
    expect(prompt).toContain('Duplicate output_key is HIGH')
  })

  it('publishes conversation snapshots for tool-idea transcript capture', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TEXT_DELTA', delta: 'Assistant response' }
      yield { type: 'DONE' }
    })

    const onConversationSnapshotChange = vi.fn()
    const context: ChatContext = {
      active_tab: 'agent_workshop',
    }

    render(
      <OpusChat
        context={context}
        onConversationSnapshotChange={onConversationSnapshotChange}
      />
    )

    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Help me design a tool request' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1)
    })

    await waitFor(() => {
      const calls = onConversationSnapshotChange.mock.calls
      expect(calls.length).toBeGreaterThan(0)
      const latestSnapshot = calls[calls.length - 1][0]
      expect(latestSnapshot).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            role: 'user',
            content: 'Help me design a tool request',
          }),
          expect.objectContaining({
            role: 'assistant',
            content: 'Assistant response',
          }),
        ])
      )
    })
  })

  it('renders a seeded durable transcript and source pill', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    const context: ChatContext = {
      active_tab: 'agents',
      session_id: 'assistant-session-12345678',
      trace_id: 'trace-789',
    }

    render(
      <OpusChat
        context={context}
        sourceSessionId="assistant-session-12345678"
        initialConversation={[
          {
            role: 'user',
            content: 'Why did the assistant recommend gene X?',
            timestamp: '2026-04-22T00:00:01Z',
          },
          {
            role: 'assistant',
            content: 'Because the prior turns emphasized evidence rank and assay quality.',
            timestamp: '2026-04-22T00:00:02Z',
          },
        ]}
      />
    )

    expect(screen.getByText('Why did the assistant recommend gene X?')).toBeInTheDocument()
    expect(
      screen.getByText('Because the prior turns emphasized evidence rank and assay quality.')
    ).toBeInTheDocument()
    expect(screen.getByText('Loaded from durable chat assistan...')).toBeInTheDocument()
  })

  it('creates and reports a durable Agent Studio session on the first user turn', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TEXT_DELTA', delta: 'First durable reply' }
      yield { type: 'DONE' }
    })

    const onDurableSessionIdChange = vi.fn()
    const context: ChatContext = {
      active_tab: 'agents',
      trace_id: 'trace-789',
    }

    render(
      <OpusChat
        context={context}
        onDurableSessionIdChange={onDurableSessionIdChange}
      />
    )

    const input = screen.getByPlaceholderText('Ask about prompts...')
    fireEvent.change(input, { target: { value: 'Please review this prompt setup.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(serviceMocks.createAgentStudioSession).toHaveBeenCalledTimes(1)
    })

    await waitFor(() => {
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledWith(
        [
          {
            role: 'user',
            content: 'Please review this prompt setup.',
          },
        ],
        context,
        'agent-studio-session-12345678',
      )
    })

    expect(onDurableSessionIdChange).toHaveBeenCalledWith('agent-studio-session-12345678')
  })

  it('keeps using the first minted session when the parent re-renders before the prop catches up', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TEXT_DELTA', delta: 'Durable reply' }
      yield { type: 'DONE' }
    })

    function Harness() {
      const [renderCount, setRenderCount] = useState(0)

      return (
        <>
          <div data-testid="render-count">{renderCount}</div>
          <OpusChat
            context={{ active_tab: 'agents' }}
            onDurableSessionIdChange={() => setRenderCount((currentCount) => currentCount + 1)}
          />
        </>
      )
    }

    render(<Harness />)

    const input = screen.getByPlaceholderText('Ask about prompts...')

    fireEvent.change(input, { target: { value: 'First durable question' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(serviceMocks.createAgentStudioSession).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(screen.getByTestId('render-count')).toHaveTextContent('1')
    })

    fireEvent.change(input, { target: { value: 'Second durable question' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(2)
    })

    expect(serviceMocks.createAgentStudioSession).toHaveBeenCalledTimes(1)
    expect(serviceMocks.streamOpusChat.mock.calls[1][2]).toBe('agent-studio-session-12345678')
  })

  it('reattaches to an active AI Chat turn after unmount without starting a duplicate stream', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    let releaseCompletion: () => void = () => {}
    const completionGate = new Promise<void>((resolve) => {
      releaseCompletion = resolve
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TEXT_DELTA', delta: 'Partial reply' }
      await completionGate
      yield { type: 'TEXT_DELTA', delta: ' completed' }
      yield { type: 'DONE' }
    })

    const first = render(
      <OpusChat
        context={{ active_tab: 'agents' }}
        onDurableSessionIdChange={vi.fn()}
      />
    )

    const input = screen.getByPlaceholderText('Ask about prompts...')
    fireEvent.change(input, { target: { value: 'Keep this running across navigation.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    expect(await screen.findByText('Partial reply')).toBeInTheDocument()
    expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1)

    first.unmount()

    render(
      <OpusChat
        context={{ active_tab: 'agents', session_id: 'agent-studio-session-12345678' }}
        durableSessionId="agent-studio-session-12345678"
        sourceSessionId="agent-studio-session-12345678"
      />
    )

    expect(screen.getByText('Partial reply')).toBeInTheDocument()
    expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1)

    releaseCompletion()

    expect(await screen.findByText('Partial reply completed')).toBeInTheDocument()
    expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1)
  })

  it('reuses an existing durable Agent Studio session instead of minting another one', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TEXT_DELTA', delta: 'Resumed reply' }
      yield { type: 'DONE' }
    })

    const context: ChatContext = {
      active_tab: 'agents',
      session_id: 'agent-studio-session-existing',
      trace_id: 'trace-789',
    }

    render(
      <OpusChat
        context={context}
        durableSessionId="agent-studio-session-existing"
        sourceSessionId="agent-studio-session-existing"
        initialConversation={[
          {
            role: 'assistant',
            content: 'Existing durable transcript',
            timestamp: '2026-04-22T00:00:02Z',
          },
        ]}
      />
    )

    const input = screen.getByPlaceholderText('Ask about prompts...')
    fireEvent.change(input, { target: { value: 'Continue from this session.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledWith(
        [
          {
            role: 'assistant',
            content: 'Existing durable transcript',
          },
          {
            role: 'user',
            content: 'Continue from this session.',
          },
        ],
        context,
        'agent-studio-session-existing',
      )
    })

    expect(serviceMocks.createAgentStudioSession).not.toHaveBeenCalled()
  })

  it('keeps AI-assisted feedback modeless so the conversation can still be inspected while comments are drafted', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    render(
      <OpusChat
        context={{ active_tab: 'agents', trace_id: 'trace-789' }}
        initialConversation={[
          {
            role: 'user',
            content: 'The prompt missed an allele edge case.',
            timestamp: '2026-04-22T00:00:01Z',
          },
          {
            role: 'assistant',
            content: 'We should send that to the developers.',
            timestamp: '2026-04-22T00:00:02Z',
          },
        ]}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'AI-assisted' }))
    await waitFor(() => {
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    const dialog = screen.getByRole('dialog', { name: 'Submit Feedback to Developers?' })
    expect(dialog).toHaveAttribute('aria-modal', 'false')
    expect(document.querySelector('.MuiBackdrop-root')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move feedback popup' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close feedback popup' })).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText(/add any additional comments/i), {
      target: { value: 'Please include the prior trace context.' },
    })
    fireEvent.change(screen.getByPlaceholderText('Ask about prompts...'), {
      target: { value: 'I can still use the chat behind the popup.' },
    })

    expect(screen.getByPlaceholderText(/add any additional comments/i)).toHaveValue(
      'Please include the prior trace context.'
    )
    expect(screen.getByPlaceholderText('Ask about prompts...')).toHaveValue(
      'I can still use the chat behind the popup.'
    )
  })

  it('renders agr_curation_query method arguments readably in tool calls', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield {
        type: 'TOOL_USE',
        tool_name: 'agr_curation_query',
        tool_input: {
          method: 'get_gene_by_id',
          gene_id: 'FB:FBgn0259685',
          data_provider: 'FB',
          ontology_term_type: 'DOTerm',
          curie: 'DOID:0050156',
        },
      }
      yield {
        type: 'TOOL_RESULT',
        tool_name: 'agr_curation_query',
        result: {
          success: true,
          result_count: 1,
        },
      }
      yield { type: 'TEXT_DELTA', delta: 'I checked the curation lookup.' }
      yield { type: 'DONE' }
    })

    render(<OpusChat context={{ active_tab: 'agents' }} />)

    const input = screen.getByPlaceholderText('Ask about prompts...')
    fireEvent.change(input, { target: { value: 'Check this lookup.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    const toolCallsToggle = await screen.findByText('Tool Calls (1)')
    fireEvent.click(toolCallsToggle)

    expect(await screen.findByText(/AGR Curation: Gene by ID/)).toBeInTheDocument()
    expect(screen.getByText(/Method: get_gene_by_id/)).toBeInTheDocument()
    expect(screen.getByText(/Gene ID: FB:FBgn0259685/)).toBeInTheDocument()
    expect(screen.getByText(/Data Provider: FB/)).toBeInTheDocument()
    expect(screen.getByText(/Ontology Term Type: DOTerm/)).toBeInTheDocument()
    expect(screen.getByText(/CURIE: DOID:0050156/)).toBeInTheDocument()
    expect(screen.queryByText(/"gene_id"/)).not.toBeInTheDocument()
  })

  it('renders profile changes in the shared Workshop review with one Apply action', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
    const before = { mode: 'profile_bound_generic', schemaKey: '', profilePin: null,
      profileContract: { name: 'Details', semantic_class: 'item', fields: [] } }
    const after = { ...before, profileContract: { ...before.profileContract,
      fields: [{ key: 'paper_labels', value_schema: { kind: 'array', items: { kind: 'string' } }, required: true, source_labels: ['Names in paper'] }] } }
    const proposal = { contract_version: 'workshop_authoring_proposal.v1', success: true, valid: true, pending_user_approval: true,
      base_draft_fingerprint: 'base', candidate_draft_fingerprint: 'candidate', findings: [], change_summary: 'Collect names',
      diff: [{ kind: 'changed', path: 'custom_agent.output_contract', before, after }], candidate: { draft_name: 'Reader', draft_output: after } }
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TOOL_RESULT', tool_name: 'propose_workshop_draft_update', result: proposal }
      yield { type: 'DONE' }
    })
    const apply = vi.fn().mockResolvedValue({ applied: true, message: 'Applied without saving.' })
    render(<OpusChat context={{ active_tab: 'agent_workshop' }} onApplyWorkshopProposal={apply} />)
    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Collect the names used in the paper.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    expect(await screen.findByRole('region', { name: 'Output Structure candidate comparison' })).toBeInTheDocument()
    expect(screen.getByText('Source: AI Chat')).toBeInTheDocument()
    expect(screen.getByText(/Names in paper/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply to draft' })).not.toBeInTheDocument()
    expect(apply).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }))
    await waitFor(() => expect(apply).toHaveBeenCalledWith(proposal))
    expect(await screen.findByText(/Proposal applied to the draft. It has not been saved/)).toBeInTheDocument()
  })

  it.each(['open', 'dismiss', 'stale'])('offers a curator-controlled Workshop action: %s', async (decision) => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn(), writable: true })
    const action = {
      success: true, contract_version: 'workshop_action.v1', request: { action: 'open_agent', agent_id: 'ca_stock', node_id: 'stock' },
      label: 'Open Stock reader', source: { agent_id: 'ca_stock', name: 'Stock reader', updated_at: 'now', agent_revision_id: 'revision-2' },
      origin: { flow_draft_fingerprint: 'flow-fingerprint', node_id: 'stock', agent_id: 'ca_stock', agent_revision_id: 'revision-1' },
      active_tab: 'flows', flow_draft_fingerprint: 'flow-fingerprint', workshop_draft_fingerprint: null,
      saved: false, message: 'Nothing saved.',
    }
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TOOL_RESULT', tool_name: 'request_workshop_action', result: action }
      yield { type: 'DONE' }
    })
    const open = vi.fn().mockResolvedValue(undefined)
    if (decision === 'stale') open.mockRejectedValue(new Error('Your draft changed; ask for a fresh action.'))
    render(<OpusChat context={{ active_tab: 'flows' }} onWorkshopAction={open} />)
    const input = screen.getByPlaceholderText('Ask about flows...')
    fireEvent.change(input, { target: { value: 'Edit this stock reader' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    const button = await screen.findByRole('button', { name: 'Open Stock reader' })
    await waitFor(() => expect(button).toBeEnabled())
    expect(open).not.toHaveBeenCalled()
    if (decision === 'dismiss') {
      fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
      expect(open).not.toHaveBeenCalled()
    } else {
      fireEvent.click(button)
      await waitFor(() => expect(open).toHaveBeenCalledWith(action))
    }
    if (decision === 'stale') {
      expect(await screen.findByText('Your draft changed; ask for a fresh action.')).toBeInTheDocument()
      expect(button).toBeEnabled()
    } else await waitFor(() => expect(screen.queryByRole('button', { name: 'Open Stock reader' })).not.toBeInTheDocument())
  })

  it.each(['Apply changes', 'Cancel', 'Escape', 'failure', 'invalid'])('requires complete Workshop review before %s', async (action) => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
    const proposal = {
      contract_version: 'workshop_authoring_proposal.v1',
      success: action !== 'invalid', valid: action !== 'invalid', pending_user_approval: action !== 'invalid',
      base_draft_fingerprint: 'sha256:base', candidate_draft_fingerprint: 'sha256:candidate',
      change_summary: 'Rename the reader',
      findings: action === 'invalid'
        ? [{ code: 'unavailable_tool', severity: 'error', path: 'custom_agent.tool_ids', message: 'Choose an authorized tool.' }] : [],
      diff: [{ kind: 'changed', path: 'custom_agent.name', before: 'Original', after: 'Revised' }],
      candidate: { draft_name: 'Revised', prompt_draft: 'Private candidate instructions' },
    }
    serviceMocks.streamOpusChat.mockImplementationOnce(async function* () {
      yield { type: 'TOOL_RESULT', tool_name: 'propose_workshop_draft_update', result: proposal }
      if (action === 'failure') yield { type: 'ERROR', message: 'Turn failed after proposal' }
      yield { type: 'DONE' }
    }).mockImplementation(async function* () { yield { type: 'DONE' } })
    const onApplyWorkshopProposal = vi.fn().mockResolvedValue({ applied: true, message: 'Applied without saving.' })
    const snapshot = vi.fn()
    render(<OpusChat context={{ active_tab: 'agent_workshop' }}
      onApplyWorkshopProposal={onApplyWorkshopProposal} onConversationSnapshotChange={snapshot} />)
    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Rename this agent.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    if (action === 'failure') {
      await waitFor(() => expect(screen.getByText(/Turn failed after proposal/)).toBeInTheDocument())
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(onApplyWorkshopProposal).not.toHaveBeenCalled()
      return
    }
    expect(await screen.findByRole('dialog', { name: 'Review agent changes' })).toBeInTheDocument()
    if (action === 'invalid') {
      expect(screen.getByText('Choose an authorized tool.')).toBeInTheDocument()
      expect(screen.queryByText(/Unknown error/)).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled()
      expect(onApplyWorkshopProposal).not.toHaveBeenCalled()
      return
    }
    expect(screen.getByText('Original')).toBeInTheDocument()
    expect(screen.getByText('Revised')).toBeInTheDocument()
    expect(onApplyWorkshopProposal).not.toHaveBeenCalled()
    if (action === 'Escape') {
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape', code: 'Escape' })
    } else {
      fireEvent.click(screen.getByRole('button', { name: action }))
    }
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(onApplyWorkshopProposal).toHaveBeenCalledTimes(action === 'Apply changes' ? 1 : 0)
    expect(JSON.stringify(snapshot.mock.calls)).not.toContain('Private candidate instructions')
  })

  it.each(['Apply changes', 'Cancel', 'failed Apply', 'unavailable Apply'])('requires explicit review of a transient flow proposal: %s', async (decision) => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })
    const proposal = {
      contract_version: 'flow_authoring_proposal.v1',
      success: true,
      valid: true,
      pending_user_approval: true,
      base_draft_fingerprint: `sha256:${'a'.repeat(64)}`,
      candidate_draft_fingerprint: `sha256:${'b'.repeat(64)}`,
      change_summary: 'Add gene extraction after Initial Instructions.',
      diff: [{
        kind: 'changed',
        path: 'flow_definition.nodes.node_0.data.task_instructions',
        before: 'Old instructions',
        after: 'Extract genes.',
      }],
      findings: [],
      candidate: {
        name: 'Gene flow',
        description: 'Extract genes.',
        flow_definition: {
          version: '1.1',
          entry_node_id: 'node_0',
          nodes: [{
            id: 'node_0',
            type: 'task_input',
            position: { x: 0, y: 0 },
            data: {
              agent_id: 'task_input',
              agent_display_name: 'Initial Instructions',
              task_instructions: 'Extract genes.',
              output_key: 'task_input',
            },
          }],
          edges: [],
        },
      },
    }
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TOOL_RESULT', tool_name: 'propose_flow_draft_update', result: proposal }
      yield { type: 'DONE' }
    })
    const onApplyFlowProposal = vi.fn().mockResolvedValue({
      applied: true,
      message: 'Proposal applied to the draft. Save remains manual.',
    })
    if (decision === 'failed Apply') onApplyFlowProposal.mockResolvedValue({
      applied: false, message: 'Your latest edits were preserved.',
    })
    if (decision === 'unavailable Apply') onApplyFlowProposal.mockRejectedValue(new Error('Connection lost'))
    render(<OpusChat context={{ active_tab: 'flows' }} onApplyFlowProposal={onApplyFlowProposal} />)

    const input = screen.getByPlaceholderText('Ask about flows...')
    fireEvent.change(input, { target: { value: 'Build a gene flow.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    expect(await screen.findByRole('dialog', { name: 'Review this flow change' })).toBeInTheDocument()
    expect(screen.getByText('Add gene extraction after Initial Instructions.')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Proposed flow changes' })).toHaveTextContent('Initial instructions: Extract genes.')
    expect(screen.getByText('Technical details').closest('details')).not.toHaveAttribute('open')
    fireEvent.click(screen.getByText('Technical details'))
    expect(screen.getByText('flow_definition.nodes.node_0.data.task_instructions')).toBeInTheDocument()
    expect(screen.getByText('Old instructions')).toBeInTheDocument()
    expect(screen.getAllByText('Extract genes.')).not.toHaveLength(0)
    expect(onApplyFlowProposal).not.toHaveBeenCalled()

    if (decision === 'failed Apply' || decision === 'unavailable Apply') {
      fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }))
      const message = decision === 'failed Apply' ? /Your latest edits were preserved/ : /We could not confirm this change/
      await waitFor(() => expect(within(screen.getByRole('dialog')).getByText(message)).toBeVisible())
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
      expect(await screen.findByText(/Your current draft has been kept; nothing was saved/)).toBeVisible()
      expect(screen.queryByText(/editor draft was not changed/)).not.toBeInTheDocument()
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1)
      return
    }
    fireEvent.click(screen.getByRole('button', { name: decision }))
    if (decision === 'Cancel') {
      expect(onApplyFlowProposal).not.toHaveBeenCalled()
      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Review this flow change' })).not.toBeInTheDocument())
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1)
      return
    }
    await waitFor(() => expect(onApplyFlowProposal).toHaveBeenCalledWith(expect.objectContaining({
      contract_version: 'flow_authoring_proposal.v1',
    })))
    expect(await screen.findByText(/has not been saved/)).toBeInTheDocument()
  })

  it('renders the compact header with one context chip, a feedback menu, and a hide control', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    const onHide = vi.fn()

    render(
      <OpusChat
        context={{ active_tab: 'agents' }}
        selectedAgent={DISEASE_VALIDATOR}
        variant="panel"
        panelId="agent-studio-claude-panel"
        onHide={onHide}
        initialConversation={[
          { role: 'user', content: 'Hello', timestamp: '2026-04-22T00:00:01Z' },
          { role: 'assistant', content: 'Hi', timestamp: '2026-04-22T00:00:02Z' },
        ]}
      />
    )

    expect(screen.getByRole('heading', { name: 'AI Chat' })).toBeInTheDocument()
    expect(screen.queryByText('Chat with Claude')).not.toBeInTheDocument()
    expect(screen.queryByText('Contact Devs:')).not.toBeInTheDocument()
    expect(screen.getByText('Disease Validator')).toBeInTheDocument()

    const hideButton = screen.getByRole('button', { name: 'Hide AI Chat' })
    expect(hideButton).toHaveAttribute('aria-expanded', 'true')
    expect(hideButton).toHaveAttribute('aria-controls', 'agent-studio-claude-panel')
    fireEvent.click(hideButton)
    expect(onHide).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }))
    const aiAssisted = screen.getByRole('menuitem', { name: 'AI-assisted' })
    const manual = screen.getByRole('menuitem', { name: 'Manual' })
    expect(aiAssisted).not.toHaveAttribute('aria-disabled', 'true')
    expect(manual).not.toHaveAttribute('aria-disabled', 'true')

    fireEvent.click(manual)
    expect(await screen.findByRole('dialog', { name: 'Submit Prompt Suggestion' })).toBeInTheDocument()
  })

  it('prefers the restored-session label over the agent chip and disables feedback with no messages', () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    render(
      <OpusChat
        context={{ active_tab: 'agents', session_id: 'assistant-session-12345678' }}
        sourceSessionId="assistant-session-12345678"
        selectedAgent={DISEASE_VALIDATOR}
        variant="drawer"
        panelId="agent-studio-claude-drawer"
        onHide={vi.fn()}
      />
    )

    expect(screen.getByText('Loaded from durable chat assistan...')).toBeInTheDocument()
    expect(screen.queryByText('Disease Validator')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close AI Chat' })).toHaveAttribute(
      'aria-controls',
      'agent-studio-claude-drawer',
    )

    fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }))
    expect(screen.getByRole('menuitem', { name: 'AI-assisted' })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('menuitem', { name: 'Manual' })).toHaveAttribute('aria-disabled', 'true')
  })

  it('keeps streaming, tool calls, and the draft input alive while the shell hides and re-shows the chat', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    let releaseCompletion: () => void = () => {}
    const completionGate = new Promise<void>((resolve) => {
      releaseCompletion = resolve
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TOOL_USE', tool_name: 'get_prompt', tool_input: { agent_id: 'gene' } }
      yield { type: 'TOOL_RESULT', tool_name: 'get_prompt', result: { success: true } }
      yield { type: 'TEXT_DELTA', delta: 'Partial reply' }
      await completionGate
      yield { type: 'TEXT_DELTA', delta: ' completed' }
      yield { type: 'DONE' }
    })

    const onStreamingChange = vi.fn()

    function Shell() {
      const [hidden, setHidden] = useState(false)
      return (
        <>
          <button onClick={() => setHidden((current) => !current)}>toggle-shell</button>
          <div data-testid="shell-slot" style={{ visibility: hidden ? 'hidden' : 'visible' }}>
            <OpusChat
              context={{ active_tab: 'agents' }}
              onStreamingChange={onStreamingChange}
            />
          </div>
        </>
      )
    }

    render(<Shell />)

    const input = screen.getByPlaceholderText('Ask about prompts...')
    fireEvent.change(input, { target: { value: 'Keep this running while hidden.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    expect(await screen.findByText('Partial reply')).toBeInTheDocument()
    expect(onStreamingChange).toHaveBeenLastCalledWith(true)
    expect(screen.getByText('Tool Calls (1)')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Ask about prompts...'), {
      target: { value: 'Draft typed mid-stream' },
    })
    const chatBeforeToggle = screen.getByRole('heading', { name: 'AI Chat' })

    fireEvent.click(screen.getByText('toggle-shell'))
    fireEvent.click(screen.getByText('toggle-shell'))

    expect(screen.getByRole('heading', { name: 'AI Chat' })).toBe(chatBeforeToggle)
    expect(screen.getByText('Partial reply')).toBeInTheDocument()
    expect(screen.getByText('Tool Calls (1)')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Ask about prompts...')).toHaveValue('Draft typed mid-stream')

    releaseCompletion()

    expect(await screen.findByText('Partial reply completed')).toBeInTheDocument()
    expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(1)
    await waitFor(() => {
      expect(onStreamingChange).toHaveBeenLastCalledWith(false)
    })
  })

  it('announces hosted capability-search start and zero-result progress without exposing tool metadata', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    let releaseSearchResult: () => void = () => {}
    let releaseDone: () => void = () => {}
    const searchResultGate = new Promise<void>((resolve) => { releaseSearchResult = resolve })
    const doneGate = new Promise<void>((resolve) => { releaseDone = resolve })
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'PROVIDER_CONTEXT_PREFLIGHT' }
      yield { type: 'TOOL_SEARCH', status: 'searching' }
      await searchResultGate
      yield { type: 'TOOL_SEARCH_RESULT', status: 'loaded', loaded_tool_count: 0 }
      await doneGate
      yield { type: 'DONE' }
    })

    render(<OpusChat context={{ active_tab: 'flows' }} />)
    const input = screen.getByPlaceholderText('Ask about flows...')
    fireEvent.change(input, { target: { value: 'Check available capabilities.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    expect(await screen.findByRole('status')).toHaveTextContent('Finding the right tools for your request…')
    expect(screen.queryByText(/tool name/i)).not.toBeInTheDocument()

    releaseSearchResult()
    expect(await screen.findByRole('status')).toHaveTextContent('Working with the information already available…')

    releaseDone()
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
  })

  it.each([
    ['REFUSAL', 'Request declined: The model declined this request.'],
    ['INCOMPLETE', 'Response incomplete: The model stopped before completing this turn.'],
    ['CONTEXT_OVERFLOW', 'Conversation too long: The conversation exceeded the model context.'],
    ['ERROR', 'Error: The model service had a temporary problem.'],
  ] as const)('renders %s as a distinct terminal state without reporting a frontend crash', async (type, expected) => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })
    const errorSpy = vi.spyOn(logger, 'error')
    const messages = {
      REFUSAL: 'The model declined this request.',
      INCOMPLETE: 'The model stopped before completing this turn.',
      CONTEXT_OVERFLOW: 'The conversation exceeded the model context.',
      ERROR: 'The model service had a temporary problem.',
    }
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type, message: messages[type] }
    })

    render(<OpusChat context={{ active_tab: 'agents' }} />)
    const input = screen.getByPlaceholderText('Ask about prompts...')
    fireEvent.change(input, { target: { value: 'Please respond.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    expect(await screen.findByText(expected)).toBeInTheDocument()
    await waitFor(() => expect(input).not.toBeDisabled())
    expect(errorSpy).not.toHaveBeenCalled()
  })

  it('matches live tool results to the exact call ID rather than the latest tool', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })
    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield { type: 'TOOL_USE', tool_name: 'first_tool', tool_input: {}, call_id: 'call-1' }
      yield { type: 'TOOL_USE', tool_name: 'second_tool', tool_input: {}, call_id: 'call-2' }
      yield {
        type: 'TOOL_RESULT',
        tool_name: 'first_tool',
        result: { success: true },
        call_id: 'call-1',
      }
      yield { type: 'DONE' }
    })

    render(<OpusChat context={{ active_tab: 'agents' }} />)
    const input = screen.getByPlaceholderText('Ask about prompts...')
    fireEvent.change(input, { target: { value: 'Use both tools.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    fireEvent.click(await screen.findByText('Tool Calls (2)'))
    const firstToolRow = screen.getByText('first_tool').parentElement?.parentElement
    const secondToolRow = screen.getByText('second_tool').parentElement?.parentElement
    expect(firstToolRow).not.toBeNull()
    expect(secondToolRow).not.toBeNull()
    expect(within(firstToolRow as HTMLElement).getByText('✓ Success')).toBeInTheDocument()
    expect(within(secondToolRow as HTMLElement).queryByText('✓ Success')).not.toBeInTheDocument()
  })
})

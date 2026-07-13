import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'

import OpusChat, { resetSharedOpusChatStateForTests } from './OpusChat'
import type { ChatContext } from '@/types/promptExplorer'

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

  it('reattaches to an active Opus turn after unmount without starting a duplicate stream', async () => {
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

    fireEvent.click(screen.getByRole('button', {
      name: /Have Claude analyze your conversation and submit feedback to developers/i,
    }))

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

  it('applies an approved workshop prompt update proposed by Claude tool call', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield {
        type: 'TOOL_RESULT',
        tool_name: 'update_workshop_prompt_draft',
        result: {
          success: true,
          pending_user_approval: true,
          apply_mode: 'replace',
          proposed_prompt: 'You are an expert curator. Return concise extracted evidence with citations.',
          change_summary: 'Rewrote instructions for stronger evidence grounding.',
        },
      }
      yield { type: 'DONE' }
    })

    const onApplyWorkshopPromptUpdate = vi.fn()
    const context: ChatContext = {
      active_tab: 'agent_workshop',
    }

    render(
      <OpusChat
        context={context}
        onApplyWorkshopPromptUpdate={onApplyWorkshopPromptUpdate}
      />
    )

    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Please rewrite my prompt.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Apply Claude Prompt Update?' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Apply to Draft' }))

    await waitFor(() => {
      expect(onApplyWorkshopPromptUpdate).toHaveBeenCalledWith({
        prompt: 'You are an expert curator. Return concise extracted evidence with citations.',
        summary: 'Rewrote instructions for stronger evidence grounding.',
        apply_mode: 'replace',
        target_prompt: 'main',
      })
    })
  })

  it('supports targeted_edit workshop prompt proposals from Claude', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield {
        type: 'TOOL_RESULT',
        tool_name: 'update_workshop_prompt_draft',
        result: {
          success: true,
          pending_user_approval: true,
          apply_mode: 'targeted_edit',
          proposed_prompt: 'Prompt with small targeted improvements.',
          change_summary: 'Updated only the output-format section.',
        },
      }
      yield { type: 'DONE' }
    })

    const onApplyWorkshopPromptUpdate = vi.fn()
    const context: ChatContext = {
      active_tab: 'agent_workshop',
    }

    render(
      <OpusChat
        context={context}
        onApplyWorkshopPromptUpdate={onApplyWorkshopPromptUpdate}
      />
    )

    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Edit just one section.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Apply Claude Prompt Update?' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Apply to Draft' }))

    await waitFor(() => {
      expect(onApplyWorkshopPromptUpdate).toHaveBeenCalledWith({
        prompt: 'Prompt with small targeted improvements.',
        summary: 'Updated only the output-format section.',
        apply_mode: 'targeted_edit',
        target_prompt: 'main',
      })
    })
  })

  it('routes group-target workshop prompt proposals to the group apply path', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield {
        type: 'TOOL_RESULT',
        tool_name: 'update_workshop_prompt_draft',
        result: {
          success: true,
          pending_user_approval: true,
          apply_mode: 'replace',
          target_prompt: 'group',
          target_group_id: 'WB',
          proposed_prompt: 'WB-specific override prompt text.',
          change_summary: 'Tightened WB anatomy constraints.',
        },
      }
      yield { type: 'DONE' }
    })

    const onApplyWorkshopPromptUpdate = vi.fn()
    const context: ChatContext = {
      active_tab: 'agent_workshop',
      agent_workshop: {
        selected_group_id: 'WB',
        selected_group_prompt_draft: 'Old WB prompt',
      },
    }

    render(
      <OpusChat
        context={context}
        onApplyWorkshopPromptUpdate={onApplyWorkshopPromptUpdate}
      />
    )

    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Update only WB prompt.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Apply Claude Prompt Update?' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Apply to Draft' }))

    await waitFor(() => {
      expect(onApplyWorkshopPromptUpdate).toHaveBeenCalledWith({
        prompt: 'WB-specific override prompt text.',
        summary: 'Tightened WB anatomy constraints.',
        apply_mode: 'replace',
        target_prompt: 'group',
        target_group_id: 'WB',
      })
    })
  })

  it('auto-runs a post-apply review after workshop draft update is confirmed', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat
      .mockImplementationOnce(async function* () {
        yield {
          type: 'TOOL_RESULT',
          tool_name: 'update_workshop_prompt_draft',
          result: {
            success: true,
            pending_user_approval: true,
            apply_mode: 'targeted_edit',
            proposed_prompt: 'Line A\nLine B',
            change_summary: 'Added Line B.',
          },
        }
        yield { type: 'DONE' }
      })
      .mockImplementationOnce(async function* () {
        yield { type: 'TEXT_DELTA', delta: 'Post-apply review completed.' }
        yield { type: 'DONE' }
      })

    function Harness() {
      const [context, setContext] = useState<ChatContext>({
        active_tab: 'agent_workshop',
        agent_workshop: {
          prompt_draft: 'Line A',
        },
      })

      return (
        <OpusChat
          context={context}
          onApplyWorkshopPromptUpdate={(proposal) => {
            setContext({
              active_tab: 'agent_workshop',
              agent_workshop: {
                prompt_draft: proposal.prompt,
              },
            })
          }}
        />
      )
    }

    render(<Harness />)

    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Please add one line.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Apply Claude Prompt Update?' })).toBeInTheDocument()
    })
    expect(screen.getByText(/Proposed additions are highlighted in green/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Apply to Draft' }))

    await waitFor(() => {
      expect(serviceMocks.streamOpusChat).toHaveBeenCalledTimes(2)
    })
    const autoReviewMessages = serviceMocks.streamOpusChat.mock.calls[1][0]
    expect(autoReviewMessages[autoReviewMessages.length - 1].content).toContain(
      'Please run a post-apply review of my Agent Workshop draft'
    )
  })

  it('shows removed lines in red/strikethrough preview when proposal deletes content', async () => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
      writable: true,
    })

    serviceMocks.streamOpusChat.mockImplementation(async function* () {
      yield {
        type: 'TOOL_RESULT',
        tool_name: 'update_workshop_prompt_draft',
        result: {
          success: true,
          pending_user_approval: true,
          apply_mode: 'targeted_edit',
          proposed_prompt: 'Line A',
          change_summary: 'Removed Line B.',
        },
      }
      yield { type: 'DONE' }
    })

    const context: ChatContext = {
      active_tab: 'agent_workshop',
      agent_workshop: {
        prompt_draft: 'Line A\nLine B',
      },
    }

    render(<OpusChat context={context} onApplyWorkshopPromptUpdate={vi.fn()} />)

    const input = screen.getByPlaceholderText('Ask about your workshop draft...')
    fireEvent.change(input, { target: { value: 'Remove one line.' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Apply Claude Prompt Update?' })).toBeInTheDocument()
    })

    expect(screen.getByText(/Proposed removals are highlighted in red with strikethrough/)).toBeInTheDocument()
    expect(screen.getByText('Line B')).toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'

import OpusChat from './OpusChat'
import type { ChatContext } from '@/types/promptExplorer'

const serviceMocks = vi.hoisted(() => ({
  streamOpusChat: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)

describe('OpusChat', () => {
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

  it('routes MOD-target workshop prompt proposals to MOD apply path', async () => {
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
          target_prompt: 'mod',
          target_mod_id: 'WB',
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
        selected_mod_id: 'WB',
        selected_mod_prompt_draft: 'Old WB prompt',
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
        target_prompt: 'mod',
        target_mod_id: 'WB',
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
    expect(screen.getByText('Removed lines')).toBeInTheDocument()
    expect(screen.getByText('Line B')).toBeInTheDocument()
  })
})

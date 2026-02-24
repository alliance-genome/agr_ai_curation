import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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
      })
    })
  })
})

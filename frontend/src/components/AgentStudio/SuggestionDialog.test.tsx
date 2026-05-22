import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SuggestionDialog from './SuggestionDialog'
import { submitSuggestion } from '@/services/agentStudioService'

vi.mock('@/services/agentStudioService', () => ({
  submitSuggestion: vi.fn(),
}))

describe('SuggestionDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(submitSuggestion).mockResolvedValue({
      status: 'success',
      suggestion_id: 'suggestion-1',
      message: 'Suggestion submitted.',
    })
  })

  it('uses the modeless feedback surface and preserves manual draft fields while the page behind is used', async () => {
    const behindClick = vi.fn()
    const onSuccess = vi.fn()

    render(
      <>
        <button type="button" onClick={behindClick}>
          Inspect Agent Studio context
        </button>
        <SuggestionDialog
          open
          onClose={vi.fn()}
          onSuccess={onSuccess}
          onError={vi.fn()}
          context={{
            active_tab: 'agents',
            selected_agent_id: 'agent-1',
            selected_group_id: 'group-alpha',
            trace_id: 'trace-1',
          }}
          selectedAgent={{
            agent_id: 'agent-1',
            agent_name: 'Allele Agent',
            description: 'Reviews allele curation prompts.',
            base_prompt: 'Prompt',
            source_file: 'database',
            has_group_rules: false,
            group_rules: {},
            tools: [],
          }}
        />
      </>
    )

    const dialog = screen.getByRole('dialog', { name: 'Submit Prompt Suggestion' })
    expect(dialog).toHaveAttribute('aria-modal', 'false')
    expect(document.querySelector('.MuiBackdrop-root')).not.toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText(/brief 1-2 sentence summary/i), {
      target: { value: 'Clarify allele lookup behavior' },
    })
    fireEvent.change(screen.getByPlaceholderText(/explain why this change is needed/i), {
      target: { value: 'The current prompt is hard to compare against the trace.' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Inspect Agent Studio context' }))

    expect(behindClick).toHaveBeenCalledTimes(1)
    expect(screen.getByPlaceholderText(/brief 1-2 sentence summary/i)).toHaveValue('Clarify allele lookup behavior')

    fireEvent.click(screen.getByRole('button', { name: 'Submit Suggestion' }))

    await waitFor(() => {
      expect(submitSuggestion).toHaveBeenCalledWith({
        agent_id: 'agent-1',
        suggestion_type: 'improvement',
        summary: 'Clarify allele lookup behavior',
        detailed_reasoning: 'The current prompt is hard to compare against the trace.',
        proposed_change: undefined,
        group_id: 'group-alpha',
        trace_id: 'trace-1',
      })
    })
    expect(onSuccess).toHaveBeenCalledWith('suggestion-1')
  })
})

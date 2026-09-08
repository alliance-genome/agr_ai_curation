import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AgentPromptsTab, { formatCharCount } from './AgentPromptsTab'
import type { CombinedPromptResponse, PromptInfo, PromptLayerInfo } from '@/types/promptExplorer'

function layer(overrides: Partial<PromptLayerInfo>): PromptLayerInfo {
  return {
    id: 'x',
    kind: 'base_prompt',
    title: 'Layer',
    content: '',
    provenance: 'test',
    editable: false,
    locked: false,
    source_ref: '',
    hash: '',
    ...overrides,
  }
}

function buildAgent(overrides: Partial<PromptInfo> = {}): PromptInfo {
  return {
    agent_id: 'example_validator',
    agent_name: 'Example validator',
    description: 'Checks things.',
    base_prompt: 'Base prompt body',
    source_file: 'database',
    has_group_rules: true,
    group_rules: {
      group_a: { group_id: 'group_a', content: 'Group A rules', source_file: 'database' },
    },
    prompt_layers: [
      layer({ id: 'core', kind: 'core_static', title: 'Core prompt', content: 'Core contract text', provenance: 'backend_static', locked: true }),
      layer({ id: 'gen', kind: 'core_generated', title: 'Generated contract', content: 'Generated contract text', provenance: 'runtime', locked: true }),
      layer({ id: 'base', kind: 'base_prompt', title: 'Base prompt', content: 'Base prompt body', provenance: 'prompt_template:system', editable: true }),
    ],
    effective_prompt_hash: 'abcdef1234567890',
    tools: [],
    ...overrides,
  }
}

const noop = vi.fn()

describe('formatCharCount', () => {
  it('shows exact counts under a thousand and one decimal k above', () => {
    expect(formatCharCount(0)).toBe('0')
    expect(formatCharCount(999)).toBe('999')
    expect(formatCharCount(1000)).toBe('1k')
    expect(formatCharCount(1840)).toBe('1.8k')
    expect(formatCharCount(7720)).toBe('7.7k')
  })
})

describe('AgentPromptsTab', () => {
  it('opens on Effective with labeled layer boundaries and switches layers one at a time', () => {
    render(<AgentPromptsTab agent={buildAgent()} selectedGroupId={null} onGroupSelect={noop} combinedPrompt={null} loadingCombined={false} />)

    const picker = screen.getByRole('group', { name: 'Prompt layer' })
    const buttons = within(picker).getAllByRole('button')
    expect(buttons.map((button) => button.textContent)).toEqual([
      'Core18', 'Generated23', 'Base16', 'Group0', 'Override0', 'Effective61',
    ])
    expect(within(picker).getByRole('button', { name: /^Effective/ })).toHaveAttribute('aria-pressed', 'true')

    const pane = screen.getByTestId('prompt-reading-pane')
    expect(pane).toHaveTextContent('Core prompt · backend_static')
    expect(pane).toHaveTextContent('Core contract text')
    expect(pane).toHaveTextContent('Generated contract · runtime')
    expect(pane).toHaveTextContent('Base prompt body')
    expect(screen.getByRole('button', { name: 'Copy effective prompt' })).toBeInTheDocument()
    expect(screen.getByText('Effective hash', { exact: false })).toHaveTextContent('abcdef123456')

    fireEvent.click(within(picker).getByRole('button', { name: /^Core/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('Core contract text')
    expect(screen.getByTestId('prompt-reading-pane')).not.toHaveTextContent('Base prompt body')
    expect(screen.getByRole('button', { name: 'Copy core prompt' })).toBeInTheDocument()

    fireEvent.click(within(picker).getByRole('button', { name: /^Group/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('Select a group to view its rules.')
    expect(screen.getByRole('button', { name: 'Copy group rules' })).toBeDisabled()

    fireEvent.click(within(picker).getByRole('button', { name: /^Override/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('No main prompt override is applied.')
  })

  it('copies the visible layer only', () => {
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
    render(<AgentPromptsTab agent={buildAgent()} selectedGroupId={null} onGroupSelect={noop} combinedPrompt={null} loadingCombined={false} />)

    fireEvent.click(screen.getByRole('button', { name: /^Base/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Copy base prompt' }))
    expect(writeText).toHaveBeenCalledWith('Base prompt body')

    fireEvent.click(screen.getByRole('button', { name: /^Effective/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Copy effective prompt' }))
    expect(writeText).toHaveBeenLastCalledWith('Core contract text\n\nGenerated contract text\n\nBase prompt body')
    writeText.mockRestore()
  })

  it('uses the combined manifest for a selected group and highlights the group rules', () => {
    const combined: CombinedPromptResponse = {
      agent_id: 'example_validator',
      group_id: 'group_a',
      combined_prompt: 'Core contract text\n\nBase prompt body\n\nGroup A rules from manifest',
      effective_prompt_hash: 'fedcba0987654321',
      layer_manifest: {
        agent_id: 'example_validator',
        hash: 'fedcba0987654321',
        layers: [
          layer({ id: 'core', kind: 'core_static', title: 'Core prompt', content: 'Core contract text', provenance: 'backend_static' }),
          layer({ id: 'base', kind: 'base_prompt', title: 'Base prompt', content: 'Base prompt body', provenance: 'prompt_template:system' }),
          layer({ id: 'group', kind: 'group_rules', title: 'Group rules', content: 'Group A rules from manifest', provenance: 'prompt_template:group_rules' }),
        ],
      },
    }
    const onGroupSelect = vi.fn()
    render(<AgentPromptsTab agent={buildAgent()} selectedGroupId="group_a" onGroupSelect={onGroupSelect} combinedPrompt={combined} loadingCombined={false} />)

    const pane = screen.getByTestId('prompt-reading-pane')
    expect(pane).toHaveTextContent('Group rules · prompt_template:group_rules')
    const highlighted = pane.querySelector('[data-highlight="group-rules"]')
    expect(highlighted).toHaveTextContent('Group A rules from manifest')
    expect(screen.getByText('Effective hash', { exact: false })).toHaveTextContent('fedcba098765')

    fireEvent.click(screen.getByRole('button', { name: /^Group/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('Group A rules from manifest')

    const groupSelect = screen.getByRole('combobox', { name: 'Group' })
    expect(groupSelect).toHaveTextContent('group_a')
  })

  it('shows a loading pane while the combined prompt is fetched and falls back to layers on failure', () => {
    const { rerender } = render(
      <AgentPromptsTab agent={buildAgent()} selectedGroupId="group_a" onGroupSelect={noop} combinedPrompt={null} loadingCombined />
    )
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('Loading effective prompt')

    rerender(<AgentPromptsTab agent={buildAgent()} selectedGroupId="group_a" onGroupSelect={noop} combinedPrompt={null} loadingCombined={false} />)
    const pane = screen.getByTestId('prompt-reading-pane')
    expect(pane).toHaveTextContent('Core contract text')
    expect(pane).toHaveTextContent('Base prompt body')

    fireEvent.click(screen.getByRole('button', { name: /^Group/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('Group A rules')
  })

  it('keeps the layer error and overlay review alerts above the pane and excludes flagged text from Effective', () => {
    const agent = buildAgent({
      base_prompt: 'Curator note\n\nPlatform Runtime Contract copied fragment',
      custom_prompt_overlay_status: 'needs_review',
      custom_prompt_warning: 'Custom-agent prompt still contains locked/core prompt markers after safe cleanup.',
      prompt_layer_error: 'Layer manifest could not be built.',
    })
    render(<AgentPromptsTab agent={agent} selectedGroupId={null} onGroupSelect={noop} combinedPrompt={null} loadingCombined={false} />)

    expect(screen.getByText('Layer manifest could not be built.')).toBeInTheDocument()
    expect(screen.getByText('Custom-agent prompt still contains locked/core prompt markers after safe cleanup.')).toBeInTheDocument()

    const pane = screen.getByTestId('prompt-reading-pane')
    expect(pane).toHaveTextContent('Core contract text')
    expect(pane).not.toHaveTextContent('Platform Runtime Contract copied fragment')

    fireEvent.click(screen.getByRole('button', { name: /^Override/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('Platform Runtime Contract copied fragment')
  })

  it('hides the group select when the agent has no group rules', () => {
    render(
      <AgentPromptsTab
        agent={buildAgent({ has_group_rules: false, group_rules: {} })}
        selectedGroupId={null}
        onGroupSelect={noop}
        combinedPrompt={null}
        loadingCombined={false}
      />
    )
    expect(screen.queryByRole('combobox', { name: 'Group' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Group/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('This agent has no group rules.')
  })
})

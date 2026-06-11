import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentDetailsPanel from './AgentDetailsPanel'
import type { PromptInfo } from '@/types/promptExplorer'

const serviceMocks = vi.hoisted(() => ({
  fetchCombinedPrompt: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)
vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: {},
    refresh: vi.fn(),
    isLoading: false,
    error: null,
  }),
}))

function buildFlaggedAgent(): PromptInfo {
  return {
    agent_id: 'ca_11111111-2222-3333-4444-555555555555',
    agent_name: 'Flagged Gene Agent',
    description: 'Custom prompt variant',
    base_prompt: 'Curator authored note\n\nPlatform Runtime Contract copied fragment',
    source_file: 'custom_agent:11111111-2222-3333-4444-555555555555',
    has_group_rules: true,
    group_rules: {
      GROUP_A: {
        group_id: 'GROUP_A',
        content: 'Group A rules',
        source_file: 'database',
      },
    },
    prompt_layers: [
      {
        id: 'gene:core_static',
        kind: 'core_static',
        title: 'Core Prompt',
        content: 'Safe locked core contract',
        provenance: 'backend_static',
        editable: false,
        locked: true,
        source_ref: 'core',
        hash: 'hash-core',
      },
      {
        id: 'gene:base_prompt',
        kind: 'base_prompt',
        title: 'Base Prompt',
        content: 'Parent base prompt',
        provenance: 'prompt_template:system',
        editable: true,
        locked: false,
        source_ref: 'base',
        hash: 'hash-base',
      },
    ],
    custom_prompt_overlay_status: 'needs_review',
    custom_prompt_removed_layer_kinds: ['core_static'],
    custom_prompt_warning: 'Custom-agent prompt still contains locked/core prompt markers after safe cleanup.',
    tools: [],
  }
}

function buildCleanCustomAgent(): PromptInfo {
  return {
    ...buildFlaggedAgent(),
    base_prompt: 'Curator overlay guidance',
    prompt_layers: [
      ...(buildFlaggedAgent().prompt_layers || []),
      {
        id: 'gene:curator_overlay',
        kind: 'curator_overlay',
        title: 'Main Prompt Override',
        content: 'Curator overlay guidance',
        provenance: 'custom_agent',
        editable: true,
        locked: false,
        source_ref: 'custom_agent',
        hash: 'hash-overlay',
      },
    ],
    custom_prompt_overlay_status: 'clean',
    custom_prompt_removed_layer_kinds: [],
    custom_prompt_warning: undefined,
  }
}

describe('AgentDetailsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows a composed empty state when no agent is selected', () => {
    render(<AgentDetailsPanel agent={null} selectedGroupId={null} onGroupSelect={vi.fn()} />)
    expect(screen.getByText('Browse your agents')).toBeInTheDocument()
    expect(screen.getByText(/pick an agent on the left/i)).toBeInTheDocument()
  })

  it('marks flagged custom prompt text and excludes it from effective preview rendering', () => {
    render(
      <AgentDetailsPanel
        agent={buildFlaggedAgent()}
        selectedGroupId="GROUP_A"
        onGroupSelect={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Prompts' }))

    expect(serviceMocks.fetchCombinedPrompt).not.toHaveBeenCalled()
    expect(screen.getByText('Custom-agent prompt still contains locked/core prompt markers after safe cleanup.')).toBeInTheDocument()
    expect(screen.getByText(/Platform Runtime Contract copied fragment/)).toBeInTheDocument()

    const effectivePromptSection = screen.getByText('Effective Prompt Preview').closest('div')?.parentElement
    expect(effectivePromptSection).toHaveTextContent('Safe locked core contract')
    expect(effectivePromptSection).toHaveTextContent('Parent base prompt')
    expect(effectivePromptSection).not.toHaveTextContent('Platform Runtime Contract copied fragment')
  })

  it('keeps locked layers in the selected-group fallback for custom agents', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    serviceMocks.fetchCombinedPrompt.mockRejectedValue(new Error('combined preview unavailable'))

    render(
      <AgentDetailsPanel
        agent={buildCleanCustomAgent()}
        selectedGroupId="GROUP_A"
        onGroupSelect={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(serviceMocks.fetchCombinedPrompt).toHaveBeenCalledWith(
        'ca_11111111-2222-3333-4444-555555555555',
        'GROUP_A'
      )
    })

    fireEvent.click(screen.getByRole('tab', { name: 'Prompts' }))

    const effectivePromptSection = screen.getByText('Effective Prompt Preview').closest('div')?.parentElement
    expect(effectivePromptSection).toHaveTextContent('Safe locked core contract')
    expect(effectivePromptSection).toHaveTextContent('Parent base prompt')
    expect(effectivePromptSection).toHaveTextContent('Curator overlay guidance')

    consoleError.mockRestore()
  })
})

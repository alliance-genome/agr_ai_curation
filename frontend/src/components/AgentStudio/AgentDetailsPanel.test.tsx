import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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
      WB: {
        group_id: 'WB',
        content: 'WB group rules',
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
    custom_prompt_warning: 'Custom overlay still contains locked/core prompt markers after safe cleanup.',
    tools: [],
  }
}

describe('AgentDetailsPanel', () => {
  it('marks flagged custom overlays and excludes them from effective preview rendering', () => {
    render(
      <AgentDetailsPanel
        agent={buildFlaggedAgent()}
        selectedGroupId="WB"
        viewMode="combined"
        onGroupSelect={vi.fn()}
        onViewModeChange={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Prompts' }))

    expect(serviceMocks.fetchCombinedPrompt).not.toHaveBeenCalled()
    expect(screen.getByText('Custom overlay still contains locked/core prompt markers after safe cleanup.')).toBeInTheDocument()
    expect(screen.getByText(/Platform Runtime Contract copied fragment/)).toBeInTheDocument()

    const effectivePromptSection = screen.getByText('Effective Prompt Preview').closest('div')?.parentElement
    expect(effectivePromptSection).toHaveTextContent('Safe locked core contract')
    expect(effectivePromptSection).toHaveTextContent('Parent base prompt')
    expect(effectivePromptSection).not.toHaveTextContent('Platform Runtime Contract copied fragment')
  })
})

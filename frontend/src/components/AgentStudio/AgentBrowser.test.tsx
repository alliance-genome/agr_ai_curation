import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AgentBrowser from './AgentBrowser'
import type { PromptCatalog, PromptInfo } from '@/types/promptExplorer'

const metadataMocks = vi.hoisted(() => ({
  agents: {
    rgd_agent: { allowed_group_ids: ['RGD'] },
  } as Record<string, unknown>,
}))

vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: metadataMocks.agents,
    refresh: vi.fn(),
    isLoading: false,
    error: null,
  }),
}))

vi.mock('@/services/agentStudioService', () => ({
  fetchCombinedPrompt: vi.fn(),
}))

const buildAgent = (agentId: string, name: string): PromptInfo => ({
  agent_id: agentId,
  agent_name: name,
  description: `${name} description`,
  base_prompt: 'Prompt',
  source_file: 'database',
  has_group_rules: false,
  group_rules: {},
  tools: [],
  subcategory: 'Data Validation',
})

describe('AgentBrowser MOD restrictions', () => {
  it('shows a restriction badge for an authorized catalog entry and does not invent filtered entries', () => {
    const catalog: PromptCatalog = {
      categories: [{
        category: 'Validation',
        agents: [buildAgent('rgd_agent', 'RGD Agent')],
      }],
      total_agents: 1,
      available_groups: [],
      last_updated: '2026-08-27T00:00:00Z',
    }

    render(
      <AgentBrowser
        catalog={catalog}
        selectedAgentId="rgd_agent"
        selectedGroupId={null}
        onAgentSelect={vi.fn()}
        onGroupSelect={vi.fn()}
      />
    )

    expect(screen.getByLabelText('RGD Agent restricted to RGD')).toBeInTheDocument()
    expect(screen.getByText(/Available to MODs: RGD/)).toBeInTheDocument()
    expect(screen.queryByText('Unauthorized Agent')).not.toBeInTheDocument()
  })
})

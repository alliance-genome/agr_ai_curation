import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AgentBrowser from './AgentBrowser'
import type { PromptCatalog, PromptInfo } from '@/types/promptExplorer'

const metadataMocks = vi.hoisted(() => ({
  agents: {
    restricted_agent: { allowed_group_ids: ['GROUP_A'] },
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

describe('AgentBrowser group restrictions', () => {
  it('shows a restriction badge for an authorized catalog entry and does not invent filtered entries', () => {
    const catalog: PromptCatalog = {
      categories: [{
        category: 'Validation',
        agents: [buildAgent('restricted_agent', 'Restricted Agent')],
      }],
      total_agents: 1,
      available_groups: [],
      last_updated: '2026-08-27T00:00:00Z',
    }

    render(
      <AgentBrowser
        catalog={catalog}
        selectedAgentId="restricted_agent"
        selectedGroupId={null}
        onAgentSelect={vi.fn()}
        onGroupSelect={vi.fn()}
      />
    )

    expect(screen.getByLabelText('Restricted Agent restricted to GROUP_A')).toBeInTheDocument()
    expect(screen.getByText(/Available to groups: GROUP_A/)).toBeInTheDocument()
    expect(screen.queryByText('Unauthorized Agent')).not.toBeInTheDocument()
  })
})

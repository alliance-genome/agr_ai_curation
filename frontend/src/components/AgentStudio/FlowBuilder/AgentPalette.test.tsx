import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentPalette from './AgentPalette'

const serviceMocks = vi.hoisted(() => ({
  fetchPromptCatalog: vi.fn(),
}))
const metadataMocks = vi.hoisted(() => ({
  agents: {
    rgd_agent: { icon: 'R', allowed_group_ids: ['RGD'] },
  } as Record<string, unknown>,
  refresh: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => ({
  fetchPromptCatalog: serviceMocks.fetchPromptCatalog,
}))

vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: metadataMocks.agents,
    refresh: metadataMocks.refresh,
    isLoading: false,
    error: null,
  }),
}))

vi.mock('@/services/logger', () => ({
  default: { error: vi.fn() },
}))

describe('AgentPalette access-aware choices', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [{
        category: 'Validation',
        agents: [{
          agent_id: 'rgd_agent',
          agent_name: 'RGD Agent',
          description: 'Authorized restricted agent',
          base_prompt: '',
          source_file: 'database',
          has_group_rules: false,
          group_rules: {},
          tools: [],
          subcategory: 'Data Validation',
        }],
      }],
      total_agents: 1,
      available_groups: [],
      last_updated: '2026-08-27T00:00:00Z',
    })
  })

  it('renders only server-returned choices and marks authorized restricted agents', async () => {
    render(<AgentPalette />)

    expect(await screen.findByText('RGD Agent')).toBeInTheDocument()
    expect(screen.getByLabelText('RGD Agent restricted to RGD')).toBeInTheDocument()
    expect(screen.queryByText('Unauthorized Agent')).not.toBeInTheDocument()
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentPalette from './AgentPalette'

const serviceMocks = vi.hoisted(() => ({
  fetchPromptCatalog: vi.fn(),
}))
const metadataMocks = vi.hoisted(() => ({
  agents: {
    restricted_agent: { icon: 'R', allowed_group_ids: ['GROUP_A'] },
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
          agent_id: 'restricted_agent',
          agent_name: 'Restricted Agent',
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

    expect(await screen.findByText('Restricted Agent')).toBeInTheDocument()
    expect(screen.getByLabelText('Restricted Agent restricted to GROUP_A')).toBeInTheDocument()
    expect(screen.queryByText('Unauthorized Agent')).not.toBeInTheDocument()
  })

  it('includes the offered immutable revision when dragging a custom agent', async () => {
    serviceMocks.fetchPromptCatalog.mockResolvedValue({
      categories: [{ category: 'Custom', agents: [{
        agent_id: 'ca_fixture', agent_name: 'Pinned custom', description: 'Example',
        agent_revision_id: '11111111-2222-4333-8444-555555555555', tools: [],
      }] }],
    })
    render(<AgentPalette />)
    const label = await screen.findByText('Pinned custom')
    const setData = vi.fn()
    fireEvent.dragStart(label.closest('[draggable="true"]')!, { dataTransfer: { setData } })
    expect(setData).toHaveBeenCalledWith('application/reactflow', expect.any(String))
    expect(JSON.parse(setData.mock.calls[0][1])).toEqual(expect.objectContaining({
      agentId: 'ca_fixture', agentRevisionId: '11111111-2222-4333-8444-555555555555',
    }))
  })
})

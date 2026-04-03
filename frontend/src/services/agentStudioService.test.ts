import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  cloneAgentToWorkshop,
  createFlow,
  createCustomAgent,
  listCustomAgents,
  listToolIdeaRequests,
  setCustomAgentVisibility,
  submitToolIdeaRequest,
  updateFlow,
} from './agentStudioService'

const mockFetch = vi.fn()
global.fetch = mockFetch

describe('agentStudioService', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('listCustomAgents sends template_source query param when provided', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ custom_agents: [], total: 0 }),
    })

    await listCustomAgents('gene')

    expect(mockFetch).toHaveBeenCalledWith('/api/agent-studio/custom-agents?template_source=gene')
  })

  it('listCustomAgents omits query params when template_source is not provided', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ custom_agents: [], total: 0 }),
    })

    await listCustomAgents()

    expect(mockFetch).toHaveBeenCalledWith('/api/agent-studio/custom-agents')
  })

  it('createCustomAgent sends template_source and does not require parent_agent_id', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: '11111111-1111-1111-1111-111111111111',
        agent_id: 'ca_11111111-1111-1111-1111-111111111111',
        user_id: 1,
        template_source: 'gene',
        name: 'My Agent',
        description: null,
        custom_prompt: 'Prompt',
        group_prompt_overrides: {},
        icon: '🔧',
        include_group_rules: true,
        model_id: 'gpt-4o',
        model_temperature: 0.1,
        model_reasoning: null,
        tool_ids: [],
        output_schema_key: null,
        visibility: 'private',
        project_id: null,
        parent_prompt_hash: null,
        current_parent_prompt_hash: null,
        parent_prompt_stale: false,
        parent_exists: true,
        is_active: true,
        created_at: '2026-02-23T00:00:00Z',
        updated_at: '2026-02-23T00:00:00Z',
      }),
    })

    await createCustomAgent({
      template_source: 'gene',
      name: 'My Agent',
      custom_prompt: 'Prompt',
      model_id: 'gpt-4o',
    })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/agent-studio/custom-agents',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
    )
    const fetchOptions = mockFetch.mock.calls[0][1]
    const parsedBody = JSON.parse(fetchOptions.body as string)
    expect(parsedBody.template_source).toBe('gene')
    expect(parsedBody).not.toHaveProperty('parent_agent_id')
  })

  it('cloneAgentToWorkshop posts clone request payload', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: '11111111-1111-1111-1111-111111111111',
        agent_id: 'ca_11111111-1111-1111-1111-111111111111',
        user_id: 1,
        template_source: 'gene',
        name: 'Gene Copy',
        description: null,
        custom_prompt: 'Prompt',
        group_prompt_overrides: {},
        icon: '🔧',
        include_group_rules: true,
        model_id: 'gpt-4o',
        model_temperature: 0.1,
        model_reasoning: null,
        tool_ids: [],
        output_schema_key: null,
        visibility: 'private',
        project_id: null,
        parent_prompt_hash: null,
        current_parent_prompt_hash: null,
        parent_prompt_stale: false,
        parent_exists: true,
        is_active: true,
        created_at: '2026-02-23T00:00:00Z',
        updated_at: '2026-02-23T00:00:00Z',
      }),
    })

    await cloneAgentToWorkshop('ca_source', { name: 'Gene Copy' })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/agent-studio/agents/ca_source/clone',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
    )
    const fetchOptions = mockFetch.mock.calls[0][1]
    expect(JSON.parse(fetchOptions.body as string)).toEqual({ name: 'Gene Copy' })
  })

  it('setCustomAgentVisibility posts visibility payload', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: '11111111-1111-1111-1111-111111111111',
        agent_id: 'ca_11111111-1111-1111-1111-111111111111',
        user_id: 1,
        template_source: 'gene',
        name: 'My Agent',
        description: null,
        custom_prompt: 'Prompt',
        group_prompt_overrides: {},
        icon: '🔧',
        include_group_rules: true,
        model_id: 'gpt-4o',
        model_temperature: 0.1,
        model_reasoning: null,
        tool_ids: [],
        output_schema_key: null,
        visibility: 'project',
        project_id: '11111111-2222-3333-4444-555555555555',
        parent_prompt_hash: null,
        current_parent_prompt_hash: null,
        parent_prompt_stale: false,
        parent_exists: true,
        is_active: true,
        created_at: '2026-02-23T00:00:00Z',
        updated_at: '2026-02-23T00:00:00Z',
      }),
    })

    await setCustomAgentVisibility('ca_11111111-1111-1111-1111-111111111111', 'project')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/agent-studio/agents/ca_11111111-1111-1111-1111-111111111111/share',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
    )
    const fetchOptions = mockFetch.mock.calls[0][1]
    expect(JSON.parse(fetchOptions.body as string)).toEqual({ visibility: 'project' })
  })

  it('submitToolIdeaRequest posts idea payload', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        user_id: 1,
        project_id: '11111111-2222-3333-4444-555555555555',
        title: 'Need a new tool',
        description: 'This tool should enrich GO references',
        opus_conversation: [],
        status: 'submitted',
        developer_notes: null,
        resulting_tool_key: null,
        created_at: '2026-02-23T00:00:00Z',
        updated_at: '2026-02-23T00:00:00Z',
      }),
    })

    await submitToolIdeaRequest({
      title: 'Need a new tool',
      description: 'This tool should enrich GO references',
      opus_conversation: [],
    })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/agent-studio/tool-ideas',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
    )
    const fetchOptions = mockFetch.mock.calls[0][1]
    expect(JSON.parse(fetchOptions.body as string)).toEqual({
      title: 'Need a new tool',
      description: 'This tool should enrich GO references',
      opus_conversation: [],
    })
  })

  it('listToolIdeaRequests fetches current user requests', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        tool_ideas: [],
        total: 0,
      }),
    })

    await listToolIdeaRequests()

    expect(mockFetch).toHaveBeenCalledWith('/api/agent-studio/tool-ideas')
  })

  it('createFlow returns the created flow object for immediate UI updates', async () => {
    const createdFlow = {
      id: 'flow-123',
      user_id: 1,
      name: 'Fresh Flow',
      description: 'Saved from builder',
      execution_count: 0,
      last_executed_at: null,
      created_at: '2026-04-03T00:00:00Z',
      updated_at: '2026-04-03T00:00:00Z',
      flow_definition: {
        version: '1.0' as const,
        entry_node_id: 'node_0',
        nodes: [],
        edges: [],
      },
    }

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => createdFlow,
    })

    const result = await createFlow({
      name: 'Fresh Flow',
      description: 'Saved from builder',
      flow_definition: createdFlow.flow_definition,
    })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/flows',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
    )
    expect(result).toEqual(createdFlow)
  })

  it('updateFlow returns the updated flow object after saving changes', async () => {
    const updatedFlow = {
      id: 'flow-123',
      user_id: 1,
      name: 'Updated Flow',
      description: 'Updated from builder',
      execution_count: 2,
      last_executed_at: null,
      created_at: '2026-04-03T00:00:00Z',
      updated_at: '2026-04-03T01:00:00Z',
      flow_definition: {
        version: '1.0' as const,
        entry_node_id: 'node_0',
        nodes: [],
        edges: [],
      },
    }

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => updatedFlow,
    })

    const result = await updateFlow('flow-123', {
      name: 'Updated Flow',
      description: 'Updated from builder',
      flow_definition: updatedFlow.flow_definition,
    })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/flows/flow-123',
      expect.objectContaining({
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
      })
    )
    expect(result).toEqual(updatedFlow)
  })
})

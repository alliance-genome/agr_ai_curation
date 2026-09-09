import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  cloneAgentToWorkshop,
  createAgentStudioSession,
  createFlow,
  createCustomAgent,
  fetchAgentTemplates,
  fetchAgentStudioHistoryList,
  fetchAgentStudioSessionDetail,
  listFlows,
  listCustomAgents,
  listToolIdeaRequests,
  setCustomAgentVisibility,
  submitToolIdeaRequest,
  updateCustomAgent,
  updateFlow,
} from './agentStudioService'

const mockFetch = vi.fn()
global.fetch = mockFetch

describe('agentStudioService', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns canonical group options with available workshop templates', async () => {
    const responseBody = {
      templates: [{
        agent_id: 'gene',
        name: 'Gene Specialist',
        icon: 'G',
        model_id: 'gpt-5.6-terra',
        tool_ids: [],
        allowed_group_ids: ['GROUP_A'],
      }],
      group_options: [{ group_id: 'GROUP_A', name: 'Group A' }],
    }
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => responseBody })

    await expect(fetchAgentTemplates()).resolves.toEqual(responseBody)
    expect(mockFetch).toHaveBeenCalledWith('/api/agent-studio/agents/templates')
  })

  it('createAgentStudioSession posts the agent_studio chat kind to the shared session endpoint', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        session_id: 'agent-studio-session-123',
        created_at: '2026-04-23T00:00:00Z',
        updated_at: '2026-04-23T00:00:00Z',
      }),
    })

    const result = await createAgentStudioSession()

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/chat/session',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_kind: 'agent_studio' }),
      }),
    )
    expect(result.session_id).toBe('agent-studio-session-123')
  })

  it('fetchAgentStudioHistoryList scopes the shared history list to agent_studio', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          chat_kind: 'agent_studio',
          total_sessions: 0,
          limit: 20,
          query: null,
          document_id: null,
          next_cursor: null,
          sessions: [],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )

    await fetchAgentStudioHistoryList({ query: ' prompt tuning ' })

    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toBe('/api/chat/history?chat_kind=agent_studio&query=prompt+tuning')
    expect(init?.credentials).toBe('include')
  })

  it('fetchAgentStudioSessionDetail scopes transcript detail reads to agent_studio', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          session: {
            session_id: 'agent-studio-session-123',
            chat_kind: 'agent_studio',
            created_at: '2026-04-23T00:00:00Z',
            updated_at: '2026-04-23T00:00:00Z',
            recent_activity_at: '2026-04-23T00:00:00Z',
          },
          active_document: null,
          messages: [],
          message_limit: 50,
          next_message_cursor: null,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )

    await fetchAgentStudioSessionDetail({
      sessionId: 'agent-studio-session-123',
      messageLimit: 50,
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toBe(
      '/api/chat/history/agent-studio-session-123?chat_kind=agent_studio&message_limit=50',
    )
    expect(init?.credentials).toBe('include')
  })

  it('listCustomAgents sends template_source query param when provided', async () => {
    const responseBody = {
      custom_agents: [{ inherited_allowed_group_ids: ['GROUP_A'] }],
      total: 1,
    }
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => responseBody,
    })

    await expect(listCustomAgents('gene')).resolves.toEqual(responseBody)

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
      allowed_group_ids: ['GROUP_A'],
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
    expect(parsedBody.allowed_group_ids).toEqual(['GROUP_A'])
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
        is_active: true,
        created_at: '2026-02-23T00:00:00Z',
        updated_at: '2026-02-23T00:00:00Z',
      }),
    })

    await cloneAgentToWorkshop('ca_source', { name: 'Gene Copy', allowed_group_ids: ['GROUP_A'] })

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/agent-studio/agents/ca_source/clone',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
    )
    const fetchOptions = mockFetch.mock.calls[0][1]
    expect(JSON.parse(fetchOptions.body as string)).toEqual({ name: 'Gene Copy', allowed_group_ids: ['GROUP_A'] })
  })

  it('updateCustomAgent sends allowed_group_ids', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({}) })

    await updateCustomAgent('custom-id', { allowed_group_ids: ['GROUP_A'] })

    const fetchOptions = mockFetch.mock.calls[0][1]
    expect(JSON.parse(fetchOptions.body as string)).toEqual({ allowed_group_ids: ['GROUP_A'] })
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

  it('listFlows uses the shared default page size', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        flows: [],
        total: 0,
        page: 1,
        page_size: 50,
      }),
    })

    await listFlows()

    expect(mockFetch).toHaveBeenCalledWith('/api/flows?page=1&page_size=50', {
      credentials: 'include',
    })
  })

  it('listFlows maps unauthorized responses to the shared login message', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
    })

    await expect(listFlows()).rejects.toThrow('Please log in to view your flows')
  })

  it('listFlows maps transport failures to a shared connection message', async () => {
    mockFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    await expect(listFlows()).rejects.toThrow('Failed to connect to server')
  })

  it('listFlows preserves unexpected response parsing errors', async () => {
    const parseError = new SyntaxError('Unexpected token < in JSON at position 0')

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => {
        throw parseError
      },
    })

    await expect(listFlows()).rejects.toBe(parseError)
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
        version: '1.1' as const,
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
        version: '1.1' as const,
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

describe('flow sharing contracts', () => {
  beforeEach(() => mockFetch.mockReset())

  it('posts visibility and returns viewer metadata', async () => {
    const { shareFlow } = await import('./agentStudioService')
    const shared = { id: 'owned', user_id: 7, is_owner: true, visibility: 'project', project_id: 'project-a', shared_at: '2026-09-09' }
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify(shared)))
    await expect(shareFlow('owned', 'project')).resolves.toEqual(shared)
    expect(mockFetch).toHaveBeenCalledWith('/api/flows/owned/share', expect.objectContaining({ method: 'POST', body: JSON.stringify({ visibility: 'project' }) }))
  })

  it('clones with an optional name and preserves private owner metadata', async () => {
    const { cloneFlow } = await import('./agentStudioService')
    const copy = { id: 'copy', user_id: 8, is_owner: true, visibility: 'private', project_id: null, shared_at: null, execution_count: 0 }
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify(copy)))
    await expect(cloneFlow('shared', 'My copy')).resolves.toEqual(copy)
    expect(mockFetch).toHaveBeenCalledWith('/api/flows/shared/clone', expect.objectContaining({ method: 'POST', body: JSON.stringify({ name: 'My copy' }) }))
  })

  it('surfaces authorization and unavailable-agent errors without changing policy', async () => {
    const { cloneFlow, getFlow, shareFlow } = await import('./agentStudioService')
    for (const request of [() => cloneFlow('shared'), () => getFlow('shared'), () => shareFlow('shared', 'private')]) {
      mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Referenced agent is unavailable to your group' }), { status: 403 }))
      await expect(request()).rejects.toThrow('Referenced agent is unavailable to your group')
    }
  })

  it('includes shared flows on later browse pages', async () => {
    const { listAllFlows } = await import('./agentStudioService')
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({ flows: [{ id: 'owned' }], total: 2, page: 1, page_size: 1 })))
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({ flows: [{ id: 'shared', is_owner: false }], total: 2, page: 2, page_size: 1 })))
    expect((await listAllFlows()).flows.map((flow) => flow.id)).toEqual(['owned', 'shared'])
    expect(mockFetch).toHaveBeenLastCalledWith('/api/flows?page=2&page_size=1', { credentials: 'include' })
  })
})

describe('shared Workshop contracts', () => {
  beforeEach(() => mockFetch.mockReset())
  it('requests visible scope for discovery while preserving template filtering', async () => {
    const body = { custom_agents: [{ id: 'shared', user_id: 2, visibility: 'project', project_id: 'team' }], total: 1 }
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => body })
    await expect(listCustomAgents('gene', 'visible')).resolves.toEqual(body)
    expect(mockFetch).toHaveBeenCalledWith('/api/agent-studio/custom-agents?scope=visible&template_source=gene')
  })

  it('preserves owner records and teammate Tool Idea summaries without adding private fields', async () => {
    const summary = { id: 'shared', user_id: 2, project_id: 'team', title: 'Lookup', description: 'Batch lookup', status: 'completed', created_at: '2026-09-09', updated_at: '2026-09-09' }
    const owner = { ...summary, id: 'owned', user_id: 1, opus_conversation: [], developer_notes: null, resulting_tool_key: 'lookup' }
    const body = { tool_ideas: [owner, summary], total: 2 }
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => body })
    const result = await listToolIdeaRequests()
    expect(result).toEqual(body)
    expect(result.tool_ideas[1]).not.toHaveProperty('opus_conversation')
    expect(result.tool_ideas[1]).not.toHaveProperty('developer_notes')
    expect(result.tool_ideas[1]).not.toHaveProperty('resulting_tool_key')
  })
  it.each([403, 404])('surfaces denied or inaccessible clone responses (%s)', async (status) => {
    mockFetch.mockResolvedValueOnce({ ok: false, status })
    await expect(cloneAgentToWorkshop('ca_inaccessible')).rejects.toThrow(`Failed to clone agent: ${status}`)
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })

})

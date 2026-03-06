import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, beforeEach, expect, it, vi } from 'vitest'

import PromptWorkshop from './PromptWorkshop'
import type { PromptCatalog, CustomAgent, ModelOption, ToolLibraryItem, AgentTemplate } from '@/types/promptExplorer'

const serviceMocks = vi.hoisted(() => ({
  createCustomAgent: vi.fn(),
  deleteCustomAgent: vi.fn(),
  fetchAgentTemplates: vi.fn(),
  fetchModelOptions: vi.fn(),
  fetchToolLibrary: vi.fn(),
  listToolIdeaRequests: vi.fn(),
  listCustomAgentVersions: vi.fn(),
  listCustomAgents: vi.fn(),
  revertCustomAgentVersion: vi.fn(),
  setCustomAgentVisibility: vi.fn(),
  submitToolIdeaRequest: vi.fn(),
  updateCustomAgent: vi.fn(),
}))

const metadataMocks = vi.hoisted(() => ({
  refresh: vi.fn(),
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)
vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: {},
    refresh: metadataMocks.refresh,
    isLoading: false,
    error: null,
  }),
}))

function buildCatalog(): PromptCatalog {
  return {
    categories: [
      {
        category: 'Validation',
        agents: [
          {
            agent_id: 'gene',
            agent_name: 'Gene Specialist',
            description: 'Gene validation',
            base_prompt: 'System base prompt',
            source_file: 'database',
            has_mod_rules: false,
            mod_rules: {},
            tools: ['agr_curation_query'],
          },
        ],
      },
    ],
    total_agents: 1,
    available_mods: [],
    last_updated: '2026-02-23T00:00:00Z',
  }
}

function buildCatalogWithModRule(): PromptCatalog {
  return {
    categories: [
      {
        category: 'Validation',
        agents: [
          {
            agent_id: 'gene',
            agent_name: 'Gene Specialist',
            description: 'Gene validation',
            base_prompt: 'System base prompt',
            source_file: 'database',
            has_mod_rules: true,
            mod_rules: {
              WB: {
                mod_id: 'WB',
                content: 'WB template prompt',
                source_file: 'database',
              },
            },
            tools: ['agr_curation_query'],
          },
        ],
      },
    ],
    total_agents: 1,
    available_mods: ['WB'],
    last_updated: '2026-02-23T00:00:00Z',
  }
}

function buildCustomAgent(overrides: Partial<CustomAgent> = {}): CustomAgent {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    agent_id: 'ca_11111111-1111-1111-1111-111111111111',
    user_id: 1,
    template_source: 'gene',
    name: 'My Agent',
    description: 'desc',
    custom_prompt: 'Prompt',
    mod_prompt_overrides: {},
    icon: '🔧',
    include_mod_rules: true,
    model_id: 'gpt-4o',
    model_temperature: 0.1,
    model_reasoning: undefined,
    tool_ids: [],
    output_schema_key: undefined,
    visibility: 'private',
    project_id: undefined,
    parent_prompt_hash: undefined,
    current_parent_prompt_hash: undefined,
    parent_prompt_stale: false,
    parent_exists: true,
    is_active: true,
    created_at: '2026-02-23T00:00:00Z',
    updated_at: '2026-02-23T00:00:00Z',
    ...overrides,
  }
}

describe('PromptWorkshop', () => {
  const modelOptions: ModelOption[] = [
    {
      model_id: 'gpt-4o',
      name: 'GPT-4o',
      provider: 'openai',
      description: 'default',
      guidance: 'General model',
      default: true,
      supports_reasoning: false,
      supports_temperature: true,
      reasoning_options: [],
      default_reasoning: undefined,
      reasoning_descriptions: {},
      recommended_for: [],
      avoid_for: [],
    },
    {
      model_id: 'gpt-5.4',
      name: 'GPT-5.4',
      provider: 'openai',
      description: 'reasoning model',
      guidance: 'Use medium by default',
      default: false,
      supports_reasoning: true,
      supports_temperature: false,
      reasoning_options: ['low', 'medium', 'high'],
      default_reasoning: 'medium',
      reasoning_descriptions: {
        low: 'Fast',
        medium: 'Balanced',
        high: 'Slow',
      },
      recommended_for: ['Complex work'],
      avoid_for: ['Simple lookups'],
    },
  ]

  const toolLibrary: ToolLibraryItem[] = [
    {
      tool_key: 'search_document',
      display_name: 'Search Document',
      description: 'Search document sections',
      category: 'Document',
      curator_visible: true,
      allow_attach: true,
      allow_execute: true,
      config: {},
    },
    {
      tool_key: 'admin_only_tool',
      display_name: 'Admin Tool',
      description: 'Restricted',
      category: 'Admin',
      curator_visible: true,
      allow_attach: false,
      allow_execute: false,
      config: {},
    },
    {
      tool_key: 'chebi_lookup',
      display_name: 'ChEBI Lookup',
      description: 'Chemicals',
      category: 'External API',
      curator_visible: true,
      allow_attach: true,
      allow_execute: true,
      config: {},
    },
  ]

  const templates: AgentTemplate[] = [
    {
      agent_id: 'gene',
      name: 'Gene Specialist',
      description: 'Gene validation',
      icon: '🧬',
      category: 'Validation',
      model_id: 'gpt-4o',
      tool_ids: ['search_document'],
      output_schema_key: undefined,
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()

    metadataMocks.refresh.mockResolvedValue(undefined)
    serviceMocks.fetchModelOptions.mockResolvedValue(modelOptions)
    serviceMocks.fetchToolLibrary.mockResolvedValue(toolLibrary)
    serviceMocks.fetchAgentTemplates.mockResolvedValue(templates)
    serviceMocks.listToolIdeaRequests.mockResolvedValue({ tool_ideas: [], total: 0 })
    serviceMocks.listCustomAgentVersions.mockResolvedValue([])
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [], total: 0 })
    serviceMocks.createCustomAgent.mockResolvedValue(buildCustomAgent())
    serviceMocks.setCustomAgentVisibility.mockResolvedValue(buildCustomAgent({ visibility: 'project' }))
    serviceMocks.submitToolIdeaRequest.mockResolvedValue({
      id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
      user_id: 1,
      project_id: '11111111-2222-3333-4444-555555555555',
      title: 'Need a new tool',
      description: 'Description',
      opus_conversation: [],
      status: 'submitted',
      developer_notes: undefined,
      resulting_tool_key: undefined,
      created_at: '2026-02-23T00:00:00Z',
      updated_at: '2026-02-23T00:00:00Z',
    })
  })

  it('saves new agents with template_source payload (no parent_agent_id)', async () => {
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [], total: 0 })
      .mockResolvedValueOnce({ custom_agents: [buildCustomAgent()], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchAgentTemplates).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByText('File'))
    fireEvent.click(await screen.findByText('Save New Agent'))

    await waitFor(() => {
      expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1)
    })

    const payload = serviceMocks.createCustomAgent.mock.calls[0][0]
    expect(payload.template_source).toBe('gene')
    expect(payload.model_id).toBe('gpt-4o')
    expect(payload).not.toHaveProperty('parent_agent_id')
  }, 15000)

  it('disables non-attachable tools in tool library modal', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchToolLibrary).toHaveBeenCalled()
    })

    // Expand the Tools accordion first
    fireEvent.click(screen.getByRole('button', { name: /Tools/ }))

    fireEvent.click(await screen.findByRole('button', { name: 'Manage Tools' }))

    const restrictedText = await screen.findByText(/Not attachable by policy/)
    expect(restrictedText).toBeInTheDocument()

    const adminLabel = await screen.findByText('Admin Tool')
    const adminRowButton = adminLabel.closest('.MuiListItemButton-root')
    expect(adminRowButton).toHaveClass('Mui-disabled')
  }, 15000)

  it('filters tool library by selected category', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchToolLibrary).toHaveBeenCalled()
    })

    // Expand the Tools accordion first
    fireEvent.click(screen.getByRole('button', { name: /Tools/ }))

    fireEvent.click(await screen.findByRole('button', { name: 'Manage Tools' }))

    const dialog = await screen.findByRole('dialog', { name: 'Tool Library' })
    fireEvent.mouseDown(within(dialog).getByText('All categories'))
    fireEvent.click(await screen.findByRole('option', { name: 'External API' }))

    await waitFor(() => {
      expect(within(dialog).getByText('ChEBI Lookup')).toBeInTheDocument()
    })
    expect(within(dialog).queryByText('Search Document')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('Admin Tool')).not.toBeInTheDocument()
  }, 15000)

  it('shares newly created agents when visibility is set to project', async () => {
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [], total: 0 })
      .mockResolvedValueOnce({ custom_agents: [buildCustomAgent({ visibility: 'project' })], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchAgentTemplates).toHaveBeenCalled()
    })

    const visibilityLabel = screen
      .getAllByText('Visibility')
      .find((node) => node.tagName.toLowerCase() === 'label')
    expect(visibilityLabel).toBeTruthy()
    const visibilityControl = visibilityLabel!.closest('.MuiFormControl-root') as HTMLElement | null
    expect(visibilityControl).toBeTruthy()
    fireEvent.mouseDown(within(visibilityControl!).getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'Shared with Project' }))

    fireEvent.click(screen.getByText('File'))
    fireEvent.click(await screen.findByText('Save New Agent'))

    await waitFor(() => {
      expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(serviceMocks.setCustomAgentVisibility).toHaveBeenCalledTimes(1)
    })

    expect(serviceMocks.setCustomAgentVisibility).toHaveBeenCalledWith(
      'ca_11111111-1111-1111-1111-111111111111',
      'project'
    )
  })

  it('submits tool idea requests from the workshop dialog', async () => {
    const opusConversation = [
      { role: 'user' as const, content: 'I need a GO enrichment helper', timestamp: '2026-02-23T01:00:00Z' },
      { role: 'assistant' as const, content: 'What should the output look like?', timestamp: '2026-02-23T01:00:05Z' },
    ]

    render(<PromptWorkshop catalog={buildCatalog()} opusConversation={opusConversation} />)

    await waitFor(() => {
      expect(serviceMocks.fetchToolLibrary).toHaveBeenCalled()
    })

    // Expand the Tools accordion first
    fireEvent.click(screen.getByRole('button', { name: /Tools/ }))

    fireEvent.click(await screen.findByRole('button', { name: 'Send to Developers' }))
    const dialog = await screen.findByRole('dialog', { name: 'Submit Tool Request' })
    fireEvent.change(within(dialog).getByLabelText('Title'), {
      target: { value: 'Need GO relationship enrichment tool' },
    })
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: 'Add a tool that returns expanded GO relationships for a term.' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Submit' }))

    await waitFor(() => {
      expect(serviceMocks.submitToolIdeaRequest).toHaveBeenCalledTimes(1)
    })

    expect(serviceMocks.submitToolIdeaRequest).toHaveBeenCalledWith({
      title: 'Need GO relationship enrichment tool',
      description: 'Add a tool that returns expanded GO relationships for a term.',
      opus_conversation: opusConversation,
    })
  })

  it('opens the provided initial custom agent id for editing', async () => {
    const existing = buildCustomAgent({ name: 'Cloned Agent' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.updateCustomAgent.mockResolvedValue(existing)

    render(
      <PromptWorkshop
        catalog={buildCatalog()}
        initialCustomAgentId={existing.id}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/Editing: Cloned Agent/)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('File'))
    fireEvent.click(await screen.findByText('Save Agent'))

    await waitFor(() => {
      expect(serviceMocks.updateCustomAgent).toHaveBeenCalledTimes(1)
    })
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
  })

  it('blocks saving an existing agent when all previously attached tools are removed', async () => {
    const existing = buildCustomAgent({
      name: 'Tooled Agent',
      tool_ids: ['search_document'],
    })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.updateCustomAgent.mockResolvedValue(existing)

    render(
      <PromptWorkshop
        catalog={buildCatalog()}
        initialCustomAgentId={existing.id}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/Editing: Tooled Agent/)).toBeInTheDocument()
    })

    // Ensure tools controls are visible before opening the library modal
    fireEvent.click(screen.getByRole('button', { name: /Tools/ }))

    fireEvent.click(await screen.findByRole('button', { name: 'Manage Tools' }))
    const toolDialog = await screen.findByRole('dialog', { name: 'Tool Library' })
    fireEvent.click(within(toolDialog).getByText('Search Document'))
    fireEvent.click(within(toolDialog).getByRole('button', { name: 'Done' }))

    fireEvent.click(screen.getByText('File'))
    fireEvent.click(await screen.findByText('Save Agent'))

    await waitFor(() => {
      expect(
        screen.getByText(/Cannot save this agent with no tools selected/)
      ).toBeInTheDocument()
    })
    expect(serviceMocks.updateCustomAgent).not.toHaveBeenCalled()
  }, 15000)

  it('saves a copy via Save Agent As without updating the original agent', async () => {
    const existing = buildCustomAgent({ name: 'Original Agent' })
    const copied = buildCustomAgent({
      id: '22222222-2222-2222-2222-222222222222',
      agent_id: 'ca_22222222-2222-2222-2222-222222222222',
      name: 'Original Agent (Copy)',
    })

    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [existing], total: 1 })
      .mockResolvedValueOnce({ custom_agents: [existing, copied], total: 2 })
    serviceMocks.createCustomAgent.mockResolvedValue(copied)

    render(
      <PromptWorkshop
        catalog={buildCatalog()}
        initialCustomAgentId={existing.id}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/Editing: Original Agent/)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('File'))
    fireEvent.click(await screen.findByText('Save Agent As...'))

    const dialog = await screen.findByRole('dialog', { name: 'Save Agent As' })
    fireEvent.change(within(dialog).getByLabelText('Agent Name'), {
      target: { value: 'Original Agent (Copy)' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save As' }))

    await waitFor(() => {
      expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1)
    })
    expect(serviceMocks.updateCustomAgent).not.toHaveBeenCalled()
  })

  it('refreshes once to resolve a cloned initial custom agent id created after initial load', async () => {
    const existing = buildCustomAgent({ id: 'aaaaaaaa-1111-1111-1111-111111111111', name: 'Existing Agent' })
    const cloned = buildCustomAgent({ id: 'bbbbbbbb-2222-2222-2222-222222222222', name: 'Cloned Agent' })
    serviceMocks.fetchAgentTemplates.mockResolvedValue([])
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [existing], total: 1 })
      .mockResolvedValueOnce({ custom_agents: [existing, cloned], total: 2 })

    render(
      <PromptWorkshop
        catalog={buildCatalog()}
        initialCustomAgentId={cloned.id}
      />
    )

    await waitFor(() => {
      expect(serviceMocks.listCustomAgents).toHaveBeenCalledTimes(2)
    })

    await waitFor(() => {
      expect(screen.getByText(/Editing: Cloned Agent/)).toBeInTheDocument()
    })
  })

  it('does not auto-select an unrelated custom agent when no template-aligned agent exists', async () => {
    const unrelated = buildCustomAgent({ template_source: 'disease', name: 'Disease Agent' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [unrelated], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchAgentTemplates).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByText('File'))
    expect(await screen.findByText('Save New Agent')).toBeInTheDocument()
    expect(screen.queryByText('Save Agent')).not.toBeInTheDocument()
  })

  it('applies incoming prompt updates from Opus approval into the workshop draft', async () => {
    const { rerender } = render(<PromptWorkshop catalog={buildCatalog()} incomingPromptUpdate={null} />)

    await waitFor(() => {
      expect(serviceMocks.fetchAgentTemplates).toHaveBeenCalled()
    })

    rerender(
      <PromptWorkshop
        catalog={buildCatalog()}
        incomingPromptUpdate={{
          request_id: 1,
          prompt: 'Updated prompt from Claude',
          summary: 'Reworked structure and tightened extraction constraints.',
          apply_mode: 'targeted_edit',
        }}
      />
    )

    fireEvent.click(await screen.findByText('Main Prompt'))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Enter the system prompt for this agent...')).toHaveValue(
        'Updated prompt from Claude'
      )
    })
    expect(screen.getByText('Applied Claude update: Reworked structure and tightened extraction constraints.')).toBeInTheDocument()
  })

  it('saves selected reasoning for reasoning-capable models', async () => {
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [], total: 0 })
      .mockResolvedValueOnce({ custom_agents: [buildCustomAgent({ model_id: 'gpt-5.4', model_reasoning: 'high' })], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)

    await waitFor(() => {
      expect(serviceMocks.fetchModelOptions).toHaveBeenCalled()
    })

    const modelLabel = screen
      .getAllByText('Model')
      .find((node) => node.tagName.toLowerCase() === 'label')
    expect(modelLabel).toBeTruthy()
    const modelControl = modelLabel!.closest('.MuiFormControl-root') as HTMLElement | null
    expect(modelControl).toBeTruthy()
    fireEvent.mouseDown(within(modelControl!).getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'GPT-5.4' }))

    const [reasoningLabel] = await screen.findAllByText('Reasoning', { selector: 'label' })
    const reasoningControl = reasoningLabel.closest('.MuiFormControl-root') as HTMLElement | null
    expect(reasoningControl).toBeTruthy()
    fireEvent.mouseDown(within(reasoningControl!).getByRole('combobox'))
    fireEvent.click(await screen.findByRole('option', { name: 'High' }))

    fireEvent.click(screen.getByText('File'))
    fireEvent.click(await screen.findByText('Save New Agent'))

    await waitFor(() => {
      expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1)
    })
    expect(serviceMocks.createCustomAgent.mock.calls[0][0].model_reasoning).toBe('high')
  })

  it('opens a model-selection guidance request with Claude', async () => {
    const onVerifyRequest = vi.fn()

    render(<PromptWorkshop catalog={buildCatalog()} onVerifyRequest={onVerifyRequest} />)

    await waitFor(() => {
      expect(serviceMocks.fetchModelOptions).toHaveBeenCalled()
    })

    fireEvent.click(await screen.findByRole('button', { name: 'Confused about models? Chat with Claude' }))

    expect(onVerifyRequest).toHaveBeenCalledTimes(1)
    expect(onVerifyRequest.mock.calls[0][0]).toContain('Help me choose the best model settings')
  })

  it('opens a system-prompt discussion request with Claude', async () => {
    const onVerifyRequest = vi.fn()

    render(<PromptWorkshop catalog={buildCatalog()} onVerifyRequest={onVerifyRequest} />)

    await waitFor(() => {
      expect(serviceMocks.fetchModelOptions).toHaveBeenCalled()
    })

    fireEvent.click(await screen.findByRole('button', { name: 'Discuss prompt changes with Claude' }))

    expect(onVerifyRequest).toHaveBeenCalledTimes(1)
    expect(onVerifyRequest.mock.calls[0][0]).toContain('Help me improve the SYSTEM PROMPT')
  })

  it('applies incoming MOD prompt updates from Opus approval into MOD overrides', async () => {
    const onContextChange = vi.fn()
    const { rerender } = render(
      <PromptWorkshop
        catalog={buildCatalogWithModRule()}
        incomingPromptUpdate={null}
        onContextChange={onContextChange}
      />
    )

    await waitFor(() => {
      expect(serviceMocks.fetchAgentTemplates).toHaveBeenCalled()
    })

    rerender(
      <PromptWorkshop
        catalog={buildCatalogWithModRule()}
        onContextChange={onContextChange}
        incomingPromptUpdate={{
          request_id: 2,
          prompt: 'WB override from Claude',
          summary: 'Updated WB-specific extraction guidance.',
          apply_mode: 'replace',
          target_prompt: 'mod',
          target_mod_id: 'WB',
        }}
      />
    )

    await screen.findByText(
      (content) => (
        content.includes('Applied Claude MOD update (WB):')
        && content.includes('Updated WB-specific extraction guidance.')
      ),
      {},
      { timeout: 5000 }
    )

    await waitFor(() => {
      const contextSnapshots = onContextChange.mock.calls.map((call) => call[0])
      expect(contextSnapshots).toContainEqual(
        expect.objectContaining({
          selected_mod_id: 'WB',
          selected_mod_prompt_draft: 'WB override from Claude',
        })
      )
    }, { timeout: 5000 })
  }, 15000)
})

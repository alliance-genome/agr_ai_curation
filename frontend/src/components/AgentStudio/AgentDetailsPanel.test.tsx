import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentDetailsPanel from './AgentDetailsPanel'
import { buildDomainEnvelopeMetadata } from '@/test/fixtures/agentStudioDomainEnvelope'
import type { PromptInfo } from '@/types/promptExplorer'

const serviceMocks = vi.hoisted(() => ({
  fetchCombinedPrompt: vi.fn(),
  fetchAllTools: vi.fn(),
  fetchToolDetails: vi.fn(),
}))
const metadataMocks = vi.hoisted(() => ({
  agents: {} as Record<string, unknown>,
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)
vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: metadataMocks.agents,
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
      group_a: {
        group_id: 'group_a',
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

function buildDocumentedAgent(): PromptInfo {
  return {
    agent_id: 'example_validator',
    agent_name: 'Example validator',
    description: 'Fallback description',
    base_prompt: 'Base prompt',
    source_file: 'database',
    has_group_rules: false,
    group_rules: {},
    tools: ['lookup_term', 'search_synonyms', 'get_id', 'search_db', 'record_evidence'],
    documentation: {
      summary: 'Confirms names against the ontology.',
      capabilities: [{ name: 'Name lookup', description: 'Find identifiers for names.' }],
      data_sources: [],
      limitations: ['Only queries one ontology.'],
      use_when: ['After any extractor that names a term.'],
      avoid_when: ['For parent or child terms.'],
    },
  }
}

describe('AgentDetailsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    metadataMocks.agents = {}
    serviceMocks.fetchAllTools.mockResolvedValue({
      lookup_term: { name: 'Lookup term', description: 'Find a term by name.' },
    })
    serviceMocks.fetchToolDetails.mockResolvedValue({
      name: 'Search DB',
      description: 'Read-only query.',
      category: 'Database',
      source_file: 'tools/search_db.py',
      documentation: { summary: 'Runs a query.', parameters: [] },
    })
  })

  it('shows a composed empty state when no agent is selected', () => {
    render(<AgentDetailsPanel agent={null} selectedGroupId={null} onGroupSelect={vi.fn()} />)
    expect(screen.getByText('Browse your agents')).toBeInTheDocument()
    expect(screen.getByText(/pick an agent on the left/i)).toBeInTheDocument()
  })

  it('renders the header with name, summary, two actions, three tabs, and no tool chip wall', () => {
    const onDiscuss = vi.fn()
    const onClone = vi.fn()
    render(
      <AgentDetailsPanel
        agent={buildDocumentedAgent()}
        selectedGroupId={null}
        onGroupSelect={vi.fn()}
        onDiscussWithClaude={onDiscuss}
        onCloneToWorkshop={onClone}
      />
    )

    expect(screen.getByRole('heading', { level: 2, name: 'Example validator' })).toBeInTheDocument()
    expect(screen.getByText('Confirms names against the ontology.')).toBeInTheDocument()
    expect(screen.queryByText('Fallback description')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Discuss with Claude' }))
    expect(onDiscuss).toHaveBeenCalledWith('example_validator', 'Example validator')
    fireEvent.click(screen.getByRole('button', { name: 'Clone to Workshop' }))
    expect(onClone).toHaveBeenCalledWith('example_validator')

    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    expect(tabs).toEqual(['Guide', 'Prompts'])
    expect(screen.queryByText('Tools:')).not.toBeInTheDocument()
    expect(screen.queryByText('record_evidence')).not.toBeInTheDocument()

    expect(screen.getByRole('region', { name: 'When to use it' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Limitations' })).toBeInTheDocument()
    expect(screen.getByText('5 tools')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back to Agents' })).not.toBeInTheDocument()
  })

  it('opens ToolDetailsDialog from the tools table Details link', async () => {
    render(<AgentDetailsPanel agent={buildDocumentedAgent()} selectedGroupId={null} onGroupSelect={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Show all tools' }))
    await waitFor(() => {
      expect(screen.getByText('Find a term by name.')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Details for search_db' }))

    expect(serviceMocks.fetchToolDetails).toHaveBeenCalledWith('search_db', 'example_validator')
    expect(await screen.findByText('Tool Details')).toBeInTheDocument()
    expect(await screen.findByText('Search DB')).toBeInTheDocument()
  })

  it('surfaces a tool inventory failure in the Tools section and retries on demand', async () => {
    serviceMocks.fetchAllTools
      .mockRejectedValueOnce(new Error('Failed to fetch tools: 500'))
      .mockResolvedValueOnce({ lookup_term: { name: 'Lookup term', description: 'Find a term by name.' } })

    render(<AgentDetailsPanel agent={buildDocumentedAgent()} selectedGroupId={null} onGroupSelect={vi.fn()} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Tool descriptions could not be loaded. Failed to fetch tools: 500')

    fireEvent.click(screen.getByRole('button', { name: 'Show all tools' }))
    expect(screen.getAllByText('Not loaded')).toHaveLength(5)
    expect(screen.queryByText('No description yet')).not.toBeInTheDocument()

    fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Retry' }))
    await waitFor(() => {
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
    expect(serviceMocks.fetchAllTools).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Find a term by name.')).toBeInTheDocument()
    expect(screen.getAllByText('No description yet')).toHaveLength(4)
  })

  it('sends a drafting prompt through the discuss handoff for sparse documentation', () => {
    const onDiscuss = vi.fn()
    const sparse = { ...buildDocumentedAgent(), documentation: undefined }
    render(
      <AgentDetailsPanel agent={sparse} selectedGroupId={null} onGroupSelect={vi.fn()} onDiscussWithClaude={onDiscuss} />
    )

    expect(screen.getByText('No curator guide yet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Ask Claude to draft a guide' }))
    expect(onDiscuss).toHaveBeenCalledTimes(1)
    const [agentId, agentName, prompt] = onDiscuss.mock.calls[0]
    expect(agentId).toBe('example_validator')
    expect(agentName).toBe('Example validator')
    expect(prompt).toMatch(/Draft a curator guide/)
    expect(prompt).toMatch(/Agent ID: example_validator/)
  })

  it('shows the Envelope tab only when the agent declares a domain pack', () => {
    metadataMocks.agents = {
      example_validator: { domain_envelope: buildDomainEnvelopeMetadata() },
    }
    render(<AgentDetailsPanel agent={buildDocumentedAgent()} selectedGroupId={null} onGroupSelect={vi.fn()} />)

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Guide', 'Envelope', 'Prompts'])
    fireEvent.click(screen.getByRole('tab', { name: 'Envelope' }))
    expect(screen.getByRole('table', { name: 'Gene mention evidence fields' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Schema and provenance' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('renders the Back to Agents control and one-column stripes at narrow width', () => {
    const onBack = vi.fn()
    render(
      <AgentDetailsPanel agent={buildDocumentedAgent()} selectedGroupId={null} onGroupSelect={vi.fn()} onBack={onBack} narrow />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Back to Agents' }))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('presents package-owned group restrictions as read-only', () => {
    const systemAgent = { ...buildCleanCustomAgent(), agent_id: 'gene', agent_name: 'Gene Specialist' }
    metadataMocks.agents = {
      gene: { allowed_group_ids: ['group_a'] },
    }

    render(
      <AgentDetailsPanel
        agent={systemAgent}
        selectedGroupId={null}
        onGroupSelect={vi.fn()}
      />
    )

    expect(screen.getByText(/Available to groups: group_a/)).toBeInTheDocument()
    expect(screen.getByText(/package-owned system restriction is read-only/)).toBeInTheDocument()
  })

  it('marks flagged custom prompt text and excludes it from the effective view', () => {
    render(
      <AgentDetailsPanel
        agent={buildFlaggedAgent()}
        selectedGroupId="group_a"
        onGroupSelect={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('tab', { name: 'Prompts' }))

    expect(serviceMocks.fetchCombinedPrompt).not.toHaveBeenCalled()
    expect(screen.getByText('Custom-agent prompt still contains locked/core prompt markers after safe cleanup.')).toBeInTheDocument()

    const effective = screen.getByTestId('prompt-reading-pane')
    expect(effective).toHaveTextContent('Safe locked core contract')
    expect(effective).toHaveTextContent('Parent base prompt')
    expect(effective).not.toHaveTextContent('Platform Runtime Contract copied fragment')

    fireEvent.click(within(screen.getByRole('group', { name: 'Prompt layer' })).getByRole('button', { name: /^Override/ }))
    expect(screen.getByTestId('prompt-reading-pane')).toHaveTextContent('Platform Runtime Contract copied fragment')
  })

  it('keeps locked layers in the selected-group fallback for custom agents', async () => {
    serviceMocks.fetchCombinedPrompt.mockRejectedValue(new Error('combined preview unavailable'))

    render(
      <AgentDetailsPanel
        agent={buildCleanCustomAgent()}
        selectedGroupId="group_a"
        onGroupSelect={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(serviceMocks.fetchCombinedPrompt).toHaveBeenCalledWith(
        'ca_11111111-2222-3333-4444-555555555555',
        'group_a'
      )
    })

    fireEvent.click(screen.getByRole('tab', { name: 'Prompts' }))

    const effective = screen.getByTestId('prompt-reading-pane')
    await waitFor(() => {
      expect(effective).not.toHaveTextContent('Loading effective prompt')
    })
    expect(effective).toHaveTextContent('Safe locked core contract')
    expect(effective).toHaveTextContent('Parent base prompt')
    expect(effective).toHaveTextContent('Curator overlay guidance')
  })
})

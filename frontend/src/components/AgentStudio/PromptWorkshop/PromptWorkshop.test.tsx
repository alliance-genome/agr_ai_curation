import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, beforeEach, expect, it, vi } from 'vitest'
import { createRef } from 'react'
import { webcrypto } from 'node:crypto'
import { fingerprintWorkshopDraft } from '../authoringContext'

import PromptWorkshop, {
  type WorkshopAuthoringContextHandle,
  type WorkshopLeaveGuard,
} from './PromptWorkshop'
import { buildDomainEnvelopeMetadata } from '@/test/fixtures/agentStudioDomainEnvelope'
import { buildExecutionRevision as buildVersion } from '@/test/fixtures/agentExecutionRevision'
import type {
  PromptCatalog,
  CustomAgent,
  ModelOption,
  ToolLibraryItem,
  AgentTemplate,
  GroupOption,
} from '@/types/promptExplorer'

const serviceMocks = vi.hoisted(() => ({
  validateWorkshopDraft: vi.fn(),
  createCustomAgent: vi.fn(),
  getWorkshopSavedReference: vi.fn(),
  getAgentExecutionRevision: vi.fn(),
  deleteCustomAgent: vi.fn(),
  fetchAgentTemplates: vi.fn(),
  fetchModelOptions: vi.fn(),
  fetchToolLibrary: vi.fn(),
  listToolIdeaRequests: vi.fn(),
  listAgentExecutionRevisions: vi.fn(),
  listCustomAgents: vi.fn(),
  restoreAgentExecutionRevision: vi.fn(),
  setCustomAgentVisibility: vi.fn(),
  submitToolIdeaRequest: vi.fn(),
  updateCustomAgent: vi.fn(),
}))

const metadataMocks = vi.hoisted(() => ({
  agents: {} as Record<string, unknown>,
  refresh: vi.fn(),
}))

const profileMocks = vi.hoisted(() => ({ validateGenericProfile: vi.fn(), getGenericProfile: vi.fn(), getGenericProfileRevision: vi.fn(), listGenericProfiles: vi.fn(), getProfileMappingOptions: vi.fn() }))
vi.mock('@/services/genericProfileService', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/genericProfileService')>(),
  validateGenericProfile: profileMocks.validateGenericProfile,
  getGenericProfile: profileMocks.getGenericProfile,
  getGenericProfileRevision: profileMocks.getGenericProfileRevision,
  listGenericProfiles: profileMocks.listGenericProfiles,
  getProfileMappingOptions: profileMocks.getProfileMappingOptions,
}))

const authMocks = vi.hoisted(() => ({
  user: {
    uid: 'doug-test-user',
    email: 'doughowe@uoregon.edu',
    name: 'Doug Howe',
    groups: ['ZFIN'],
    providerGroups: ['zfin-curators'],
  },
}))

vi.mock('@/services/agentStudioService', () => serviceMocks)
vi.mock('@/contexts/AgentMetadataContext', () => ({
  useAgentMetadata: () => ({
    agents: metadataMocks.agents,
    refresh: metadataMocks.refresh,
    isLoading: false,
    error: null,
  }),
}))
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: authMocks.user,
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
            has_group_rules: false,
            group_rules: {},
            tools: ['agr_curation_query'],
          },
        ],
      },
    ],
    total_agents: 1,
    available_groups: [],
    last_updated: '2026-02-23T00:00:00Z',
  }
}

function buildCatalogWithGroupRule(): PromptCatalog {
  const catalog = buildCatalog()
  catalog.categories[0].agents[0].has_group_rules = true
  catalog.categories[0].agents[0].group_rules = {
    WB: { group_id: 'WB', content: 'WB template prompt', source_file: 'database' },
  }
  catalog.available_groups = ['WB']
  return catalog
}

function buildCatalogWithPromptLayers(): PromptCatalog {
  const catalog = buildCatalogWithGroupRule()
  catalog.categories[0].agents[0].prompt_layers = [
    {
      id: 'gene:core_static',
      kind: 'core_static',
      title: 'Platform runtime contract',
      content: 'Locked core contract',
      provenance: 'backend_static',
      editable: false,
      locked: true,
      source_ref: 'core',
      hash: 'hash-core',
    },
    {
      id: 'gene:core_generated',
      kind: 'core_generated',
      title: 'Generated runtime contract',
      content: 'Locked generated contract',
      provenance: 'backend_generated',
      editable: false,
      locked: true,
      source_ref: 'generated',
      hash: 'hash-generated',
    },
    {
      id: 'gene:base_prompt',
      kind: 'base_prompt',
      title: 'Editable base prompt',
      content: 'System base prompt',
      provenance: 'prompt_template:system',
      editable: true,
      locked: false,
      source_ref: 'base',
      hash: 'hash-base',
    },
  ]
  return catalog
}

function buildCatalogWithTemplateSpecificGroupRules(): PromptCatalog {
  return {
    categories: [
      {
        category: 'Validation',
        agents: [
          {
            agent_id: 'gene',
            agent_name: 'Gene Specialist',
            description: 'Gene validation',
            base_prompt: 'Gene base prompt',
            source_file: 'database',
            has_group_rules: true,
            group_rules: {
              FB: { group_id: 'FB', content: 'FB template prompt', source_file: 'database' },
              WB: { group_id: 'WB', content: 'WB template prompt', source_file: 'database' },
            },
            tools: ['agr_curation_query'],
          },
          {
            agent_id: 'disease',
            agent_name: 'Disease Specialist',
            description: 'Disease validation',
            base_prompt: 'Disease base prompt',
            source_file: 'database',
            has_group_rules: true,
            group_rules: {
              WB: { group_id: 'WB', content: 'Disease WB template prompt', source_file: 'database' },
            },
            tools: ['agr_curation_query'],
          },
        ],
      },
    ],
    total_agents: 2,
    available_groups: ['WB', 'FB', 'MGI'],
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
    group_prompt_overrides: {},
    allowed_group_ids: [],
    inherited_allowed_group_ids: [],
    icon: '🔧',
    include_group_rules: true,
    model_id: 'gpt-5.6-terra',
    model_temperature: 0.1,
    model_reasoning: undefined,
    tool_ids: [],
    output_schema_key: undefined,
    visibility: 'private',
    project_id: undefined,
    is_active: true,
    created_at: '2026-02-23T00:00:00Z',
    updated_at: '2026-02-23T00:00:00Z',
    execution_revision_id: 'version-2',
    ...overrides,
  }
}

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function gotoSection(section: 'Setup' | 'Prompt' | 'Tools' | 'Versions'): void {
  const nav = screen.getByRole('navigation', { name: 'Agent Workshop sections' })
  fireEvent.click(within(nav).getByRole('button', { name: new RegExp(`^${section}`) }))
}

async function startFromTemplate(): Promise<void> {
  const card = await screen.findByRole('button', { name: /From a template/ })
  await waitFor(() => expect(card).toBeEnabled())
  fireEvent.click(card)
}

async function waitForHeaderName(value: string): Promise<void> {
  await waitFor(() => {
    expect(screen.getByRole('heading', { level: 2, name: value })).toBeInTheDocument()
  }, { timeout: 10000 })
}

async function saveFromHeader(note?: string): Promise<void> {
  const saveButton = screen.getByRole('button', { name: 'Save' })
  await waitFor(() => expect(saveButton).toBeEnabled())
  fireEvent.click(saveButton)
  const dialog = await screen.findByRole('dialog', { name: /Save (as version|new agent)/ })
  if (note !== undefined) {
    fireEvent.change(within(dialog).getByLabelText('Note (optional)'), { target: { value: note } })
  }
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
}

async function selectOption(comboboxName: string, optionName: string | RegExp): Promise<void> {
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: comboboxName }))
  fireEvent.click(await screen.findByRole('option', { name: optionName }))
}

/** Multi-selects stay open after a click; close the menu so the page is no longer aria-hidden. */
async function closeSelectMenu(): Promise<void> {
  const listbox = screen.queryByRole('listbox')
  if (listbox) fireEvent.keyDown(listbox, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument())
}

function groupPicker(): HTMLElement {
  gotoSection('Prompt')
  return screen.getByRole('group', { name: 'Group' })
}

async function assertGroupOptions(expected: string[], absent: string[] = []): Promise<void> {
  await waitFor(() => {
    const picker = groupPicker()
    expected.forEach((groupId) => {
      expect(within(picker).getByRole('button', { name: new RegExp(`^${groupId}`) })).toBeInTheDocument()
    })
  })
  const picker = groupPicker()
  absent.forEach((groupId) => {
    expect(within(picker).queryByRole('button', { name: new RegExp(`^${groupId}`) })).not.toBeInTheDocument()
  })
}

describe('PromptWorkshop', () => {
  const modelOptions: ModelOption[] = [
    {
      model_id: 'gpt-5.6-terra',
      name: 'GPT-5.6 Terra',
      provider: 'openai',
      description: 'fast reasoning model',
      guidance: 'Use for validation, lookups, utilities, and iterative drafting.',
      default: true,
      supports_reasoning: true,
      supports_temperature: false,
      reasoning_options: ['low', 'medium', 'high', 'xhigh'],
      default_reasoning: 'medium',
      reasoning_descriptions: { low: 'Fastest', medium: 'Balanced', high: 'Deep', xhigh: 'Deepest' },
      recommended_for: ['Validation and lightweight work'],
      avoid_for: ['Deep multi-step adjudication'],
    },
    {
      model_id: 'gpt-5.6-sol',
      name: 'GPT-5.6 Sol',
      provider: 'openai',
      description: 'deep reasoning model',
      guidance: 'Use for complex PDF extraction and difficult reasoning.',
      default: false,
      supports_reasoning: true,
      supports_temperature: false,
      reasoning_options: ['low', 'medium', 'high', 'xhigh'],
      default_reasoning: 'medium',
      reasoning_descriptions: { low: 'Fast', medium: 'Balanced', high: 'Slow', xhigh: 'Slowest' },
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
      config: { requires_document: true },
    },
    {
      tool_key: 'admin_only_tool',
      display_name: 'Admin Tool',
      description: 'Restricted',
      category: 'Admin',
      curator_visible: true,
      allow_attach: false,
      allow_execute: false,
      config: { requires_document: false },
    },
    {
      tool_key: 'chebi_lookup',
      display_name: 'ChEBI Lookup',
      description: 'Chemicals',
      category: 'External API',
      curator_visible: true,
      allow_attach: true,
      allow_execute: true,
      config: { requires_document: false },
    },
  ]

  const templates: AgentTemplate[] = [
    {
      agent_id: 'gene',
      name: 'Gene Specialist',
      description: 'Gene validation',
      icon: '🧬',
      category: 'Validation',
      model_id: 'gpt-5.6-terra',
      tool_ids: ['search_document'],
      allowed_group_ids: [],
      output_schema_key: undefined,
    },
  ]

  const multiTemplateOptions: AgentTemplate[] = [
    templates[0],
    {
      agent_id: 'disease',
      name: 'Disease Specialist',
      description: 'Disease validation',
      icon: '🦠',
      category: 'Validation',
      model_id: 'gpt-5.6-terra',
      tool_ids: ['search_document'],
      allowed_group_ids: [],
      output_schema_key: undefined,
    },
  ]

  const groupOptions: GroupOption[] = [
    { group_id: 'GROUP_A', name: 'Group A' },
    { group_id: 'GROUP_B', name: 'Group B' },
    { group_id: 'GROUP_C', name: 'Group C' },
    { group_id: 'GROUP_D', name: 'Group D' },
  ]

  beforeEach(() => {
    authMocks.user = {
      uid: 'doug-test-user',
      email: 'doughowe@uoregon.edu',
      name: 'Doug Howe',
      groups: ['ZFIN'],
      providerGroups: ['zfin-curators'],
    }
    metadataMocks.agents = {}
    Object.values(serviceMocks).forEach((mock) => mock.mockReset())
    Object.values(profileMocks).forEach((mock) => mock.mockReset())
    profileMocks.validateGenericProfile.mockResolvedValue({ fingerprint: 'validated' })
    metadataMocks.refresh.mockReset()

    metadataMocks.refresh.mockResolvedValue(undefined)
    serviceMocks.fetchModelOptions.mockResolvedValue(modelOptions)
    serviceMocks.fetchToolLibrary.mockResolvedValue(toolLibrary)
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates, group_options: groupOptions })
    serviceMocks.listToolIdeaRequests.mockResolvedValue({ tool_ideas: [], total: 0 })
    serviceMocks.listAgentExecutionRevisions.mockResolvedValue({ revisions: [], next_before_revision: null })
    serviceMocks.getAgentExecutionRevision.mockImplementation(async (agentId: string, revisionId: string) => ({
      ...buildVersion(2), id: revisionId, agent_id: agentId,
    }))
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [], total: 0 })
    serviceMocks.createCustomAgent.mockResolvedValue(buildCustomAgent())
    serviceMocks.getWorkshopSavedReference.mockResolvedValue({ agent_id: buildCustomAgent().agent_id })
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

  // ── Start screen and origin ──

  it('opens on the start screen and lands on Setup with the chosen origin', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} />)

    expect(await screen.findByRole('group', { name: 'Start a new agent' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'New agent' })).toBeInTheDocument()
    expect(screen.getByText('Not saved yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Clone one of yours/ })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /From scratch/ }))
    expect(screen.queryByRole('group', { name: 'Start a new agent' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scratch' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('From scratch · Not saved yet')).toBeInTheDocument()
  }, 15000)

  it('captures the complete current draft immediately after an edit', async () => {
    const authoringRef = createRef<WorkshopAuthoringContextHandle>()
    render(<PromptWorkshop catalog={buildCatalog()} authoringContextRef={authoringRef} />)

    fireEvent.click(await screen.findByRole('button', { name: /From scratch/ }))
    fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: 'Immediate Agent' } })
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Exact description' } })
    gotoSection('Prompt')
    fireEvent.change(screen.getByLabelText('Your prompt'), {
      target: { value: 'The latest prompt keystroke' },
    })

    const context = authoringRef.current!.captureAuthoringContext()
    expect(context).toEqual(expect.objectContaining({
      getting_started_mode: 'scratch',
      draft_name: 'Immediate Agent',
      draft_description: 'Exact description',
      prompt_draft: 'The latest prompt keystroke',
      draft_visibility: 'private',
      draft_allowed_group_ids: [],
      inherited_allowed_group_ids: [],
      group_prompt_overrides: {},
      draft_is_dirty: true,
    }))
    expect(context.draft_tool_ids).toEqual([])
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
    expect(serviceMocks.updateCustomAgent).not.toHaveBeenCalled()
  }, 15000)

  it('lands on Setup with the template selector focused after From a template', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')
    expect(screen.getByText('Template: Gene Specialist · Not saved yet')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Template' })).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  }, 15000)

  it('skips the start screen when a template is preselected', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" />)
    await waitForHeaderName('Gene Specialist (Custom)')
    expect(screen.queryByRole('group', { name: 'Start a new agent' })).not.toBeInTheDocument()
  }, 15000)

  // ── Saving ──

  it('does not publish a Flow handoff or persist when Save is canceled', async () => {
    const onSavedHandoff = vi.fn()
    render(<PromptWorkshop catalog={buildCatalog()} onSavedHandoff={onSavedHandoff} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
    expect(onSavedHandoff).not.toHaveBeenCalled()
    await waitForHeaderName('Gene Specialist (Custom)')
  })

  it('saves new agents with template_source payload (no parent_agent_id)', async () => {
    const onSavedHandoff = vi.fn()
    const origin = { flow_id: 'flow-1', flow_draft_fingerprint: 'sha256:original-flow' }
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [], total: 0 })
      .mockResolvedValue({ custom_agents: [buildCustomAgent()], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} continuationOrigin={origin} onSavedHandoff={onSavedHandoff} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    expect(dialog).toHaveTextContent('Creates version 1 of this agent.')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1))
    const payload = serviceMocks.createCustomAgent.mock.calls[0][0]
    expect(payload.template_source).toBe('gene')
    expect(payload.model_id).toBe('gpt-5.6-terra')
    expect(payload.allowed_group_ids).toEqual([])
    expect(payload.tool_ids).toEqual(['search_document'])
    expect(payload.icon).toBe('🔧')
    expect(payload).not.toHaveProperty('parent_agent_id')
    expect(payload).not.toHaveProperty('notes')

    await waitForHeaderName('My Agent')
    expect(screen.getByRole('status')).toHaveTextContent('Saved just now')
    expect(screen.getByText('Template: Gene Specialist')).toBeInTheDocument()
    expect(onSavedHandoff).toHaveBeenCalledWith({
      status: 'ready', saved_agent_id: buildCustomAgent().agent_id,
      saved_custom_agent_id: buildCustomAgent().id, origin,
    })
  }, 15000)

  it.each(['failure', 'unavailable'])('retains a saved identity when catalog refresh reports %s without emitting an actionable handoff', async (outcome) => {
    const onSavedHandoff = vi.fn()
    serviceMocks.listCustomAgents
      .mockResolvedValue({ custom_agents: [], total: 0 })
    render(<PromptWorkshop catalog={buildCatalog()} onSavedHandoff={onSavedHandoff} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')
    if (outcome === 'failure') {
      serviceMocks.listCustomAgents.mockRejectedValue(new Error('Private server payload'))
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSavedHandoff).toHaveBeenCalledWith({
      status: 'catalog_unavailable', origin: undefined,
    }))
    await waitForHeaderName('My Agent')
    expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Private server payload')).not.toBeInTheDocument()
  })

  it('asks for a note, lists changed sections, and sends the note with the update', async () => {
    const existing = buildCustomAgent({ tool_ids: ['search_document'] })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.listAgentExecutionRevisions.mockResolvedValue({ revisions: [buildVersion(1), buildVersion(2)], next_before_revision: null })
    serviceMocks.updateCustomAgent.mockResolvedValue(existing)

    render(<PromptWorkshop catalog={buildCatalogWithGroupRule()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('My Agent')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    gotoSection('Prompt')
    fireEvent.change(screen.getByLabelText('Your prompt'), { target: { value: 'Prompt with edits' } })
    fireEvent.click(within(screen.getByRole('group', { name: 'Group' })).getByRole('button', { name: 'WB' }))
    fireEvent.change(screen.getByLabelText('WB instructions'), { target: { value: 'WB override' } })
    gotoSection('Tools')
    fireEvent.click(screen.getByRole('button', { name: 'Remove search_document' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add tools' }))
    const library = await screen.findByRole('dialog', { name: /Add tools/ })
    fireEvent.click(within(library).getByRole('checkbox', { name: /chebi_lookup/ }))
    fireEvent.click(within(library).getByRole('button', { name: 'Attach 1 tool' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes')
    const nav = screen.getByRole('navigation', { name: 'Agent Workshop sections' })
    expect(within(nav).getByRole('button', { name: 'Prompt, unsaved edits' })).toBeInTheDocument()
    expect(within(nav).getByRole('button', { name: 'Tools, 1 attached, unsaved edits' })).toBeInTheDocument()
    expect(within(nav).getByRole('button', { name: 'Versions, 2' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save as version 3/ })
    expect(dialog).toHaveTextContent('Changed since v2: Your prompt, WB instructions, Tools.')
    fireEvent.change(within(dialog).getByLabelText('Note (optional)'), { target: { value: 'Second pass' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(serviceMocks.updateCustomAgent).toHaveBeenCalledTimes(1))
    const [id, payload] = serviceMocks.updateCustomAgent.mock.calls[0]
    expect(id).toBe(existing.id)
    expect(payload).not.toHaveProperty('output_schema_key')
    expect(payload.notes).toBe('Second pass')
    expect(payload.custom_prompt).toBe('Prompt with edits')
    expect(payload.group_prompt_overrides).toEqual({ WB: 'WB override' })
    expect(payload.tool_ids).toEqual(['chebi_lookup'])
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
  }, 20000)

  it('shows the save-failed pill and keeps edits when the update rejects', async () => {
    const onSavedHandoff = vi.fn()
    const existing = buildCustomAgent()
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.updateCustomAgent.mockRejectedValue(new Error('409: another curator saved version 3'))

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} onSavedHandoff={onSavedHandoff} />)
    await waitForHeaderName('My Agent')
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Changed description' } })
    await saveFromHeader()

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Save failed'))
    expect(screen.getByRole('alert')).toHaveTextContent('Could not save. 409: another curator saved version 3 Your edits are still here.')
    expect(screen.getByLabelText('Description')).toHaveValue('Changed description')
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(onSavedHandoff).not.toHaveBeenCalled()
  }, 15000)

  it('uses canonical group options and warns before saving a restriction that excludes the owner', async () => {
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [], total: 0 })
      .mockResolvedValue({ custom_agents: [buildCustomAgent({ allowed_group_ids: ['GROUP_B'] })], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    expect(screen.getByText('Sharing sets who can see this agent. Groups restrict who can run it.')).toBeInTheDocument()
    await selectOption('Available to groups', /Group B GROUP_B/)
    await closeSelectMenu()
    await saveFromHeader()

    const warningDialog = await screen.findByRole('dialog', { name: 'Save a restriction that excludes you?' })
    expect(within(warningDialog).getByText(/your current groups are ZFIN/)).toBeInTheDocument()
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()

    fireEvent.click(within(warningDialog).getByRole('button', { name: 'Save restriction' }))
    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1))
    expect(serviceMocks.createCustomAgent.mock.calls[0][0].allowed_group_ids).toEqual(['GROUP_B'])
  }, 15000)

  it('hydrates and updates an existing group restriction', async () => {
    const restrictedAgent = buildCustomAgent({ allowed_group_ids: ['GROUP_B'] })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [restrictedAgent], total: 1 })
    serviceMocks.updateCustomAgent.mockResolvedValue(restrictedAgent)

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={restrictedAgent.id} />)
    await waitForHeaderName('My Agent')
    expect(screen.getByRole('combobox', { name: 'Available to groups' })).toHaveTextContent('GROUP_B')

    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Now restricted' } })
    await saveFromHeader()
    const warningDialog = await screen.findByRole('dialog', { name: 'Save a restriction that excludes you?' })
    fireEvent.click(within(warningDialog).getByRole('button', { name: 'Save restriction' }))

    await waitFor(() => expect(serviceMocks.updateCustomAgent).toHaveBeenCalledTimes(1))
    expect(serviceMocks.updateCustomAgent.mock.calls[0][1].allowed_group_ids).toEqual(['GROUP_B'])
    expect(serviceMocks.updateCustomAgent.mock.calls[0][1].description).toBe('Now restricted')
  }, 15000)

  it('keeps a package-owned template restriction as the clone access floor', async () => {
    const restrictedTemplate: AgentTemplate = { ...templates[0], allowed_group_ids: ['GROUP_B'] }
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates: [restrictedTemplate], group_options: groupOptions })

    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()

    expect(await screen.findByText(/This template is restricted to GROUP_B/)).toBeInTheDocument()
    const groupSelect = screen.getByRole('combobox', { name: 'Available to groups' })
    await waitFor(() => expect(groupSelect).toHaveTextContent('GROUP_B'))
    expect(screen.getByText('Inherits a GROUP_B access floor; you can narrow it, not widen it.')).toBeInTheDocument()

    fireEvent.mouseDown(groupSelect)
    expect(screen.queryByRole('option', { name: 'All groups' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /Group B GROUP_B/ }))
    await closeSelectMenu()
    expect(groupSelect).toHaveTextContent('GROUP_B')
  }, 15000)

  it('uses a restricted custom clone persisted access floor when its system template is unrestricted', async () => {
    const restrictedClone = buildCustomAgent({
      allowed_group_ids: ['GROUP_B', 'GROUP_C'],
      inherited_allowed_group_ids: ['GROUP_B', 'GROUP_C'],
    })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [restrictedClone], total: 1 })
    serviceMocks.updateCustomAgent.mockResolvedValue({ ...restrictedClone, allowed_group_ids: ['GROUP_B'] })

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={restrictedClone.id} />)
    await waitForHeaderName('My Agent')
    const groupSelect = screen.getByRole('combobox', { name: 'Available to groups' })
    expect(groupSelect).toHaveTextContent('GROUP_B, GROUP_C')
    expect(screen.getByText('Inherits a GROUP_B, GROUP_C access floor; you can narrow it, not widen it.')).toBeInTheDocument()

    fireEvent.mouseDown(groupSelect)
    expect(screen.queryByRole('option', { name: 'All groups' })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /GROUP_D/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /Group C GROUP_C/ }))
    await closeSelectMenu()
    expect(groupSelect).toHaveTextContent('GROUP_B')
    expect(groupSelect).not.toHaveTextContent('GROUP_C')

    await saveFromHeader()
    const warningDialog = await screen.findByRole('dialog', { name: 'Save a restriction that excludes you?' })
    fireEvent.click(within(warningDialog).getByRole('button', { name: 'Save restriction' }))

    await waitFor(() => expect(serviceMocks.updateCustomAgent).toHaveBeenCalledTimes(1))
    expect(serviceMocks.updateCustomAgent.mock.calls[0][1].allowed_group_ids).toEqual(['GROUP_B'])
  }, 15000)

  it('shares newly created agents when visibility is set to project', async () => {
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [], total: 0 })
      .mockResolvedValue({ custom_agents: [buildCustomAgent({ visibility: 'project' })], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    await selectOption('Visibility', 'Shared with project')
    await saveFromHeader()

    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1))
    expect(serviceMocks.createCustomAgent.mock.calls[0][0].visibility).toBe('project')
    expect(serviceMocks.setCustomAgentVisibility).not.toHaveBeenCalled()
  }, 15000)

  it('saves selected reasoning for reasoning-capable models', async () => {
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [], total: 0 })
      .mockResolvedValue({ custom_agents: [buildCustomAgent({ model_id: 'gpt-5.6-sol', model_reasoning: 'high' })], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    await selectOption('Model', 'GPT-5.6 Sol')
    await selectOption('Reasoning', 'High')
    expect(screen.getByText(/High reasoning selected\. The default for GPT-5.6 Sol is Medium\. Slow\./)).toBeInTheDocument()
    await saveFromHeader()

    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1))
    expect(serviceMocks.createCustomAgent.mock.calls[0][0].model_id).toBe('gpt-5.6-sol')
    expect(serviceMocks.createCustomAgent.mock.calls[0][0].model_reasoning).toBe('high')
  }, 15000)

  it('saves a copy via Save as without updating the original agent', async () => {
    const existing = buildCustomAgent({ name: 'Original Agent' })
    const copied = buildCustomAgent({
      id: '22222222-2222-2222-2222-222222222222',
      agent_id: 'ca_22222222-2222-2222-2222-222222222222',
      name: 'Original Agent (Copy)',
    })
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [existing], total: 1 })
      .mockResolvedValue({ custom_agents: [existing, copied], total: 2 })
    serviceMocks.createCustomAgent.mockResolvedValue(copied)

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('Original Agent')

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Save as…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Save as a new agent' })
    expect(within(dialog).getByLabelText('Agent name')).toHaveValue('Original Agent (Copy)')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save as' }))

    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledTimes(1))
    expect(serviceMocks.createCustomAgent.mock.calls[0][0].name).toBe('Original Agent (Copy)')
    expect(serviceMocks.updateCustomAgent).not.toHaveBeenCalled()
    await waitForHeaderName('Original Agent (Copy)')
  }, 15000)

  it('blocks saving an existing agent when all previously attached tools are removed', async () => {
    const existing = buildCustomAgent({ name: 'Tooled Agent', tool_ids: ['search_document'] })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.updateCustomAgent.mockResolvedValue(existing)

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('Tooled Agent')

    gotoSection('Tools')
    fireEvent.click(screen.getByRole('button', { name: 'Remove search_document' }))
    expect(screen.getByText(/No tools attached/)).toBeInTheDocument()
    await saveFromHeader()

    await waitFor(() => {
      expect(screen.getByText(/Cannot save this agent with no tools selected/)).toBeInTheDocument()
    })
    expect(serviceMocks.updateCustomAgent).not.toHaveBeenCalled()
  }, 15000)

  // ── Opening, deleting, unsaved-change guard ──

  it('opens the provided initial custom agent id for editing', async () => {
    const existing = buildCustomAgent({ name: 'Cloned Agent' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.updateCustomAgent.mockResolvedValue(existing)

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('Cloned Agent')
    expect(screen.queryByRole('group', { name: 'Start a new agent' })).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: 'Cloned Agent renamed' } })
    await saveFromHeader()
    await waitFor(() => expect(serviceMocks.updateCustomAgent).toHaveBeenCalledTimes(1))
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
  }, 15000)

  it('refreshes once to resolve a cloned initial custom agent id created after initial load', async () => {
    const existing = buildCustomAgent({ id: 'aaaaaaaa-1111-1111-1111-111111111111', name: 'Existing Agent' })
    const cloned = buildCustomAgent({ id: 'bbbbbbbb-2222-2222-2222-222222222222', name: 'Cloned Agent' })
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates: [], group_options: groupOptions })
    serviceMocks.listCustomAgents
      .mockResolvedValueOnce({ custom_agents: [existing], total: 1 })
      .mockResolvedValueOnce({ custom_agents: [existing, cloned], total: 2 })

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={cloned.id} />)

    await waitFor(() => expect(serviceMocks.listCustomAgents).toHaveBeenCalledTimes(2))
    await waitForHeaderName('Cloned Agent')
  })

  it('does not auto-select an unrelated custom agent when no template-aligned agent exists', async () => {
    const unrelated = buildCustomAgent({ template_source: 'disease', name: 'Disease Agent' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [unrelated], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')
    expect(screen.getByText('Template: Gene Specialist · Not saved yet')).toBeInTheDocument()
  })

  it('opens a saved agent from the Open dialog and deletes it from the header menu', async () => {
    const existing = buildCustomAgent({ name: 'Saved One' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.deleteCustomAgent.mockImplementation(async () => {
      serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [], total: 0 })
    })

    render(<PromptWorkshop catalog={buildCatalog()} />)
    await screen.findByRole('group', { name: 'Start a new agent' })

    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog', { name: 'Open agent' })
    fireEvent.click(await within(dialog).findByText('Saved One'))
    await waitForHeaderName('Saved One')
    expect(screen.queryByRole('dialog', { name: 'Open agent' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete agent' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete agent?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(serviceMocks.deleteCustomAgent).toHaveBeenCalledWith(existing.id))
    expect(await screen.findByRole('group', { name: 'Start a new agent' })).toBeInTheDocument()
  }, 15000)

  it('prompts before New and Open when the draft has unsaved edits', async () => {
    const existing = buildCustomAgent({ name: 'Saved One' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('Saved One')
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'edited' } })

    fireEvent.click(screen.getByRole('button', { name: 'New' }))
    const guard = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
    fireEvent.click(within(guard).getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByLabelText('Description')).toHaveValue('edited')

    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    const openDialog = await screen.findByRole('dialog', { name: 'Open agent' })
    fireEvent.click(await within(openDialog).findByText('Saved One'))
    const guardAgain = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
    fireEvent.click(within(guardAgain).getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument())
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Open agent' })).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByLabelText('Description')).toHaveValue('edited')

    fireEvent.click(screen.getByRole('button', { name: 'New' }))
    const guardThird = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
    fireEvent.click(within(guardThird).getByRole('button', { name: 'Discard' }))
    expect(await screen.findByRole('group', { name: 'Start a new agent' })).toBeInTheDocument()
  }, 15000)

  it('lets the page leave a clean draft at once through the leave guard', async () => {
    const existing = buildCustomAgent({ name: 'Saved One' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    const guardRef = createRef<WorkshopLeaveGuard>()

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} leaveGuardRef={guardRef} />)
    await waitForHeaderName('Saved One')

    await expect(guardRef.current!.requestLeave()).resolves.toBe(true)
    expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument()
  }, 15000)

  it('asks before the page leaves a dirty draft and answers with the curator choice', async () => {
    const existing = buildCustomAgent({ name: 'Saved One' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    const guardRef = createRef<WorkshopLeaveGuard>()

    const { container } = render(
      <PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} leaveGuardRef={guardRef} />
    )
    await waitForHeaderName('Saved One')
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'edited' } })

    let keepEditing: Promise<boolean> | undefined
    act(() => {
      keepEditing = guardRef.current!.requestLeave()
    })
    const guard = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
    fireEvent.click(within(guard).getByRole('button', { name: 'Keep editing' }))
    await expect(keepEditing).resolves.toBe(false)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByLabelText('Description')).toHaveValue('edited')
    expect(container.contains(document.activeElement)).toBe(true)

    let discard: Promise<boolean> | undefined
    act(() => {
      discard = guardRef.current!.requestLeave()
    })
    const guardAgain = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
    fireEvent.click(within(guardAgain).getByRole('button', { name: 'Discard' }))
    await expect(discard).resolves.toBe(true)
  }, 15000)

  it('blocks page close only while the draft has unsaved edits', async () => {
    const existing = buildCustomAgent({ name: 'Saved One' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('Saved One')

    const cleanEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(cleanEvent)
    expect(cleanEvent.defaultPrevented).toBe(false)

    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'edited' } })
    const dirtyEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirtyEvent)
    expect(dirtyEvent.defaultPrevented).toBe(true)
  }, 15000)

  it('Save As clones the saved agent and does not clear an unchanged output contract', async () => {
    const existing = buildCustomAgent()
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('My Agent')
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /Save as/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Save as a new agent' })
    fireEvent.change(within(dialog).getByLabelText('Agent name'), { target: { value: 'Saved copy' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save as' }))
    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledOnce())
    const payload = serviceMocks.createCustomAgent.mock.calls[0][0]
    expect(payload).toMatchObject({ clone_source_agent_id: existing.agent_id,
      clone_source_updated_at: existing.updated_at, name: 'Saved copy' })
    expect(payload).not.toHaveProperty('output_schema_key')
  }, 15000)

  // ── Versions ──

  it('lists complete configurations and restores the exact revision with an expected-head guard', async () => {
    const existing = buildCustomAgent()
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.listAgentExecutionRevisions.mockResolvedValue({ revisions: [buildVersion(1), buildVersion(2)], next_before_revision: null })
    serviceMocks.restoreAgentExecutionRevision.mockResolvedValue(existing)

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('My Agent')
    gotoSection('Versions')

    const table = await screen.findByRole('table', { name: 'Version history' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('v2')
    expect(rows[0]).toHaveTextContent('Current')
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Restore configuration 1' }))
    const confirm = await screen.findByRole('dialog', { name: 'Restore configuration 1?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Restore' }))

    await waitFor(() => expect(serviceMocks.restoreAgentExecutionRevision).toHaveBeenCalledWith(existing.id, 'version-1', 'version-2'))
    expect(await screen.findByText('Restored configuration 1 as a new version')).toBeInTheDocument()
  }, 15000)

  // ── Prompt layers and groups ──

  it('loads older saved configurations and refreshes history after a restore', async () => {
    const existing = buildCustomAgent()
    const restored = buildCustomAgent({ execution_revision_id: 'version-3', updated_at: '2026-02-24T00:00:00Z' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.listAgentExecutionRevisions.mockRejectedValueOnce(new Error('History temporarily unavailable'))
      .mockResolvedValueOnce({ revisions: [buildVersion(2)], next_before_revision: 2 })
      .mockRejectedValueOnce(new Error('Older history temporarily unavailable'))
      .mockResolvedValueOnce({ revisions: [buildVersion(1)], next_before_revision: null })
      .mockResolvedValue({ revisions: [buildVersion(3), buildVersion(2), buildVersion(1)], next_before_revision: null })
    serviceMocks.restoreAgentExecutionRevision.mockResolvedValue(restored)
    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('My Agent')
    gotoSection('Versions')
    fireEvent.click(await screen.findByRole('button', { name: 'Retry loading configurations' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Load older configurations' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Retry loading configurations' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Restore configuration 1' }))
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [restored], total: 1 })
    const confirm = await screen.findByRole('dialog', { name: 'Restore configuration 1?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(serviceMocks.listAgentExecutionRevisions).toHaveBeenCalledWith(existing.id, 2))
    await waitFor(() => {
      const rows = within(screen.getByRole('table', { name: 'Version history' })).getAllByRole('row')
      expect(rows[1]).toHaveTextContent('v3')
      expect(rows[1]).toHaveTextContent('Current')
    })
  }, 15000)

  it('keeps unsaved edits when a curator cancels restoration', async () => {
    const existing = buildCustomAgent()
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.listAgentExecutionRevisions.mockResolvedValue({ revisions: [buildVersion(2), buildVersion(1)], next_before_revision: null })
    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('My Agent')
    fireEvent.change(screen.getByRole('textbox', { name: /Agent name/ }), { target: { value: 'Unsaved name' } })
    gotoSection('Versions')
    fireEvent.click(await screen.findByRole('button', { name: 'Restore configuration 1' }))
    const discard = await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })
    fireEvent.click(within(discard).getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument())
    expect(serviceMocks.restoreAgentExecutionRevision).not.toHaveBeenCalled()
    gotoSection('Setup')
    expect(screen.getByRole('textbox', { name: /Agent name/ })).toHaveValue('Unsaved name')
  }, 15000)

  it('shows locked inherited layers read-only inside the Prompt section', async () => {
    render(<PromptWorkshop catalog={buildCatalogWithPromptLayers()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    gotoSection('Prompt')
    expect(screen.getByLabelText('Your prompt')).toHaveValue('')

    fireEvent.click(screen.getByRole('button', { name: /^Built-in, read-only/ }))
    expect(screen.getByRole('region', { name: 'Built-in layer, read-only' })).toHaveTextContent('Locked core contract')
    fireEvent.click(screen.getByRole('button', { name: /^Output structure, read-only/ }))
    expect(screen.getByRole('region', { name: 'Output structure layer, read-only' })).toHaveTextContent('Locked generated contract')
    fireEvent.click(screen.getByRole('button', { name: /^Template, read-only/ }))
    expect(screen.getByText('Read-only. Template instructions come from Gene Specialist.')).toBeInTheDocument()

    expect(screen.queryByRole('button', { name: /Reference/ })).not.toBeInTheDocument()
    expect(screen.queryByText('Main / base prompt')).not.toBeInTheDocument()
  }, 15000)

  it('clearly marks existing main prompts that need copied-core review', async () => {
    const flaggedAgent = buildCustomAgent({
      custom_prompt: 'Partial Platform Runtime Contract prose with local curator edits.',
      custom_prompt_overlay_status: 'needs_review',
      custom_prompt_warning: 'Custom-agent prompt contains locked/core prompt markers but did not match exact parent layers for safe cleanup.',
    })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [flaggedAgent], total: 1 })

    render(<PromptWorkshop catalog={buildCatalogWithPromptLayers()} initialCustomAgentId={flaggedAgent.id} />)
    await waitForHeaderName('My Agent')

    gotoSection('Prompt')
    expect(screen.getByRole('alert')).toHaveTextContent(/Custom-agent prompt contains locked\/core prompt markers/)
    expect(screen.getByLabelText('Your prompt')).toHaveValue('Partial Platform Runtime Contract prose with local curator edits.')
  }, 15000)

  it('resets the main prompt to the template text', async () => {
    render(<PromptWorkshop catalog={buildCatalogWithPromptLayers()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')
    gotoSection('Prompt')

    fireEvent.change(screen.getByLabelText('Your prompt'), { target: { value: 'Rewritten' } })
    expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes')
    fireEvent.click(screen.getAllByRole('button', { name: 'Reset to template' })[0])
    expect(screen.getByLabelText('Your prompt')).toHaveValue('')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  }, 15000)

  it('shows only the selected template group options and resets invalid selections when switching templates', async () => {
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates: multiTemplateOptions, group_options: groupOptions })

    render(<PromptWorkshop catalog={buildCatalogWithTemplateSpecificGroupRules()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    await assertGroupOptions(['FB', 'WB'], ['MGI'])

    gotoSection('Setup')
    await selectOption('Template', 'Disease Specialist')
    await waitForHeaderName('Disease Specialist (Custom)')

    await assertGroupOptions(['WB'], ['FB', 'MGI'])
    expect(within(groupPicker()).getByRole('button', { name: /^WB/ })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText('Select a group to see or edit its instructions.')).toBeInTheDocument()
  }, 30000)

  it('uses template group rules in template mode and clone-source group rules in clone mode', async () => {
    const existingCloneSource = buildCustomAgent({
      id: '22222222-2222-2222-2222-222222222222',
      agent_id: 'ca_22222222-2222-2222-2222-222222222222',
      name: 'Disease Agent',
      template_source: 'disease',
    })
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates: multiTemplateOptions, group_options: groupOptions })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existingCloneSource], total: 1 })

    render(<PromptWorkshop catalog={buildCatalogWithTemplateSpecificGroupRules()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    await assertGroupOptions(['FB', 'WB'], ['MGI'])

    gotoSection('Setup')
    fireEvent.click(screen.getByRole('button', { name: 'Clone' }))
    await waitForHeaderName('Disease Agent (Copy)')
    expect(screen.getByText('Cloned from Disease Agent · Not saved yet')).toBeInTheDocument()

    await assertGroupOptions(['WB'], ['FB', 'MGI'])
  }, 30000)

  it('uses the selected custom agent template group rules when editing an existing agent', async () => {
    const templateAlignedCloneSource = buildCustomAgent({ name: 'Gene Agent', template_source: 'gene' })
    const selectedExistingAgent = buildCustomAgent({
      id: '33333333-3333-3333-3333-333333333333',
      agent_id: 'ca_33333333-3333-3333-3333-333333333333',
      name: 'Disease Override Agent',
      template_source: 'disease',
    })
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates: multiTemplateOptions, group_options: groupOptions })
    serviceMocks.listCustomAgents.mockResolvedValue({
      custom_agents: [templateAlignedCloneSource, selectedExistingAgent],
      total: 2,
    })

    render(
      <PromptWorkshop
        catalog={buildCatalogWithTemplateSpecificGroupRules()}
        initialCustomAgentId={selectedExistingAgent.id}
      />
    )
    await waitForHeaderName('Disease Override Agent')
    await assertGroupOptions(['WB'], ['FB', 'MGI'])
  }, 15000)

  it('switches from editing to clone mode using the selected clone source template group rules', async () => {
    const existingGeneAgent = buildCustomAgent({
      id: '44444444-4444-4444-4444-444444444444',
      agent_id: 'ca_44444444-4444-4444-4444-444444444444',
      name: 'Gene Agent',
      template_source: 'gene',
    })
    const diseaseCloneSource = buildCustomAgent({
      id: '55555555-5555-5555-5555-555555555555',
      agent_id: 'ca_55555555-5555-5555-5555-555555555555',
      name: 'Disease Agent',
      template_source: 'disease',
    })
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates: multiTemplateOptions, group_options: groupOptions })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existingGeneAgent, diseaseCloneSource], total: 2 })

    render(<PromptWorkshop catalog={buildCatalogWithTemplateSpecificGroupRules()} initialCustomAgentId={existingGeneAgent.id} />)
    await waitForHeaderName('Gene Agent')
    await assertGroupOptions(['FB', 'WB'], ['MGI'])

    gotoSection('Setup')
    fireEvent.click(screen.getByRole('button', { name: 'Clone' }))
    await selectOption('Clone source', 'Disease Agent')
    await waitForHeaderName('Disease Agent (Copy)')

    await assertGroupOptions(['WB'], ['FB', 'MGI'])
  }, 30000)

  it('infers logged-in group from provider groups when resolved groups are empty', async () => {
    authMocks.user = { ...authMocks.user, groups: [], providerGroups: ['zfin-curators'] }
    const catalog = buildCatalogWithPromptLayers()
    catalog.categories[0].agents[0].group_rules.ZFIN = { group_id: 'ZFIN', content: 'ZFIN template prompt', source_file: 'database' }
    catalog.available_groups = ['WB', 'ZFIN']

    render(<PromptWorkshop catalog={catalog} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    await waitFor(() => {
      expect(within(groupPicker()).getByRole('button', { name: /^ZFIN/ })).toHaveAttribute('aria-pressed', 'true')
    }, { timeout: 5000 })
    expect(screen.getByText(/You are logged in as Doug Howe \(ZFIN\)\. All groups use the template text\./)).toBeInTheDocument()
  }, 15000)

  // ── Setup: envelope, model guidance, missing template ──

  it('shows the envelope as one line with a working View envelope link', async () => {
    serviceMocks.fetchAgentTemplates.mockResolvedValue({ templates: templates.map((template) => ({ ...template, output_schema_key: 'gene', output_contract: { output_state: 'structured_extraction', output_mode: 'domain', output_schema_key: 'gene' } })), group_options: groupOptions })
    metadataMocks.agents = {
      gene: {
        name: 'Gene Specialist',
        icon: 'G',
        category: 'Validation',
        output_schema_key: 'gene',
        domain_envelope: buildDomainEnvelopeMetadata(),
      },
    }
    const onViewEnvelope = vi.fn()

    render(<PromptWorkshop catalog={buildCatalog()} onViewEnvelope={onViewEnvelope} />)
    await startFromTemplate()

    expect(
      await screen.findByText('Validation findings on Gene mention evidence objects · 1 automatic check')
    ).toBeInTheDocument()
    expect(screen.queryByText('Envelope & Validation')).not.toBeInTheDocument()
    expect(screen.queryByText('Gene Validated Reference Domain Pack')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'View envelope' }))
    expect(onViewEnvelope).toHaveBeenCalledWith('gene')
    fireEvent.click(screen.getByRole('radio', { name: 'No structured output' }))
    expect(screen.queryByRole('button', { name: 'View envelope' })).not.toBeInTheDocument()
  }, 15000)

  it('opens a model-selection guidance request with AI Chat from the model helper line', async () => {
    const onVerifyRequest = vi.fn()
    render(<PromptWorkshop catalog={buildCatalog()} onVerifyRequest={onVerifyRequest} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    const disclosure = screen.getByRole('button', { name: 'Model guidance' })
    fireEvent.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Use for validation, lookups, utilities, and iterative drafting.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Confused about models/ })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Ask AI Chat which model fits' }))
    expect(onVerifyRequest).toHaveBeenCalledTimes(1)
    const request = onVerifyRequest.mock.calls[0][0]
    expect(request).toContain('Help me choose the best model settings')
    expect(request).toContain('gpt-5.6-sol')
    expect(request).toContain('gpt-5.6-terra')
    expect(request).not.toContain('gpt-5.5')
  }, 15000)

  it('flags an agent whose template is no longer installed', async () => {
    const orphan = buildCustomAgent({ name: 'Legacy Agent', template_source: 'phenotype_legacy' })
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [orphan], total: 1 })

    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={orphan.id} />)
    await waitForHeaderName('Legacy Agent')

    expect(await screen.findByText('Template: phenotype_legacy (no longer available)')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('The template this agent was built from is no longer installed.')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  }, 15000)

  // ── Tools ──

  it('attaches tools through the library dialog and lists policy-disabled tools with the reason', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    gotoSection('Tools')
    const table = screen.getByRole('table', { name: 'Attached tools' })
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Add tools' }))

    const dialog = await screen.findByRole('dialog', { name: /Add tools/ })
    expect(dialog).toHaveTextContent('1 attached · 2 available')
    const blocked = within(dialog).getByRole('checkbox', { name: /admin_only_tool/ })
    expect(blocked).toHaveAttribute('aria-disabled', 'true')
    expect(dialog).toHaveTextContent('Disabled by policy for custom agents: Restricted')

    fireEvent.mouseDown(within(dialog).getByRole('combobox', { name: 'Category' }))
    fireEvent.click(await screen.findByRole('option', { name: 'External API' }))
    expect(within(dialog).queryByText('search_document')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('checkbox', { name: /chebi_lookup/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Attach 1 tool' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(within(screen.getByRole('table', { name: 'Attached tools' })).getAllByRole('row').slice(1)).toHaveLength(2)
    const nav = screen.getByRole('navigation', { name: 'Agent Workshop sections' })
    expect(within(nav).getByRole('button', { name: 'Tools, 2 attached, unsaved edits' })).toBeInTheDocument()
  }, 25000)

  it('submits tool requests to developers with the AI Chat conversation attached', async () => {
    const opusConversation = [
      { role: 'user' as const, content: 'I need a GO enrichment helper', timestamp: '2026-02-23T01:00:00Z' },
      { role: 'assistant' as const, content: 'What should the output look like?', timestamp: '2026-02-23T01:00:05Z' },
    ]
    serviceMocks.listToolIdeaRequests
      .mockResolvedValueOnce({ tool_ideas: [], total: 0 })
      .mockResolvedValueOnce({
        tool_ideas: [{
          id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
          user_id: 1,
          title: 'Need GO relationship enrichment tool',
          description: 'd',
          opus_conversation: [],
          status: 'submitted',
          created_at: '2026-02-23T00:00:00Z',
          updated_at: '2026-02-23T00:00:00Z',
        }],
        total: 1,
      })

    render(<PromptWorkshop catalog={buildCatalog()} opusConversation={opusConversation} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    gotoSection('Tools')
    fireEvent.click(screen.getByRole('button', { name: 'New request' }))
    const dialog = await screen.findByRole('dialog', { name: 'New request to developers' })
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Need GO relationship enrichment tool' } })
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: 'Add a tool that returns expanded GO relationships for a term.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))

    await waitFor(() => expect(serviceMocks.submitToolIdeaRequest).toHaveBeenCalledTimes(1))
    expect(serviceMocks.submitToolIdeaRequest).toHaveBeenCalledWith({
      title: 'Need GO relationship enrichment tool',
      description: 'Add a tool that returns expanded GO relationships for a term.',
      opus_conversation: opusConversation,
    })
    const list = await screen.findByRole('list', { name: 'Requests to developers' })
    expect(list).toHaveTextContent('Need GO relationship enrichment tool')
    expect(list).toHaveTextContent('New')
  }, 25000)

  // ── AI Chat handoffs ──

  it('opens a draft discussion request from the navigation Help group', async () => {
    const onVerifyRequest = vi.fn()
    render(<PromptWorkshop catalog={buildCatalog()} onVerifyRequest={onVerifyRequest} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    fireEvent.click(screen.getByRole('button', { name: 'Ask AI Chat' }))
    expect(onVerifyRequest).toHaveBeenCalledTimes(1)
    expect(onVerifyRequest.mock.calls[0][0]).toContain('inspect current prompt/tool schemas')
    expect(onVerifyRequest.mock.calls[0][0]).toContain('read_chunk span IDs')
    expect(onVerifyRequest.mock.calls[0][0]).toContain('record_evidence(span_ids)')
  }, 15000)

  it('opens a system-prompt discussion request with AI Chat', async () => {
    const onVerifyRequest = vi.fn()
    render(<PromptWorkshop catalog={buildCatalog()} onVerifyRequest={onVerifyRequest} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    gotoSection('Prompt')
    fireEvent.click(screen.getByRole('button', { name: 'Discuss prompt changes with AI Chat' }))
    expect(onVerifyRequest).toHaveBeenCalledTimes(1)
    expect(onVerifyRequest.mock.calls[0][0]).toContain('Help me improve the SYSTEM PROMPT')
    expect(onVerifyRequest.mock.calls[0][0]).toContain('record_evidence(span_ids)')
  })

  it('hides the AI Chat entry points when no handoff is available', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')
    expect(screen.queryByRole('button', { name: 'Ask AI Chat' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask AI Chat which model fits' })).not.toBeInTheDocument()
  })

  it.each([undefined, 'gene'])('applies a complete reviewed proposal without saving and supports Undo (template %s)', async (templateId) => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    serviceMocks.validateWorkshopDraft.mockResolvedValue({ valid: true, findings: [] })
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId={templateId} authoringContextRef={handle} />)
    await waitFor(() => expect(handle.current?.captureAuthoringContext().draft_model_id).toBeTruthy())
    if (templateId) await waitForHeaderName('Gene Specialist (Custom)')
    const base = handle.current!.captureAuthoringContext()
    // The compiler fills the configured default when select_model omits reasoning.
    const candidate = {
      ...base, draft_name: 'Reviewed reader', draft_description: 'Reviewed description',
      prompt_draft: 'Read evidence.', draft_model_id: 'gpt-5.6-sol', draft_model_reasoning: 'medium',
    }
    const result = await act(async () => handle.current!.applyAuthoringProposal({
      contract_version: 'workshop_authoring_proposal.v1',
      base_draft_fingerprint: await fingerprintWorkshopDraft(base),
      candidate_draft_fingerprint: await fingerprintWorkshopDraft(candidate),
      candidate, change_summary: 'Rename the reader', diff: [], findings: [],
    }))
    expect(result.applied).toBe(true)
    expect(handle.current!.captureAuthoringContext().draft_name).toBe('Reviewed reader')
    expect(handle.current!.captureAuthoringContext().draft_model_reasoning).toBe('medium')
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
    expect(serviceMocks.updateCustomAgent).not.toHaveBeenCalled()
    expect(serviceMocks.setCustomAgentVisibility).not.toHaveBeenCalled()
    expect(serviceMocks.validateWorkshopDraft.mock.calls.map((call) => call[1])).toEqual(['pre_apply', 'post_apply'])
    fireEvent.click(screen.getByRole('button', { name: 'Undo AI changes' }))
    expect(handle.current!.captureAuthoringContext().draft_name).toBe(base.draft_name)
  })

  it('applies a profile candidate through the shared adapter and undoes it without saving', async () => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    serviceMocks.validateWorkshopDraft.mockResolvedValue({ valid: true, findings: [] })
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    const base = handle.current!.captureAuthoringContext()
    const candidate = structuredClone(base)
    candidate.draft_output = { mode: 'profile_bound_generic', schemaKey: '', profilePin: null,
      profileContract: { name: 'Collected details', semantic_class: 'item', fields: [
        { key: 'paper_labels', required: true, nullable: false, source_labels: ['Paper names'], value_schema: { kind: 'array', items: { kind: 'string' } } },
      ] } }
    candidate.draft_output_schema_key = undefined
    const proposal = { contract_version: 'workshop_authoring_proposal.v1' as const,
      base_draft_fingerprint: await fingerprintWorkshopDraft(base), candidate_draft_fingerprint: await fingerprintWorkshopDraft(candidate),
      candidate, change_summary: 'Collect names', diff: [], findings: [] }
    const applied = await act(async () => handle.current!.applyAuthoringProposal(proposal))
    expect(applied.applied).toBe(true)
    expect(handle.current!.captureAuthoringContext().draft_output).toEqual(candidate.draft_output)
    expect(screen.getByRole('button', { name: /Output Structure, unsaved edits/ })).toBeInTheDocument()
    const stale = await act(async () => handle.current!.applyAuthoringProposal(proposal))
    expect(stale.applied).toBe(false)
    expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
    expect(serviceMocks.updateCustomAgent).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Undo AI changes' }))
    expect(handle.current!.captureAuthoringContext().draft_output).toEqual(base.draft_output)
    expect(candidate.draft_output.profileContract!.fields[0].source_labels).toEqual(['Paper names'])
  })

  it('saves an AI-selected exact profile pin without silently cloning its structure', async () => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    serviceMocks.validateWorkshopDraft.mockResolvedValue({ valid: true, findings: [] })
    const pin = { profile_id: 'shared-profile', profile_revision_id: 'shared-revision', revision: 3, fingerprint: 'sha256:exact' }
    const contract = { name: 'Shared details', semantic_class: 'item', fields: [] }
    const revision = { id: pin.profile_revision_id, profile_id: pin.profile_id, revision: pin.revision, fingerprint: pin.fingerprint, contract }
    profileMocks.getGenericProfile.mockResolvedValue({ profile: { id: pin.profile_id }, revision, can_edit: false })
    profileMocks.getGenericProfileRevision.mockResolvedValue(revision)
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    const base = handle.current!.captureAuthoringContext()
    const candidate = structuredClone(base)
    candidate.draft_output = { mode: 'profile_bound_generic', schemaKey: '', profilePin: pin, profileContract: contract }
    candidate.draft_output_schema_key = undefined
    const result = await act(async () => handle.current!.applyAuthoringProposal({
      contract_version: 'workshop_authoring_proposal.v1', candidate, change_summary: 'Reuse the shared structure', diff: [], findings: [],
      base_draft_fingerprint: await fingerprintWorkshopDraft(base), candidate_draft_fingerprint: await fingerprintWorkshopDraft(candidate),
    }))
    expect(result.applied).toBe(true)
    expect(profileMocks.getGenericProfileRevision).toHaveBeenCalledWith(pin.profile_id, 3)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalled())
    const payload = serviceMocks.createCustomAgent.mock.calls[0][0]
    expect(payload.output_contract.generic_profile_ref).toEqual(pin)
    expect(payload.new_generic_profile).toBeUndefined()
    expect(payload.revise_generic_profile).toBeUndefined()
  })

  it('rejects stale proposals and rolls back post-apply validation failure', async () => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    const base = handle.current!.captureAuthoringContext()
    const candidate = { ...base, draft_name: 'Rejected reader' }
    const proposal = {
      contract_version: 'workshop_authoring_proposal.v1' as const,
      base_draft_fingerprint: 'sha256:stale',
      candidate_draft_fingerprint: await fingerprintWorkshopDraft(candidate),
      candidate, change_summary: 'Rename', diff: [], findings: [],
    }
    const stale = await act(async () => handle.current!.applyAuthoringProposal(proposal))
    expect(stale.applied).toBe(false)
    expect(serviceMocks.validateWorkshopDraft).not.toHaveBeenCalled()
    serviceMocks.validateWorkshopDraft
      .mockResolvedValueOnce({ valid: true, findings: [] })
      .mockResolvedValueOnce({ valid: false, findings: [] })
    proposal.base_draft_fingerprint = await fingerprintWorkshopDraft(base)
    const invalid = await act(async () => handle.current!.applyAuthoringProposal(proposal))
    expect(invalid.applied).toBe(false)
    expect(handle.current!.captureAuthoringContext().draft_name).toBe(base.draft_name)
    expect(screen.queryByRole('button', { name: 'Undo AI changes' })).not.toBeInTheDocument()
  })

  it('locks the draft while validation is pending and leaves it unchanged on rejection', async () => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    const validation = createDeferred<{ valid: boolean; findings: [] }>()
    serviceMocks.validateWorkshopDraft.mockReturnValueOnce(validation.promise)
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    const base = handle.current!.captureAuthoringContext()
    const candidate = { ...base, draft_name: 'Proposed name' }
    const proposal = {
      contract_version: 'workshop_authoring_proposal.v1' as const,
      base_draft_fingerprint: await fingerprintWorkshopDraft(base),
      candidate_draft_fingerprint: await fingerprintWorkshopDraft(candidate),
      candidate, change_summary: 'Rename', diff: [], findings: [],
    }
    let applying!: ReturnType<WorkshopAuthoringContextHandle['applyAuthoringProposal']>
    act(() => { applying = handle.current!.applyAuthoringProposal(proposal) })
    await waitFor(() => expect(serviceMocks.validateWorkshopDraft).toHaveBeenCalled())
    expect(screen.getByRole('group', { name: 'Workshop draft' })).toBeDisabled()
    await act(async () => validation.resolve({ valid: false, findings: [] }))
    expect((await applying).applied).toBe(false)
    expect(handle.current!.captureAuthoringContext().draft_name).toBe(base.draft_name)
  })

  it('edits a custom Output Structure in the authoritative draft and saves without JSON', async () => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const saving = createDeferred<never>()
    serviceMocks.createCustomAgent.mockReturnValueOnce(saving.promise)
    profileMocks.validateGenericProfile.mockResolvedValue({ fingerprint: 'validated' })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    const leave = createRef<WorkshopLeaveGuard>()
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} leaveGuardRef={leave} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    const before = handle.current!.captureAuthoringContext()
    fireEvent.click(screen.getByRole('radio', { name: 'Structured extraction' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit Output Structure' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Structure name/ }), { target: { value: 'Collected details' } })
    fireEvent.change(screen.getByRole('textbox', { name: /Record class/ }), { target: { value: 'collected_detail' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }))
    const after = handle.current!.captureAuthoringContext()
    expect(after.draft_output?.profileContract?.name).toBe('Collected details')
    expect(after.draft_output?.profileContract?.fields).toHaveLength(1)
    expect(await fingerprintWorkshopDraft(after)).not.toBe(await fingerprintWorkshopDraft(before))
    expect(screen.getByRole('button', { name: /Output Structure, unsaved edits/ })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /JSON/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledWith(expect.objectContaining({
      new_generic_profile: expect.objectContaining({ name: 'Collected details', semantic_class: 'collected_detail' }),
    })))
    expect(profileMocks.validateGenericProfile).toHaveBeenCalled()
    const typeSelect = screen.getByRole('combobox', { name: 'Value kind', hidden: true })
    expect(typeSelect).toHaveAttribute('aria-disabled', 'true')
    fireEvent.mouseDown(typeSelect)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(handle.current!.captureAuthoringContext().draft_output).toEqual(after.draft_output)
    await act(async () => saving.reject(new Error('Save unavailable')))
    await waitFor(() => expect(typeSelect).not.toHaveAttribute('aria-disabled', 'true'))
  })

  it('carries a manually mapped validator through authoritative context, validation and Save without JSON', async () => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const capabilityRef = { package_id: 'example', package_version: '1', domain_pack_id: 'record', domain_pack_version: '1', binding_id: 'lookup' }
    profileMocks.getProfileMappingOptions.mockResolvedValue({
      fields: [{ path: 'attributes.new_field', display_name: 'New field', value_schema: { kind: 'string' }, required: false, nullable: false, array_domains: [] }],
      capabilities: [{ capability_ref: capabilityRef, fingerprint: 'sha256:exact', state: 'active', selectable: true, diagnostics: [],
        input_paths: { mention: ['attributes.new_field'] }, output_paths: {},
        metadata: { validator_binding_id: 'lookup', display_name: 'Identifier lookup',
          custom_profile_reuse: { enabled: true,
            inputs: { mention: { value_schema: { kind: 'string' }, required: true, nullable: false, allow_field: true, allow_constant: false, context_selector: null } },
            outputs: {}, policy: { unresolved_default: 'requires_curator_review', unresolved_allowed: ['requires_curator_review'], readiness_default: false, readiness_allowed: [false] },
            required_any_inputs: [], supports_whole_array: false, supports_element_fanout: false, requires_evidence: false, provider_input_slots: {},
          },
        },
      }], next_cursor: null,
    })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    fireEvent.click(screen.getByRole('radio', { name: 'Structured extraction' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit Output Structure' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Structure name/ }), { target: { value: 'Mapped details' } })
    fireEvent.change(screen.getByRole('textbox', { name: /Record class/ }), { target: { value: 'record' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add field' }))
    const before = handle.current!.captureAuthoringContext()
    fireEvent.click(screen.getByRole('button', { name: 'Find compatible validators' }))
    fireEvent.mouseDown(await screen.findByRole('combobox', { name: 'Find validators for canonical field' }))
    fireEvent.click(screen.getByRole('option', { name: 'New field · attributes.new_field' }))
    fireEvent.click(screen.getByRole('button', { name: 'Map field to mention · Identifier lookup' }))
    const mapped = handle.current!.captureAuthoringContext()
    expect(mapped.draft_output?.profileContract?.validator_mappings).toEqual([{
      mapping_id: 'validator_1', capability_ref: capabilityRef, capability_fingerprint: 'sha256:exact',
      inputs: { mention: { source: 'field', field_path: 'attributes.new_field' } }, outputs: {}, mode: 'whole',
      policy: { unresolved: 'requires_curator_review', blocks_readiness: false },
    }])
    expect(await fingerprintWorkshopDraft(mapped)).not.toBe(await fingerprintWorkshopDraft(before))
    expect(screen.queryByRole('textbox', { name: /JSON/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledWith(expect.objectContaining({
      new_generic_profile: mapped.draft_output!.profileContract,
    })))
    expect(profileMocks.validateGenericProfile).toHaveBeenCalledWith(mapped.draft_output!.profileContract)
  })

  it('saves a manually selected packaged builder through the authoritative Workshop draft', async () => {
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
    const saving = createDeferred<never>()
    serviceMocks.createCustomAgent.mockReturnValueOnce(saving.promise)
    const ref = { package_id: 'fixture.package', agent_id: 'builder', domain_pack_id: 'fixture.domain' }
    metadataMocks.agents = { builder: { name: 'Packaged builder', icon: '', category: 'Extraction', output_schema_key: null, domain_extraction_ref: ref } }
    const handle = createRef<WorkshopAuthoringContextHandle>()
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    const before = handle.current!.captureAuthoringContext()
    fireEvent.click(screen.getByRole('radio', { name: 'Structured extraction' }))
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Output format' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Packaged domain format' }))
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Domain format' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Packaged builder — support details unavailable' }))
    const after = handle.current!.captureAuthoringContext()
    expect(after.draft_output?.domainExtractionRef).toEqual(ref)
    expect(after.draft_output_schema_key).toBeUndefined()
    expect(after.draft_output?.schemaKey).toBe('')
    expect(await fingerprintWorkshopDraft(after)).not.toBe(await fingerprintWorkshopDraft(before))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledWith(expect.objectContaining({
      output_contract: { output_state: 'structured_extraction', output_mode: 'domain', output_schema_key: null, domain_extraction_ref: ref },
    })))
    expect(profileMocks.validateGenericProfile).not.toHaveBeenCalled()
    for (const name of ['Output format', 'Domain format']) {
      const select = screen.getByRole('combobox', { name, hidden: true })
      expect(select).toHaveAttribute('aria-disabled', 'true')
      fireEvent.mouseDown(select)
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    }
    expect(handle.current!.captureAuthoringContext().draft_output).toEqual(after.draft_output)
    await act(async () => saving.reject(new Error('Save unavailable')))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Output format' })).not.toHaveAttribute('aria-disabled', 'true'))
  })

  it('initializes a builder template from its explicit output contract, not its null schema', async () => {
    const ref = { package_id: 'fixture.package', agent_id: 'gene_builder', domain_pack_id: 'fixture.domain' }
    serviceMocks.fetchAgentTemplates.mockResolvedValue({
      templates: templates.map((template) => ({ ...template, output_schema_key: null,
        output_contract: { output_state: 'structured_extraction', output_mode: 'domain', domain_extraction_ref: ref } })),
      group_options: groupOptions,
    })
    const handle = createRef<WorkshopAuthoringContextHandle>()
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    expect(handle.current!.captureAuthoringContext().draft_output?.domainExtractionRef).toEqual(ref)
    expect(screen.getByRole('radio', { name: 'Structured extraction' })).toBeChecked()
    expect(screen.getByRole('combobox', { name: 'Domain format' })).toHaveTextContent('fixture.domain')
  })

  it.each([true, false])('saves a loaded profile as an exact revision edit only when editable (%s)', async (canEdit) => {
    const existing = buildCustomAgent()
    const pin = { profile_id: 'profile-id', profile_revision_id: 'profile-revision-2', revision: 2, fingerprint: 'sha256:profile' }
    const contract = { name: 'Saved structure', semantic_class: 'record', fields: [] }
    const revision = { id: pin.profile_revision_id, profile_id: pin.profile_id, revision: 2, fingerprint: pin.fingerprint, contract }
    profileMocks.getGenericProfile.mockResolvedValue({ can_edit: canEdit, revision, profile: { id: pin.profile_id } })
    profileMocks.getGenericProfileRevision.mockResolvedValue(revision)
    serviceMocks.listCustomAgents.mockResolvedValue({ custom_agents: [existing], total: 1 })
    serviceMocks.getAgentExecutionRevision.mockImplementation(async () => ({
      ...buildVersion(2), id: existing.execution_revision_id, agent_id: existing.id,
      snapshot: { ...buildVersion(2).snapshot, output_contract: { output_state: 'structured_extraction', output_mode: 'profile_bound_generic', generic_profile_ref: pin } },
    }))
    serviceMocks.updateCustomAgent.mockRejectedValue(new Error('Profile changed since it was opened; compare or reload before saving'))
    render(<PromptWorkshop catalog={buildCatalog()} initialCustomAgentId={existing.id} />)
    await waitForHeaderName('My Agent')
    fireEvent.click(screen.getByRole('button', { name: 'Edit Output Structure' }))
    expect(screen.getByText(canEdit ? /Saving structure changes creates a new revision/ : /Saving structure changes creates your own profile copy/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: /Structure name/ }), { target: { value: 'Edited structure' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save as version/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(serviceMocks.updateCustomAgent).toHaveBeenCalledOnce())
    const payload = serviceMocks.updateCustomAgent.mock.calls[0][1]
    if (canEdit) {
      expect(payload.revise_generic_profile).toEqual({ base: pin, contract: { ...contract, name: 'Edited structure' } })
      expect(payload).not.toHaveProperty('new_generic_profile')
    } else {
      expect(payload.new_generic_profile).toEqual({ ...contract, name: 'Edited structure' })
      expect(payload).not.toHaveProperty('revise_generic_profile')
    }
    expect(payload.expected_revision_id).toBe(existing.execution_revision_id)
    await screen.findByText(/Profile changed since it was opened/)
    expect(await screen.findByRole('textbox', { name: /Structure name/ })).toHaveValue('Edited structure')
  })

  it.each([false, true])('selects an existing profile only while the opening draft is current (stale=%s)', async (stale) => {
    const handle = createRef<WorkshopAuthoringContextHandle>()
    const profile = { id: 'reuse-profile', name: 'Reusable details', head_revision: 3, semantic_class: 'detail' }
    const revision = { id: 'reuse-revision-3', profile_id: profile.id, revision: 3, fingerprint: 'sha256:reuse',
      contract: { name: 'Reusable details', semantic_class: 'detail', fields: [] } }
    profileMocks.listGenericProfiles.mockResolvedValue({ profiles: [profile], next_cursor: null })
    profileMocks.getGenericProfile.mockResolvedValue({ profile, revision, can_edit: false })
    profileMocks.getGenericProfileRevision.mockResolvedValue(revision)
    render(<PromptWorkshop catalog={buildCatalog()} initialParentAgentId="gene" authoringContextRef={handle} />)
    await waitForHeaderName('Gene Specialist (Custom)')
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Keep my purpose' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Structured extraction' }))
    fireEvent.click(screen.getByText('Advanced: reuse a saved structure'))
    fireEvent.click(screen.getByRole('button', { name: 'Choose existing Output Structure' }))
    fireEvent.click(await screen.findByRole('button', { name: /Reusable details · revision 3/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Use this revision' })).toBeEnabled())
    if (stale) fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Changed during selection' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this revision' }))
    if (stale) {
      await screen.findByText(/draft changed while selecting a structure/)
      expect(handle.current!.captureAuthoringContext().draft_description).toBe('Changed during selection')
      expect(handle.current!.captureAuthoringContext().draft_output?.profilePin).toBeNull()
      expect(serviceMocks.createCustomAgent).not.toHaveBeenCalled()
      return
    }
    await screen.findByRole('textbox', { name: /Structure name/ })
    const pin = { profile_id: profile.id, profile_revision_id: revision.id, revision: 3, fingerprint: revision.fingerprint }
    expect(handle.current!.captureAuthoringContext().draft_output?.profilePin).toEqual(pin)
    expect(handle.current!.captureAuthoringContext().draft_description).toBe('Keep my purpose')
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const dialog = await screen.findByRole('dialog', { name: /Save new agent/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(serviceMocks.createCustomAgent).toHaveBeenCalledOnce())
    const payload = serviceMocks.createCustomAgent.mock.calls[0][0]
    expect(payload.output_contract).toEqual({ output_state: 'structured_extraction', output_mode: 'profile_bound_generic', generic_profile_ref: pin })
    expect(payload).not.toHaveProperty('new_generic_profile')
  })

  it('does not expose an Output Schema Key field anywhere in the workshop', async () => {
    render(<PromptWorkshop catalog={buildCatalog()} />)
    await startFromTemplate()
    await waitForHeaderName('Gene Specialist (Custom)')

    for (const section of ['Setup', 'Prompt', 'Tools', 'Versions'] as const) {
      gotoSection(section)
      expect(screen.queryByLabelText(/output schema key/i)).toBeNull()
    }
  }, 15000)
})

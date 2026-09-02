import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { AgentTemplate, CustomAgent, GroupOption, ModelOption } from '@/types/promptExplorer'

import SetupSection, { type SetupSectionProps } from './SetupSection'

const modelOptions: ModelOption[] = [
  {
    model_id: 'gpt-5.6-terra',
    name: 'GPT-5.6 Terra',
    provider: 'openai',
    description: 'fast reasoning model',
    guidance: 'Use for validation and lookups.',
    default: true,
    supports_reasoning: true,
    supports_temperature: false,
    reasoning_options: ['low', 'medium', 'high'],
    default_reasoning: 'medium',
    reasoning_descriptions: { low: 'Fastest', medium: 'Balanced', high: 'Deep' },
    recommended_for: ['Validation'],
    avoid_for: ['Deep adjudication'],
  },
  {
    model_id: 'plain',
    name: 'Plain Model',
    provider: 'openai',
    description: '',
    guidance: '',
    default: false,
    supports_reasoning: false,
    supports_temperature: true,
    reasoning_options: [],
    reasoning_descriptions: {},
    recommended_for: [],
    avoid_for: [],
  },
]

const templates: AgentTemplate[] = [
  { agent_id: 'gene', name: 'Gene Specialist', icon: 'G', model_id: 'gpt-5.6-terra', tool_ids: [], allowed_group_ids: [] },
  { agent_id: 'disease', name: 'Disease Specialist', icon: 'D', model_id: 'gpt-5.6-terra', tool_ids: [], allowed_group_ids: ['ZFIN'] },
]

const groupOptions: GroupOption[] = [
  { group_id: 'ZFIN', name: 'ZFIN' },
  { group_id: 'MGI', name: 'MGI' },
]

const customAgents: CustomAgent[] = [
  {
    id: 'a1',
    agent_id: 'ca_a1',
    user_id: 1,
    template_source: 'gene',
    name: 'Saved agent',
    custom_prompt: 'p',
    group_prompt_overrides: {},
    allowed_group_ids: [],
    inherited_allowed_group_ids: [],
    icon: 'x',
    include_group_rules: true,
    model_id: 'gpt-5.6-terra',
    model_temperature: 0,
    tool_ids: [],
    visibility: 'private',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

function renderSetup(overrides: Partial<SetupSectionProps> = {}) {
  const props: SetupSectionProps = {
    gettingStartedMode: 'template',
    onModeChange: vi.fn(),
    templateOptions: templates,
    parentAgentId: 'gene',
    onTemplateChange: vi.fn(),
    missingTemplateId: null,
    templateAllowedGroupIds: [],
    customAgents,
    cloneSourceAgentId: 'a1',
    onCloneSourceChange: vi.fn(),
    isExistingAgent: false,
    focusOriginToken: 0,
    icon: 'x',
    iconOptions: ['x', 'y'],
    onIconChange: vi.fn(),
    name: 'Gene Specialist (Custom)',
    onNameChange: vi.fn(),
    description: '',
    onDescriptionChange: vi.fn(),
    envelope: null,
    onViewEnvelope: vi.fn(),
    modelOptions,
    selectedModelId: 'gpt-5.6-terra',
    onModelChange: vi.fn(),
    selectedModelOption: modelOptions[0],
    selectedModelReasoning: 'medium',
    onReasoningChange: vi.fn(),
    reasoningDescription: 'Balanced',
    onAskClaudeAboutModels: vi.fn(),
    visibility: 'private',
    onVisibilityChange: vi.fn(),
    allowedGroupIds: [],
    onAllowedGroupIdsChange: vi.fn(),
    selectableGroupOptions: groupOptions,
    inheritedAllowedGroupIds: [],
    ...overrides,
  }
  render(<SetupSection {...props} />)
  return props
}

describe('SetupSection', () => {
  it('renders the starting point toggle and template picker', async () => {
    const props = renderSetup()
    expect(screen.getByRole('group', { name: 'Starting point' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Scratch' }))
    expect(props.onModeChange).toHaveBeenCalledWith('scratch')

    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Template' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Disease Specialist' }))
    expect(props.onTemplateChange).toHaveBeenCalledWith('disease')
  })

  it('shows the clone source picker in clone mode', async () => {
    const props = renderSetup({ gettingStartedMode: 'clone', cloneSourceAgentId: '' })
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Clone source' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Saved agent' }))
    expect(props.onCloneSourceChange).toHaveBeenCalledWith('a1')
  })

  it('explains a package restriction on the chosen template', () => {
    renderSetup({ parentAgentId: 'disease', templateAllowedGroupIds: ['ZFIN', 'MGI'] })
    expect(screen.getByText(/This template is restricted to ZFIN, MGI/)).toBeInTheDocument()
  })

  it('warns when the saved agent names a template that is no longer installed', () => {
    renderSetup({ parentAgentId: 'legacy', missingTemplateId: 'legacy', isExistingAgent: true })
    expect(screen.getByRole('alert')).toHaveTextContent('no longer installed')
    expect(screen.getByRole('combobox', { name: 'Template' })).toHaveTextContent('legacy (no longer available)')
  })

  it('moves focus to the origin selector when asked', () => {
    renderSetup({ focusOriginToken: 1 })
    expect(screen.getByRole('combobox', { name: 'Template' })).toHaveFocus()
  })

  it('edits identity fields', () => {
    const props = renderSetup()
    fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: 'Renamed' } })
    expect(props.onNameChange).toHaveBeenCalledWith('Renamed')
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'About' } })
    expect(props.onDescriptionChange).toHaveBeenCalledWith('About')
  })

  it('renders the envelope as one line with a View envelope link', () => {
    const props = renderSetup({
      envelope: {
        status: 'active',
        producesLabel: 'Validation findings on Disease annotation objects',
        activeChecks: 9,
        underDevelopment: 1,
      },
    })
    expect(
      screen.getByText('Validation findings on Disease annotation objects · 9 automatic checks, 1 under development')
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'View envelope' }))
    expect(props.onViewEnvelope).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Envelope & Validation')).not.toBeInTheDocument()
  })

  it('hides the envelope row when the origin has no envelope', () => {
    renderSetup({ envelope: null })
    expect(screen.queryByText('What it produces')).not.toBeInTheDocument()
  })

  it('shows model and reasoning side by side with the default helper line', async () => {
    const props = renderSetup()
    expect(screen.getByRole('combobox', { name: 'Model' })).toHaveTextContent('GPT-5.6 Terra')
    expect(screen.getByRole('combobox', { name: 'Reasoning' })).toHaveTextContent('Medium')
    expect(screen.getByText(/Medium is the default reasoning for GPT-5.6 Terra\. Balanced\./)).toBeInTheDocument()

    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Reasoning' }))
    fireEvent.click(await screen.findByRole('option', { name: 'High' }))
    expect(props.onReasoningChange).toHaveBeenCalledWith('high')

    fireEvent.click(screen.getByRole('button', { name: 'Ask Claude which model fits' }))
    expect(props.onAskClaudeAboutModels).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(/Confused about models/)).not.toBeInTheDocument()
  })

  it('names the model default when a non-default reasoning is selected', () => {
    renderSetup({ selectedModelReasoning: 'high', reasoningDescription: 'Deep' })
    expect(screen.getByText(/High reasoning selected\. The default for GPT-5.6 Terra is Medium\. Deep\./)).toBeInTheDocument()
  })

  it('omits the reasoning select for models without reasoning', () => {
    renderSetup({ selectedModelId: 'plain', selectedModelOption: modelOptions[1], selectedModelReasoning: '', reasoningDescription: '' })
    expect(screen.queryByRole('combobox', { name: 'Reasoning' })).not.toBeInTheDocument()
  })

  it('keeps provider, guidance, and fit behind the Model guidance disclosure', () => {
    renderSetup()
    const disclosure = screen.getByRole('button', { name: 'Model guidance' })
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Use for validation and lookups.')).not.toBeInTheDocument()

    fireEvent.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Use for validation and lookups.')).toBeInTheDocument()
    expect(screen.getByText(/OPENAI · gpt-5.6-terra/)).toBeInTheDocument()
    expect(screen.getByText('Validation')).toBeInTheDocument()
    expect(screen.getByText('Deep adjudication')).toBeInTheDocument()
  })

  it('renders sharing controls in one row with the one-sentence helper', async () => {
    const props = renderSetup()
    expect(screen.getByText('Sharing sets who can see this agent. Groups restrict who can run it.')).toBeInTheDocument()
    expect(screen.queryByText(/Sharing determines which people or projects/)).not.toBeInTheDocument()

    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Visibility' }))
    fireEvent.click(await screen.findByRole('option', { name: 'Shared with project' }))
    expect(props.onVisibilityChange).toHaveBeenCalledWith('project')

    const groups = screen.getByRole('combobox', { name: 'Available to groups' })
    expect(groups).toHaveTextContent('All groups')
    fireEvent.mouseDown(groups)
    fireEvent.click(await screen.findByRole('option', { name: /MGI/ }))
    expect(props.onAllowedGroupIdsChange).toHaveBeenCalledWith(['MGI'])
  })

  it('shows the inherited access floor as a locked note and blocks widening', async () => {
    const props = renderSetup({
      allowedGroupIds: ['ZFIN'],
      inheritedAllowedGroupIds: ['ZFIN', 'MGI'],
      selectableGroupOptions: groupOptions,
    })
    expect(screen.getByText('Inherits a ZFIN, MGI access floor; you can narrow it, not widen it.')).toBeInTheDocument()

    const groups = screen.getByRole('combobox', { name: 'Available to groups' })
    fireEvent.mouseDown(groups)
    const listbox = await screen.findByRole('listbox')
    expect(within(listbox).queryByRole('option', { name: 'All groups' })).not.toBeInTheDocument()
    // Unchecking the only selected group would empty the list, which the floor forbids.
    fireEvent.click(within(listbox).getByRole('option', { name: /ZFIN/ }))
    expect(props.onAllowedGroupIdsChange).not.toHaveBeenCalled()
  })
})

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { CustomAgent } from '@/types/promptExplorer'

import { DeleteAgentDialog, RevertVersionDialog, SelfExclusionDialog, UnsavedChangesDialog } from './ConfirmDialogs'
import ManageAgentsDialog from './ManageAgentsDialog'
import OpenAgentDialog from './OpenAgentDialog'
import SaveAsDialog from './SaveAsDialog'
import ToolRequestDialog from './ToolRequestDialog'

function buildAgent(overrides: Partial<CustomAgent>): CustomAgent {
  return {
    id: 'a1',
    agent_id: 'ca_a1',
    user_id: 1,
    template_source: 'gene',
    name: 'Agent One',
    description: 'First',
    custom_prompt: 'p',
    group_prompt_overrides: {},
    allowed_group_ids: [],
    inherited_allowed_group_ids: [],
    icon: 'x',
    include_group_rules: true,
    model_id: 'm',
    model_temperature: 0,
    tool_ids: [],
    visibility: 'private',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const agents = [buildAgent({}), buildAgent({ id: 'a2', agent_id: 'ca_a2', name: 'Agent Two', description: undefined })]

describe('OpenAgentDialog', () => {
  it('searches and selects an agent', () => {
    const onSelect = vi.fn()
    render(<OpenAgentDialog open agents={agents} loading={false} selectedAgentId="a1" onSelect={onSelect} onClose={vi.fn()} />)
    const dialog = screen.getByRole('dialog', { name: 'Open agent' })
    fireEvent.change(within(dialog).getByLabelText('Search agents'), { target: { value: 'two' } })
    expect(within(dialog).queryByText('Agent One')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByText('Agent Two'))
    expect(onSelect).toHaveBeenCalledWith('a2')
  })

  it('shows empty and no-match states', () => {
    const { rerender } = render(<OpenAgentDialog open agents={[]} loading={false} selectedAgentId="" onSelect={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByText('No saved agents yet')).toBeInTheDocument()
    rerender(<OpenAgentDialog open agents={agents} loading={false} selectedAgentId="" onSelect={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Search agents'), { target: { value: 'zzz' } })
    expect(screen.getByText('No agents match your search')).toBeInTheDocument()
  })
})

describe('ManageAgentsDialog', () => {
  it('opens and deletes agents and marks the open one', () => {
    const onOpenAgent = vi.fn()
    const onDeleteAgent = vi.fn()
    render(
      <ManageAgentsDialog open agents={agents} loading={false} saving={false} selectedAgentId="a1" onOpenAgent={onOpenAgent} onDeleteAgent={onDeleteAgent} onClose={vi.fn()} />
    )
    const dialog = screen.getByRole('dialog', { name: /Manage agents/ })
    expect(dialog).toHaveTextContent('Currently open')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Open Agent Two' }))
    expect(onOpenAgent).toHaveBeenCalledWith('a2')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete Agent One' }))
    expect(onDeleteAgent).toHaveBeenCalledWith(agents[0])
  })
})

describe('SaveAsDialog', () => {
  it('suggests a name, requires a non-empty name, and confirms on Enter', () => {
    const onConfirm = vi.fn()
    render(<SaveAsDialog open initialName="Agent One (Copy)" saving={false} onConfirm={onConfirm} onClose={vi.fn()} />)
    const input = screen.getByLabelText('Agent name')
    expect(input).toHaveValue('Agent One (Copy)')
    fireEvent.change(input, { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: 'Save as' })).toBeDisabled()
    fireEvent.change(input, { target: { value: 'Renamed copy' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledWith('Renamed copy')
  })
})

describe('ToolRequestDialog', () => {
  it('submits title and description and closes on success', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true)
    const onClose = vi.fn()
    render(<ToolRequestDialog open submitting={false} onSubmit={onSubmit} onClose={onClose} />)
    const dialog = screen.getByRole('dialog', { name: 'New request to developers' })
    expect(within(dialog).getByText('Describe the tool you need. You can draft it with AI Chat first.')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'GO tool' } })
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: 'Expand GO relationships.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('GO tool', 'Expand GO relationships.'))
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('stays open when submission fails validation', async () => {
    const onSubmit = vi.fn().mockResolvedValue(false)
    const onClose = vi.fn()
    render(<ToolRequestDialog open submitting={false} onSubmit={onSubmit} onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Send request' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('confirmation dialogs', () => {
  it('SelfExclusionDialog explains the mismatch and confirms', () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(<SelfExclusionDialog open allowedGroupIds={['GROUP_B']} currentUserGroupIds={['GROUP_A']} onConfirm={onConfirm} onCancel={onCancel} />)
    const dialog = screen.getByRole('dialog', { name: 'Save a restriction that excludes you?' })
    expect(dialog).toHaveTextContent(/your current groups are GROUP_A/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Go back' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save restriction' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('DeleteAgentDialog names the agent and states the effect', () => {
    const onConfirm = vi.fn()
    render(<DeleteAgentDialog open agentName="Agent One" saving={false} onConfirm={onConfirm} onCancel={vi.fn()} />)
    const dialog = screen.getByRole('dialog', { name: 'Delete agent?' })
    expect(dialog).toHaveTextContent('This archives “Agent One” so it is no longer available for new use.')
    expect(dialog).toHaveTextContent('Saved versions and their history are retained; existing references are not silently retargeted.')
    expect(dialog).not.toHaveTextContent('deletes “Agent One” and its version history')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('UnsavedChangesDialog offers Discard and Keep editing with Escape keeping edits', () => {
    const onDiscard = vi.fn()
    const onKeepEditing = vi.fn()
    render(<UnsavedChangesDialog open onDiscard={onDiscard} onKeepEditing={onKeepEditing} />)
    const dialog = screen.getByRole('dialog', { name: 'Discard unsaved changes?' })
    expect(within(dialog).getByRole('button', { name: 'Keep editing' })).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(onKeepEditing).toHaveBeenCalledTimes(1)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Discard' }))
    expect(onDiscard).toHaveBeenCalledTimes(1)
  })

  it('RevertVersionDialog names the version and confirms', () => {
    const onConfirm = vi.fn()
    render(<RevertVersionDialog open version={3} saving={false} onConfirm={onConfirm} onCancel={vi.fn()} />)
    const dialog = screen.getByRole('dialog', { name: 'Restore configuration 3?' })
    expect(dialog).toHaveTextContent('model settings, prompts, tools, group rules')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Restore' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})

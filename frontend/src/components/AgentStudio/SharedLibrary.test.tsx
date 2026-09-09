import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SharedLibrary from './SharedLibrary'
import type { CustomAgent, ToolIdeaRequest } from '@/types/promptExplorer'
import type { FlowSummaryResponse } from './FlowBuilder/types'

const mocks = vi.hoisted(() => ({ listCustomAgents: vi.fn(), listToolIdeaRequests: vi.fn(), listAllFlows: vi.fn(), cloneAgentToWorkshop: vi.fn(), cloneFlow: vi.fn() }))
vi.mock('@/services/agentStudioService', () => mocks)
const agent = (id: string, visibility: string): CustomAgent => ({
  id, agent_id: `ca_${id}`, name: `${id} agent`, description: 'Find genes', user_id: id === 'mine' ? 10 : 20,
  visibility, project_id: 'team', custom_prompt: 'Prompt', group_prompt_overrides: {}, allowed_group_ids: [], inherited_allowed_group_ids: [], icon: '', include_group_rules: false,
  model_id: 'model', model_temperature: 0, tool_ids: [], is_active: true, created_at: '', updated_at: '',
})
const flow: FlowSummaryResponse = { id: 'flow', name: 'Shared flow', description: 'Review evidence', user_id: 20, visibility: 'project', project_id: 'team', shared_at: '', is_owner: false, step_count: 2, execution_count: 0, last_executed_at: null, created_at: '', updated_at: '' }
const request: ToolIdeaRequest = { id: 'idea', title: 'Shared idea', description: 'A useful request', user_id: 20, project_id: 'team', status: 'submitted', created_at: '', updated_at: '' }
const callbacks = () => ({ onOpenAgent: vi.fn(), onOpenFlow: vi.fn(), onReuseToolIdea: vi.fn() })
const row = (title: string) => within(screen.getByText(title).closest('li')!)
async function select(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}
beforeEach(() => {
  vi.resetAllMocks()
  mocks.listCustomAgents.mockImplementation((_template, scope) => Promise.resolve({ custom_agents: scope === 'visible' ? [agent('mine', 'private'), agent('shared', 'project')] : [agent('mine', 'private')] }))
  mocks.listToolIdeaRequests.mockResolvedValue({ tool_ideas: [request] })
  mocks.listAllFlows.mockResolvedValue({ flows: [flow] })
  mocks.cloneAgentToWorkshop.mockResolvedValue({ id: 'agent-copy', visibility: 'private' })
  mocks.cloneFlow.mockResolvedValue({ id: 'flow-copy', visibility: 'private', is_owner: true })
})
describe('SharedLibrary', () => {
  it('combines authorized records with owner/project/visibility metadata and filters locally', async () => {
    render(<SharedLibrary active {...callbacks()} />)
    await screen.findByText('Shared flow')
    expect(screen.getAllByRole('listitem')).toHaveLength(4)
    expect(row('mine agent').getByText('Private')).toBeInTheDocument()
    expect(row('shared agent').getByText('Owner #20 · Project: team')).toBeInTheDocument()
    await select('Ownership', 'Mine')
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('mine agent')).toBeInTheDocument()
    await select('Ownership', 'Shared with project')
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
    await select('Artifact type', 'Flow')
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    fireEvent.change(screen.getByLabelText('Search artifacts'), { target: { value: 'absent' } })
    expect(screen.getByText('No artifacts match your filters.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Search artifacts'), { target: { value: ' EVIDENCE ' } })
    expect(screen.getByText('Shared flow')).toBeInTheDocument()
    expect(mocks.listAllFlows).toHaveBeenCalledTimes(1)
    expect(mocks.listCustomAgents).toHaveBeenCalledWith(undefined, 'visible')
  })
  it('offers owner opening and teammate clones without owner-only mutations', async () => {
    const props = callbacks()
    render(<SharedLibrary active {...props} />)
    await screen.findByText('Shared flow')
    expect(row('shared agent').queryByRole('button', { name: 'Open in Workshop' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete|share|make private|save/i })).not.toBeInTheDocument()
    fireEvent.click(row('mine agent').getByRole('button', { name: 'Open in Workshop' }))
    expect(props.onOpenAgent).toHaveBeenCalledWith('mine')
    fireEvent.click(row('shared agent').getByRole('button', { name: 'Clone to Workshop' }))
    await waitFor(() => expect(props.onOpenAgent).toHaveBeenCalledWith('agent-copy'))
    expect(mocks.cloneAgentToWorkshop).toHaveBeenCalledWith('ca_shared')
    fireEvent.click(row('Shared flow').getByRole('button', { name: 'Open read-only' }))
    expect(props.onOpenFlow).toHaveBeenCalledWith('flow')
    expect(row('Shared flow').getByRole('link', { name: 'Run in workspace' })).toHaveAttribute('href', '/?flow=flow')
    fireEvent.click(row('Shared flow').getByRole('button', { name: 'Clone to edit' }))
    await waitFor(() => expect(props.onOpenFlow).toHaveBeenCalledWith('flow-copy'))
    expect(mocks.cloneFlow).toHaveBeenCalledWith('flow')
  })
  it('reuses only summary fields even for owner requests containing private history', async () => {
    mocks.listToolIdeaRequests.mockResolvedValue({ tool_ideas: [{ ...request, opus_conversation: [{ content: 'secret history' }], developer_notes: 'secret notes' }] })
    const props = callbacks()
    render(<SharedLibrary active {...props} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open request context' }))
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Discuss request with Claude' }))
    expect(props.onReuseToolIdea).toHaveBeenCalledWith(request)
  })
  it('shows an empty library for a non-member with no authorized artifacts', async () => {
    mocks.listCustomAgents.mockResolvedValue({ custom_agents: [] })
    mocks.listToolIdeaRequests.mockResolvedValue({ tool_ideas: [] })
    mocks.listAllFlows.mockResolvedValue({ flows: [] })
    render(<SharedLibrary active {...callbacks()} />)
    expect(await screen.findByText('No artifacts match your filters.')).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })
  it('reports load failure without presenting a partial library and supports retry', async () => {
    mocks.listAllFlows.mockRejectedValueOnce(new Error('Flow access failed'))
    render(<SharedLibrary active {...callbacks()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Flow access failed')
    expect(screen.queryByText('mine agent')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByText('Shared flow')).toBeInTheDocument()
  })
  it('keeps originals available after a clone failure', async () => {
    mocks.cloneFlow.mockRejectedValue(new Error('Clone denied'))
    const props = callbacks()
    render(<SharedLibrary active {...props} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Clone to edit' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Clone denied')
    expect(props.onOpenFlow).not.toHaveBeenCalled()
    expect(screen.getByText('Shared flow')).toBeInTheDocument()
  })

  it('does not navigate away from another tab when a pending clone completes', async () => {
    let finishClone!: (copy: { id: string }) => void
    mocks.cloneFlow.mockReturnValue(new Promise((resolve) => { finishClone = resolve }))
    const props = callbacks()
    const { rerender } = render(<SharedLibrary active {...props} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Clone to edit' }))
    rerender(<SharedLibrary active={false} {...props} />)
    await act(async () => { finishClone({ id: 'copy' }) })
    expect(props.onOpenFlow).not.toHaveBeenCalled()
  })
})

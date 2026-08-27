import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ChatDefault from './ChatDefault'
import {
  ChatRoutePreferenceApiError,
  type ChatRoutePreference,
  type ChatRouteTarget,
} from '@/services/chatRoutePreferenceService'

const service = vi.hoisted(() => ({
  fetchChatRoutePreference: vi.fn(),
  fetchChatRouteTargets: vi.fn(),
  saveChatRoutePreference: vi.fn(),
  clearChatRoutePreference: vi.fn(),
}))

vi.mock('@/services/chatRoutePreferenceService', async () => {
  const actual = await vi.importActual<typeof import('@/services/chatRoutePreferenceService')>(
    '@/services/chatRoutePreferenceService',
  )
  return { ...actual, ...service }
})

const agent: ChatRouteTarget = {
  id: 'ontology-agent',
  kind: 'agent',
  display_name: 'Ontology Curator',
  description: 'Reviews ontology annotations',
  category: 'Curation',
  available: true,
}
const flow: ChatRouteTarget = {
  id: 'flow-1',
  kind: 'flow',
  display_name: 'Paper Review',
  description: 'Reviews a paper',
  category: null,
  available: true,
}
const automatic: ChatRoutePreference = {
  mode: 'automatic', agent_id: null, flow_id: null, status: 'available', target: null,
}
const agentPreference: ChatRoutePreference = {
  mode: 'agent', agent_id: agent.id, flow_id: null, status: 'available', target: agent,
}
const flowPreference: ChatRoutePreference = {
  mode: 'flow', agent_id: null, flow_id: flow.id, status: 'available', target: flow,
}

async function chooseMode(name: 'Agent' | 'Flow') {
  await userEvent.click(await screen.findByRole('button', { name }))
}

async function chooseTarget(name: string) {
  const input = await screen.findByRole('combobox')
  await userEvent.click(input)
  await userEvent.type(input, name.slice(0, 4))
  await userEvent.click(await screen.findByRole('option', { name: new RegExp(name, 'i') }))
}

describe('ChatDefault', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    service.fetchChatRoutePreference.mockResolvedValue(automatic)
    service.fetchChatRouteTargets.mockResolvedValue([agent, flow])
    service.saveChatRoutePreference.mockImplementation(async (update) =>
      update.mode === 'agent' ? agentPreference : flowPreference,
    )
    service.clearChatRoutePreference.mockResolvedValue(automatic)
  })

  it('shows initial loading and the confirmed summary', async () => {
    let resolve!: (value: ChatRoutePreference) => void
    service.fetchChatRoutePreference.mockReturnValue(new Promise((done) => { resolve = done }))
    render(<ChatDefault />)
    expect(screen.getByLabelText('Loading chat default')).toBeInTheDocument()
    resolve(automatic)
    expect(await screen.findByText('Current: Automatic routing')).toBeInTheDocument()
  })

  it('keeps modes exclusive and does not persist Agent before a target is selected', async () => {
    render(<ChatDefault />)
    await chooseMode('Agent')
    expect(screen.getByRole('button', { name: 'Agent' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Automatic' })).toHaveAttribute('aria-pressed', 'false')
    expect(service.saveChatRoutePreference).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Choose agent')).toBeInTheDocument()
  })

  it('searches safe summary fields and saves one complete agent preference atomically', async () => {
    render(<ChatDefault />)
    await chooseMode('Agent')
    await chooseTarget('Ontology Curator')
    await waitFor(() => expect(service.saveChatRoutePreference).toHaveBeenCalledWith({
      mode: 'agent', agent_id: 'ontology-agent', flow_id: null,
    }))
    expect(await screen.findByText('Current: Agent · Ontology Curator')).toBeInTheDocument()
    expect(screen.getByText('Saved: Agent · Ontology Curator')).toBeInTheDocument()
  })

  it('reveals only flow targets in Flow mode and persists a complete flow preference', async () => {
    render(<ChatDefault />)
    await chooseMode('Flow')
    const input = await screen.findByRole('combobox')
    await userEvent.click(input)
    expect(screen.queryByRole('option', { name: /Ontology Curator/i })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByRole('option', { name: /Paper Review/i }))
    await waitFor(() => expect(service.saveChatRoutePreference).toHaveBeenCalledWith({
      mode: 'flow', agent_id: null, flow_id: 'flow-1',
    }))
  })

  it('clears a confirmed selection back to Automatic', async () => {
    service.fetchChatRoutePreference.mockResolvedValue(agentPreference)
    render(<ChatDefault />)
    await userEvent.click(await screen.findByRole('button', { name: 'Automatic' }))
    await waitFor(() => expect(service.clearChatRoutePreference).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('Current: Automatic routing')).toBeInTheDocument()
  })

  it('prevents double submission while a save is in progress', async () => {
    service.clearChatRoutePreference.mockReturnValue(new Promise(() => {}))
    service.fetchChatRoutePreference.mockResolvedValue(agentPreference)
    render(<ChatDefault />)
    const automaticButton = await screen.findByRole('button', { name: 'Automatic' })
    await userEvent.click(automaticButton)
    expect(screen.getByText('Saving chat default…')).toBeInTheDocument()
    expect(automaticButton).toBeDisabled()
    expect(service.clearChatRoutePreference).toHaveBeenCalledTimes(1)
  })

  it('restores confirmed state on save failure and offers a retry', async () => {
    service.saveChatRoutePreference.mockRejectedValueOnce(new Error('server error'))
      .mockResolvedValueOnce(agentPreference)
    render(<ChatDefault />)
    await chooseMode('Agent')
    await chooseTarget('Ontology Curator')
    expect(await screen.findByRole('alert')).toHaveTextContent('previous selection is still active')
    expect(screen.getByRole('button', { name: 'Automatic' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Current: Automatic routing')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Current: Agent · Ontology Curator')).toBeInTheDocument()
  })

  it('removes a target rejected by server authorization and asks for a replacement', async () => {
    service.saveChatRoutePreference.mockRejectedValue(
      new ChatRoutePreferenceApiError('unavailable', 404),
    )
    render(<ChatDefault />)
    await chooseMode('Agent')
    await chooseTarget('Ontology Curator')
    expect(await screen.findByRole('alert')).toHaveTextContent('selection is no longer available')
    expect(screen.getByRole('alert')).toHaveTextContent('Choose another')
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    await chooseMode('Agent')
    await userEvent.click(await screen.findByRole('combobox'))
    expect(await screen.findByText('No agents are available to you.')).toBeInTheDocument()
  })

  it('shows actionable stale or authorization-revoked selection guidance', async () => {
    service.fetchChatRoutePreference.mockResolvedValue({
      ...flowPreference,
      status: 'unavailable',
      target: { ...flow, available: false },
    })
    render(<ChatDefault />)
    expect(await screen.findByText(/saved flow is no longer available/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Automatic' })).toBeEnabled()
    expect(screen.getByLabelText('Choose flow')).toBeInTheDocument()
  })

  it('handles an empty authorized list and a search with no matches', async () => {
    service.fetchChatRouteTargets.mockResolvedValue([agent])
    render(<ChatDefault />)
    await chooseMode('Flow')
    await userEvent.click(await screen.findByRole('combobox'))
    expect(await screen.findByText(/No flows are available to you yet.*Agent Studio/)).toBeInTheDocument()

    await chooseMode('Agent')
    const input = await screen.findByRole('combobox')
    await userEvent.type(input, 'unmatched')
    expect(await screen.findByText('No agents match “unmatched”. Try a name or category.')).toBeInTheDocument()
  })

  it('announces picker errors and retries loading choices', async () => {
    service.fetchChatRouteTargets.mockRejectedValueOnce(new Error('Could not load chat default choices.'))
      .mockResolvedValueOnce([agent])
    render(<ChatDefault />)
    await chooseMode('Agent')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load chat default choices.')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(service.fetchChatRouteTargets).toHaveBeenCalledTimes(2))
    expect(await screen.findByLabelText('Choose agent')).toBeEnabled()
  })

  it('shows picker loading without implying that a save is underway', async () => {
    service.fetchChatRouteTargets.mockReturnValue(new Promise(() => {}))
    render(<ChatDefault />)
    await chooseMode('Agent')
    expect(await screen.findByRole('progressbar')).toBeInTheDocument()
    expect(screen.queryByText('Saving chat default…')).not.toBeInTheDocument()
  })

  it('offers a retry when the confirmed preference cannot be loaded', async () => {
    service.fetchChatRoutePreference.mockRejectedValueOnce(new Error('Could not load your chat default.'))
      .mockResolvedValueOnce(automatic)
    render(<ChatDefault />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load your chat default.')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Current: Automatic routing')).toBeInTheDocument()
  })

  it('opens keyboard-accessible durable help with all required guidance', async () => {
    render(<ChatDefault />)
    const help = await screen.findByRole('button', { name: 'About chat default' })
    help.focus()
    await userEvent.keyboard('{Enter}')
    const dialog = await screen.findByRole('dialog', { name: 'About chat default' })
    expect(dialog).toHaveTextContent('Your typed chat message remains the request')
    expect(dialog).toHaveTextContent('future chat requests')
    expect(dialog).toHaveTextContent('change or clear it at any time')
    expect(dialog).toHaveTextContent('does not grant access')
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(help).toHaveFocus()
  })
})

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfileConsumerImpact from './ProfileConsumerImpact'
import type { ProfileConsumerPage } from '@/services/genericProfileService'

const api = vi.hoisted(() => ({ listGenericProfileConsumers: vi.fn() }))
vi.mock('@/services/genericProfileService', () => api)
const first: ProfileConsumerPage = { head_revision: 2, next_cursor: 'agent/one', consumers: [{
  key: 'agent/one', kind: 'agent', name: 'Extraction', agent_id: 'agent', agent_revision_id: 'one',
  agent_revision: 1, profile_revision: 1, is_current_agent_revision: false, archived: false,
  flow_id: null, node_id: null,
}] }
const last: ProfileConsumerPage = { head_revision: 2, next_cursor: null, consumers: [{
  ...first.consumers[0], key: 'flow/flow/node', kind: 'flow', name: 'Review pipeline',
  flow_id: 'flow', node_id: 'extract', archived: true,
}] }

describe('saved profile uses', () => {
  beforeEach(() => api.listGenericProfileConsumers.mockReset())

  it('loads on demand and explains old pins, history and archives without write controls', async () => {
    api.listGenericProfileConsumers.mockResolvedValueOnce(first).mockResolvedValueOnce(last)
    render(<ProfileConsumerImpact profileId="profile" />)
    expect(api.listGenericProfileConsumers).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Show saved uses' }))
    await screen.findByText('Agent: Extraction')
    expect(screen.getByText(/historical agent configuration/)).toHaveTextContent('older profile revision')
    fireEvent.click(screen.getByRole('button', { name: 'Load more saved uses' }))
    await screen.findByText('Flow: Review pipeline · archived')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(api.listGenericProfileConsumers).toHaveBeenLastCalledWith('profile', 'agent/one')
    expect(screen.queryByRole('button', { name: /save|retarget|apply/i })).toHaveAccessibleName('Refresh saved uses')
    expect(screen.queryByRole('button', { name: 'Load more saved uses' })).not.toBeInTheDocument()
  })

  it('preserves the first page and retries the failed cursor', async () => {
    api.listGenericProfileConsumers.mockResolvedValueOnce(first).mockRejectedValueOnce(new Error('Network unavailable')).mockResolvedValueOnce(last)
    render(<ProfileConsumerImpact profileId="profile" />)
    fireEvent.click(screen.getByRole('button', { name: 'Show saved uses' }))
    await screen.findByText('Agent: Extraction')
    fireEvent.click(screen.getByRole('button', { name: 'Load more saved uses' }))
    await screen.findByRole('alert')
    expect(screen.getByText('Agent: Extraction')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Load more saved uses' }))
    await screen.findByText('Flow: Review pipeline · archived')
    expect(api.listGenericProfileConsumers.mock.calls.slice(1)).toEqual([['profile', 'agent/one'], ['profile', 'agent/one']])
  })

  it('does not infer global non-use from an authorized empty result', async () => {
    api.listGenericProfileConsumers.mockResolvedValue({ consumers: [], next_cursor: null, head_revision: 1 })
    render(<ProfileConsumerImpact profileId="profile" />)
    fireEvent.click(screen.getByRole('button', { name: 'Show saved uses' }))
    await screen.findByText(/does not establish that nobody else uses/)
  })

  it('discards an in-flight response after selecting another profile', async () => {
    let resolve!: (value: ProfileConsumerPage) => void
    api.listGenericProfileConsumers.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    const view = render(<ProfileConsumerImpact profileId="profile" />)
    fireEvent.click(screen.getByRole('button', { name: 'Show saved uses' }))
    expect(screen.getByRole('status')).toHaveTextContent('Loading saved uses')
    view.rerender(<ProfileConsumerImpact profileId="another" />)
    await act(async () => resolve(first))
    expect(screen.queryByText('Agent: Extraction')).not.toBeInTheDocument()
    api.listGenericProfileConsumers.mockResolvedValue(last)
    fireEvent.click(screen.getByRole('button', { name: 'Show saved uses' }))
    await waitFor(() => expect(api.listGenericProfileConsumers).toHaveBeenLastCalledWith('another', undefined))
    await screen.findByText('Flow: Review pipeline · archived')
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RevalidationControls from './RevalidationControls'
import * as service from '../services/validationHistoryService'

vi.mock('../services/validationHistoryService', () => ({
  fetchCandidateReviewOperation: vi.fn(), fetchReviewOperation: vi.fn(), requestReview: vi.fn(), controlReview: vi.fn(),
}))
const saved = { operation_id: 'existing', session_id: 'session', state: 'running', artifact_count: 1, outcome: null }

describe('Record revalidation', () => {
  beforeEach(() => { vi.resetAllMocks() })
  it('reopens an existing round without requesting another validation', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue(saved)
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await screen.findByText('Revalidation: running')
    expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeDisabled()
    expect(service.requestReview).not.toHaveBeenCalled()
    expect(service.controlReview).not.toHaveBeenCalled()
  })
  it('submits only B and reuses its command identity after an ambiguous HTTP failure', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue(null)
    vi.mocked(service.requestReview).mockRejectedValueOnce(new Error('Connection interrupted')).mockResolvedValueOnce(saved)
    const user = userEvent.setup()
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Revalidate record' }))
    await screen.findByText('Connection interrupted')
    await user.click(screen.getByRole('button', { name: 'Retry this request' }))
    await screen.findByText('Revalidation: running')
    expect(service.requestReview).toHaveBeenCalledTimes(2)
    const first = vi.mocked(service.requestReview).mock.calls[0]
    expect(first.slice(0, 3)).toEqual(['session', ['B'], null])
    expect(vi.mocked(service.requestReview).mock.calls[1]).toEqual(first)
  })
})

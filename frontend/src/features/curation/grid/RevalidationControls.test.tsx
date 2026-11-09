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
  beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear() })
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
  it('restores an ambiguous request identity after the panel closes', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue(null)
    vi.mocked(service.requestReview).mockRejectedValueOnce(new Error('Lost reply')).mockResolvedValueOnce(saved)
    const user = userEvent.setup()
    const firstPanel = render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Revalidate record' }))
    await screen.findByText('Lost reply')
    const firstRequest = vi.mocked(service.requestReview).mock.calls[0]
    firstPanel.unmount()
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry this request' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Retry this request' }))
    await screen.findByText('Revalidation: running')
    expect(vi.mocked(service.requestReview).mock.calls[1]).toEqual(firstRequest)
  })
  it('names a system-error stop in plain words, not its code', async () => {
    const words = 'a system error stopped a save before it changed anything, and it was reported'
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue({ ...saved, state: 'needs_review', outcome: {
      reason: 'source_projection_invalid',
      results: [{ reason: 'source_projection_invalid', review: { state: 'needs_review', ready: false } },
        { review: { state: 'needs_curator_review', ready: false } }],
    } })
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await screen.findByText(`Stopped: ${words}`)
    expect(screen.getByText(`Review: ${words}`)).toBeInTheDocument()
    expect(screen.getByText('Review: needs curator review')).toBeInTheDocument()
    expect(screen.queryByText(/source.projection/)).toBeNull()
  })
  it('names the stop for a different answer on an edited validated reference in plain words', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue({ ...saved, state: 'needs_review',
      outcome: { reason: 'curator_revalidation_required' } })
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await screen.findByText('Stopped: a different validator answer would replace your edit on a validated reference; '
      + 'use Revalidate on that record if the new answer should replace your edit')
    expect(screen.queryByText(/curator.revalidation.required/)).toBeNull()
  })
  it('offers no continue after a system-error stop, which would only stop again', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue({ ...saved, state: 'needs_review',
      outcome: { reason: 'source_projection_invalid' } })
    const view = render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await screen.findByText('Revalidation: needs review')
    expect(screen.queryByRole('button', { name: 'Continue existing round' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeEnabled()
    view.unmount()
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue({ ...saved, state: 'needs_review',
      outcome: { reason: 'reviewer_left_pending' } })
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    expect(await screen.findByRole('button', { name: 'Continue existing round' })).toBeEnabled()
  })
})

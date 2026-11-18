import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RevalidationControls from './RevalidationControls'
import { CurationWorkspaceRequestError } from '../services/curationWorkspaceService'
import * as service from '../services/validationHistoryService'

vi.mock('../services/validationHistoryService', () => ({
  fetchCandidateReviewOperation: vi.fn(), fetchReviewOperation: vi.fn(), requestReview: vi.fn(), controlReview: vi.fn(),
}))
const saved = { operation_id: 'existing', session_id: 'session', state: 'running', artifact_count: 1, outcome: null }
const stoppedRecord = {
  state: 'needs_curator_review',
  reason: 'curator_revalidation_required',
  resource: 'B',
  review: { state: 'pending', ready: false },
}

describe('Record revalidation', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    sessionStorage.clear()
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000001')
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000002')
  })
  afterEach(() => { vi.restoreAllMocks() })
  it('reopens an existing round without requesting another validation', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue(saved)
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await screen.findByText('Revalidation: running')
    expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeDisabled()
    expect(service.requestReview).not.toHaveBeenCalled()
    expect(service.controlReview).not.toHaveBeenCalled()
  })
  it('allows rejected instructions to be corrected and submitted under a fresh request ID', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue(null)
    const actual = await vi.importActual<typeof service>('../services/validationHistoryService')
    vi.mocked(service.requestReview).mockImplementation(actual.requestReview)
    const message = 'Guidance must be nonempty and within its configured character limit'
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json({ detail: message }, { status: 422 }))
      .mockResolvedValueOnce(Response.json(saved, { status: 202 }))
    const user = userEvent.setup()
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    const submit = screen.getByRole('button', { name: 'Revalidate record' })
    await waitFor(() => expect(submit).toBeEnabled())
    const instructions = screen.getByRole('textbox', { name: 'Instructions (optional)' })
    const original = 'x'.repeat(8001)
    fireEvent.change(instructions, { target: { value: original } })
    await user.click(submit)

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(instructions).toBeEnabled()
    expect(instructions).toHaveValue(original)
    expect(sessionStorage.getItem('workspace-review-request:session:B')).toBeNull()

    const corrected = 'Check the updated record'
    await user.clear(instructions)
    await user.type(instructions, corrected)
    await user.click(screen.getByRole('button', { name: 'Revalidate record' }))
    await screen.findByText('Revalidation: running')
    const [rejected, accepted] = fetchMock.mock.calls.map(([, init]) => JSON.parse(String(init?.body)))
    expect(rejected).toEqual({ request_id: expect.any(String), candidate_ids: ['B'], guidance: original })
    expect(accepted).toEqual({ request_id: expect.any(String), candidate_ids: ['B'], guidance: corrected })
    expect(accepted.request_id).not.toBe(rejected.request_id)
  })
  it.each([
    new Error('Connection interrupted'),
    new CurationWorkspaceRequestError(503, 'Service unavailable'),
  ])('submits only B and reuses its command identity after %s', async (failure) => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue(null)
    vi.mocked(service.requestReview).mockRejectedValueOnce(failure).mockResolvedValueOnce(saved)
    const user = userEvent.setup()
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Revalidate record' }))
    await screen.findByText(failure.message)
    expect(screen.getByRole('textbox', { name: 'Instructions (optional)' })).toBeDisabled()
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
    const guidance = 'Review the saved value'
    await user.type(screen.getByRole('textbox', { name: 'Instructions (optional)' }), guidance)
    await user.click(screen.getByRole('button', { name: 'Revalidate record' }))
    await screen.findByText('Lost reply')
    const firstRequest = vi.mocked(service.requestReview).mock.calls[0]
    firstPanel.unmount()
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry this request' })).toBeEnabled())
    const restored = screen.getByRole('textbox', { name: 'Instructions (optional)' })
    expect(restored).toHaveValue(guidance)
    expect(restored).toBeDisabled()
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
    // Continuing would only stop again; the curator's Revalidate is the way forward.
    expect(screen.queryByRole('button', { name: 'Continue existing round' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeInTheDocument()
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
  it('requires a fresh Revalidate when the only reviewer result stops for curator revalidation', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue({
      ...saved, state: 'needs_review', outcome: { results: [stoppedRecord] },
    })
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    await screen.findByText('Revalidation: needs review')
    expect(screen.queryByRole('button', { name: 'Continue existing round' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Revalidate record' })).toBeEnabled()
  })
  it('offers Continue when another reviewer result is still pending', async () => {
    vi.mocked(service.fetchCandidateReviewOperation).mockResolvedValue({
      ...saved, state: 'needs_review', artifact_count: 2,
      outcome: { results: [stoppedRecord, { reason: 'reviewer_left_pending', review: { state: 'pending', ready: false } }] },
    })
    render(<RevalidationControls candidateId="B" sessionId="session" onChanged={vi.fn()} />)
    expect(await screen.findByRole('button', { name: 'Continue existing round' })).toBeEnabled()
  })
})

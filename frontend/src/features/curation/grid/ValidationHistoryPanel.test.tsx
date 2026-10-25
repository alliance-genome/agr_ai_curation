import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ValidationHistoryPanel from './ValidationHistoryPanel'
import * as service from '../services/validationHistoryService'

vi.mock('../services/validationHistoryService', () => ({
  fetchValidationHistory: vi.fn(), fetchValidationHistoryDetail: vi.fn(), decideValidation: vi.fn(),
  requestReview: vi.fn(), fetchCandidateReviewOperation: vi.fn(), fetchReviewOperation: vi.fn(), controlReview: vi.fn(),
}))
const reference = { id: 'baseline', sha256: 'a'.repeat(64) }

describe('Validation history', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(service.fetchValidationHistory).mockResolvedValue({
      entries: [], baseline: reference, next_after: null, episode_id: 'episode',
      record: { original: null, current: null, curator_overrides: [], selections: [] },
    })
  })
  it('reads saved metadata without starting validation or loading exact bodies', async () => {
    render(<ValidationHistoryPanel candidateId="record" />)
    await screen.findByText('Original extraction')
    expect(service.fetchValidationHistory).toHaveBeenCalledWith('record', 0, expect.any(AbortSignal))
    expect(service.fetchValidationHistoryDetail).not.toHaveBeenCalled()
    expect(service.requestReview).not.toHaveBeenCalled()
    expect(service.decideValidation).not.toHaveBeenCalled()
  })
  it('loads exact content on request and follows its continuation without dropping data', async () => {
    vi.mocked(service.fetchValidationHistoryDetail)
      .mockResolvedValueOnce({ content: '{"original":', next_offset: 12, total_bytes: 16 })
      .mockResolvedValueOnce({ content: '"X"}', next_offset: null, total_bytes: 16 })
    const user = userEvent.setup()
    render(<ValidationHistoryPanel candidateId="record" />)
    await user.click(await screen.findByRole('button', { name: 'Read exact saved details' }))
    await user.click(await screen.findByRole('button', { name: 'Load more saved details' }))
    await screen.findByText('{"original":"X"}')
    expect(service.fetchValidationHistoryDetail).toHaveBeenNthCalledWith(2, 'record', reference, 12)
    expect(service.requestReview).not.toHaveBeenCalled()
  })
  it('distinguishes scientific outcome, decisions, selected version and later curator edits', async () => {
    const summary = (value: string) => ({ fields: [{ field: 'term', value }], truncated: false })
    const answer = (id: string, review: string, selected: boolean): service.HistoryEntry => ({
      id, sha256: id, kind: 'completion', sequence: 1, created_at: '2026-10-25T00:00:00Z',
      actor: null, reason: null, state: 'completed', choices: [], decisions: [],
      scientific_status: 'unresolved', answer: { fields: [{ field: 'unresolved_questions', value: 'The paper names two alternatives.' }], truncated: false },
      answer_states: [{ target_id: 'term', selected, review, stale: !selected, version: 3 }],
    })
    vi.mocked(service.fetchValidationHistory).mockResolvedValue({
      entries: [answer('first', 'reject', false), {
        ...answer('retry', 'pending', false), kind: 'decision', state: null, scientific_status: null, answer: null,
        decisions: [{ action: 'reconsider', reason: 'Check the nomenclature again' }], answer_states: [],
      }, answer('second', 'accept', true)],
      baseline: reference, next_after: null, episode_id: 'episode',
      record: { original: summary('original mention'), current: summary('curator correction'),
        curator_overrides: [summary('curator correction')],
        selections: [{ target_id: 'term', version: 3, selected: 'second', review: 'accept', reason: null, scientific_status: 'unresolved' }],
      },
    })
    render(<ValidationHistoryPanel candidateId="record" />)
    await screen.findByText('original mention')
    expect(screen.getByText('Current record values')).toBeInTheDocument()
    expect(screen.getAllByText('curator correction')).toHaveLength(2)
    expect(screen.getAllByText('Scientific outcome: unresolved')).toHaveLength(2)
    expect(screen.getByText(/Not selected · Rejected · Stale/)).toBeInTheDocument()
    expect(screen.getByText(/Selected answer · Accepted/)).toBeInTheDocument()
    expect(screen.getByText(/reconsider: Check the nomenclature again/)).toBeInTheDocument()
    expect(screen.getByText(/Curator edits are current/)).toBeInTheDocument()
    expect(service.requestReview).not.toHaveBeenCalled()
  })
  it('shows access failures instead of inventing an empty successful history', async () => {
    vi.mocked(service.fetchValidationHistory).mockRejectedValue(new Error('Validation history unavailable'))
    render(<ValidationHistoryPanel candidateId="record" />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Validation history unavailable'))
    expect(screen.queryByText('Original extraction')).not.toBeInTheDocument()
  })
})

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
  it('shows access failures instead of inventing an empty successful history', async () => {
    vi.mocked(service.fetchValidationHistory).mockRejectedValue(new Error('Validation history unavailable'))
    render(<ValidationHistoryPanel candidateId="record" />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Validation history unavailable'))
    expect(screen.queryByText('Original extraction')).not.toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import RetiredRecordsPanel from './RetiredRecordsPanel'
import { fetchRecordHistory, restoreRecord } from '../services/validationHistoryService'
vi.mock('../services/validationHistoryService', () => ({ fetchRecordHistory: vi.fn(), restoreRecord: vi.fn() }))
const record = { envelope_id: 'env', object_id: 'object', target_id: 'target', version: 3,
  reason: 'Outside scope', original: { fact_id: 'original', sha256: 'a' },
  proposal: { fact_id: 'proposal', sha256: 'b' }, retirement: { fact_id: 'decision', sha256: 'c' } }

describe('RetiredRecordsPanel', () => {
  it('reads exact proposal history and restores only on explicit curator action', async () => {
    vi.mocked(fetchRecordHistory).mockResolvedValue({ content: 'Saved proposal', next_offset: null, total_bytes: 14 })
    vi.mocked(restoreRecord).mockResolvedValue({ publication: { ready: false } })
    const beforeChange = vi.fn().mockResolvedValue(undefined)
    const onChanged = vi.fn().mockResolvedValue(undefined)
    render(<RetiredRecordsPanel records={[record]} beforeChange={beforeChange} onChanged={onChanged} />)
    fireEvent.click(screen.getByText('Retired records (1)'))
    fireEvent.click(screen.getByRole('button', { name: 'Extractor recommendation and evidence' }))
    await screen.findByText('Saved proposal')
    expect(fetchRecordHistory).toHaveBeenCalledWith('env', record.proposal, 0)
    expect(restoreRecord).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Reason for restoring'), { target: { value: 'Review this again' } })
    fireEvent.click(screen.getByRole('button', { name: 'Restore for review' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
    expect(beforeChange).toHaveBeenCalledOnce()
    expect(restoreRecord).toHaveBeenCalledWith('env', 'target', 3, 'Review this again', expect.any(String))
  })
})

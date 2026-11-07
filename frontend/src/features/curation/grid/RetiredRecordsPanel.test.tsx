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

  it('shows a record the reviewer retired itself, with no extractor recommendation, and restores it', async () => {
    vi.mocked(restoreRecord).mockResolvedValue({ publication: { ready: false } })
    const onChanged = vi.fn().mockResolvedValue(undefined)
    const reviewerRetired = { ...record, reason: 'A genetic control, not a transgenic fly', proposal: null }
    render(<RetiredRecordsPanel records={[reviewerRetired]} beforeChange={vi.fn().mockResolvedValue(undefined)} onChanged={onChanged} />)
    fireEvent.click(screen.getByText('Retired records (1)'))
    expect(screen.getByText('A genetic control, not a transgenic fly')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Extractor recommendation and evidence' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Supervisor decision' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Reason for restoring'), { target: { value: 'Keep it' } })
    fireEvent.click(screen.getByRole('button', { name: 'Restore for review' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
    expect(restoreRecord).toHaveBeenLastCalledWith('env', 'target', 3, 'Keep it', expect.any(String))
  })
})

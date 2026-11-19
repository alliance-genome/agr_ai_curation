import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

  it('keeps paged history and every record’s restoration controls in the bounded panel', async () => {
    const firstPage = 'Original extraction line\n'.repeat(80)
    const lastPage = 'Final extraction line'
    const totalBytes = firstPage.length + lastPage.length
    vi.mocked(fetchRecordHistory)
      .mockResolvedValueOnce({ content: firstPage, next_offset: firstPage.length, total_bytes: totalBytes })
      .mockResolvedValueOnce({ content: lastPage, next_offset: null, total_bytes: totalBytes })
    const otherRecord = { ...record, target_id: 'second-target' }
    render(<RetiredRecordsPanel records={[record, otherRecord]} beforeChange={vi.fn()} onChanged={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Retired records (2)' }))
    const panel = screen.getByRole('region', { name: 'Retired records' })
    expect(panel).toHaveStyle({ maxHeight: '50%', overflowY: 'auto' })
    const history = within(panel)
    fireEvent.click(history.getAllByRole('button', { name: 'Original extraction' })[0])
    const readMore = await history.findByRole('button', { name: 'Read more: Original extraction' })
    fireEvent.click(readMore)
    await waitFor(() => expect(readMore).not.toBeInTheDocument())
    expect(fetchRecordHistory).toHaveBeenLastCalledWith('env', record.original, firstPage.length)
    expect(panel.querySelector('pre')?.textContent).toBe(firstPage + lastPage)

    const reasons = history.getAllByRole('textbox', { name: 'Reason for restoring' })
    const actions = history.getAllByRole('button', { name: 'Restore for review' })
    expect(reasons).toHaveLength(2)
    expect(actions).toHaveLength(2)
    fireEvent.change(reasons[1], { target: { value: 'Restore the second record' } })
    expect(actions[0]).toBeDisabled()
    expect(actions[1]).toBeEnabled()
  })
})

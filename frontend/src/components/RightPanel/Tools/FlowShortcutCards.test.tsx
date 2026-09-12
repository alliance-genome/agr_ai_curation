import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FlowShortcutCards from './FlowShortcutCards'
import type { FlowSummaryResponse } from '@/services/agentStudioService'
const flows = ['Stocks', 'Genes', 'Phenotypes'].map((name, i) => ({ id: String(i), name, description: `Extract ${name}`, step_count: 2 })) as FlowSummaryResponse[]
function setup(save = vi.fn(async (_ids: string[]) => true)) {
 function Wrapper() {
  const [ids, setIds] = useState(['0','1'])
  return <FlowShortcutCards flows={flows} selectedIds={ids} saving={false} onChange={async next => { if (await save(next)) { setIds(next); return true } return false }} onRun={vi.fn()} runningId={null} canRun onOpenWorkspace={vi.fn()} />
 }
 render(<Wrapper />)
 return save
}
describe('Personal flow shortcuts', () => {
 it('hides without deletion and adds an existing hidden flow back', async () => {
  const save = setup(); const user = userEvent.setup()
  expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Hide Stocks' }))
  expect(save).toHaveBeenLastCalledWith(['1'])
  expect(within(screen.getByRole('list')).queryByText('Stocks')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Add flow' }))
  await user.type(screen.getByRole('textbox'), 'stocks')
  await user.click(screen.getByRole('button', { name: 'Add Stocks' }))
  expect(save).toHaveBeenLastCalledWith(['1','0'])
 })
 it('moves through the handle keyboard and a pointer-accessible menu', async () => {
  const save = setup(); const user = userEvent.setup()
  screen.getByRole('button', { name: 'Reorder Genes' }).focus()
  await user.keyboard('{ArrowUp}')
  expect(save).toHaveBeenLastCalledWith(['1','0'])
  await user.click(screen.getByRole('button', { name: 'More options for Genes' }))
  await user.click(screen.getByRole('menuitem', { name: 'Move down' }))
  expect(save).toHaveBeenLastCalledWith(['0','1'])
 })
 it('reorders through a drag handle drop', async () => {
  const save = setup()
  const dataTransfer = { setData: vi.fn(), setDragImage: vi.fn(), effectAllowed: '', dropEffect: '' }
  fireEvent.dragStart(screen.getByRole('button', { name: 'Reorder Genes' }), { dataTransfer })
  expect(dataTransfer.setDragImage).toHaveBeenCalledWith(screen.getAllByRole('listitem')[1], expect.any(Number), expect.any(Number))
  const first = screen.getAllByRole('listitem')[0]
  fireEvent.dragOver(first, { dataTransfer }); fireEvent.drop(first, { dataTransfer })
  expect(save).toHaveBeenCalledWith(['1','0'])
 })
 it('keeps keyboard focus while the list saves', async () => {
  let resolveSave!: () => void
  function SavingWrapper() {
   const [saving, setSaving] = useState(false)
   const [ids, setIds] = useState(['0','1','2'])
   return <FlowShortcutCards flows={flows} selectedIds={ids} saving={saving} onChange={async next => {
    setSaving(true)
    await new Promise<void>(resolve => { resolveSave = resolve })
    setIds(next); setSaving(false); return true
   }} onRun={vi.fn()} runningId={null} canRun onOpenWorkspace={vi.fn()} />
  }
  render(<SavingWrapper />)
  const user = userEvent.setup()
  const handle = screen.getByRole('button', { name: 'Reorder Phenotypes' })
  handle.focus(); await user.keyboard('{ArrowUp}')
  expect(handle).toHaveFocus(); expect(handle).toHaveAttribute('aria-disabled','true')
  await user.tab(); expect(handle).not.toHaveFocus(); handle.focus()
  resolveSave()
  await waitFor(() => expect(handle).toHaveAttribute('aria-disabled','false'))
  expect(handle).toHaveFocus()
  await user.keyboard('{ArrowUp}'); resolveSave()
  await waitFor(() => expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('Phenotypes'))
 })
 it('keeps the previous list when saving fails', async () => {
  setup(vi.fn(async (_ids: string[]) => false)); const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Hide Stocks' }))
  expect(within(screen.getByRole('list')).getByText('Stocks')).toBeInTheDocument()
 })
})

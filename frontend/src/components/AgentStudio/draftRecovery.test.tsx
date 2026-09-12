import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DraftRecoveryNotice, useDraftRecovery } from './draftRecovery'

function Harness({ owner = 'curator-a', ready = true }: { owner?: string; ready?: boolean }) {
  const [value, setValue] = useState({ prompt: '' })
  const recovery = useDraftRecovery({ ownerId: owner, kind: 'agent', value, dirty: Boolean(value.prompt), ready, restore: setValue })
  return <><DraftRecoveryNotice recovery={recovery} /><input aria-label="Prompt" value={value.prompt} onChange={e => setValue({ prompt: e.target.value })} />
    <button onClick={() => setValue({ prompt: '' })}>Saved</button></>
}
const key = 'agr-studio-draft:v1:curator-a:agent'
beforeEach(() => { localStorage.clear(); vi.restoreAllMocks() })

describe('draft recovery', () => {
  it('offers recovery after unmount and preserves content until an explicit choice', async () => {
    const first = render(<Harness />)
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'My unsaved instructions' } })
    await screen.findByText(/Draft kept on this device/)
    first.unmount()
    render(<Harness />)
    await screen.findByRole('button', { name: 'Resume draft' })
    expect(JSON.parse(localStorage.getItem(key)!).value.prompt).toBe('My unsaved instructions')
    fireEvent.click(screen.getByRole('button', { name: 'Resume draft' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Resume draft' })).not.toBeInTheDocument())
    expect(screen.getByLabelText('Prompt')).toHaveValue('My unsaved instructions')
    fireEvent.click(screen.getByText('Saved'))
    await waitFor(() => expect(localStorage.getItem(key)).toBeNull())
  })
  it('lets a curator discard unreadable browser data and keep editing', async () => {
    localStorage.setItem(key, '{broken json')
    render(<Harness />)
    fireEvent.click(await screen.findByRole('button', { name: 'Discard recovery draft' }))
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Fresh draft' } })
    await screen.findByText(/Draft kept on this device/)
    expect(JSON.parse(localStorage.getItem(key)!).value.prompt).toBe('Fresh draft')
  })
  it('does not expose another account’s recovery draft', async () => {
    localStorage.setItem(key, JSON.stringify({ version: 1, updated: 'today', value: { prompt: 'Private draft' } }))
    render(<Harness owner="curator-b" />)
    expect(screen.queryByRole('button', { name: 'Resume draft' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Prompt')).toHaveValue('')
    expect(localStorage.getItem(key)).toContain('Private draft')
  })
  it('waits for editor hydration and allows explicitly discarding a recovered draft', async () => {
    localStorage.setItem(key, JSON.stringify({ version: 1, updated: 'today', value: { prompt: 'Keep me' } }))
    render(<Harness ready={false} />)
    expect(await screen.findByRole('button', { name: 'Resume draft' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Discard draft' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
    expect(localStorage.getItem(key)).toBeNull()
    expect(screen.getByLabelText('Prompt')).toHaveValue('')
  })
  it('does not overwrite a newer draft written by another tab', async () => {
    render(<Harness />)
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'This tab' } })
    const other = JSON.stringify({ version: 1, updated: 'later', value: { prompt: 'Other tab' } })
    localStorage.setItem(key, other)
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'This tab edited' } })
    await screen.findByText(/Another tab changed/)
    expect(localStorage.getItem(key)).toBe(other)
    expect(screen.getByLabelText('Prompt')).toHaveValue('This tab edited')
  })
  it('shows storage failures instead of claiming a draft was kept', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Quota exceeded') })
    render(<Harness />)
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Keep this visible' } })
    await screen.findByText(/could not keep a recovery draft/)
    expect(screen.queryByText(/Draft kept on this device/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Prompt')).toHaveValue('Keep this visible')
  })
})

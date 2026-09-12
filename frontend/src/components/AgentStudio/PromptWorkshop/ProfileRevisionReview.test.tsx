import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfileRevisionReview from './ProfileRevisionReview'
import { emptyOutputDraft } from './workshopOutputDraft'

const api = vi.hoisted(() => ({ getGenericProfile: vi.fn(), compareGenericProfileRevision: vi.fn() }))
vi.mock('@/services/genericProfileService', () => api)
const contract = { name: 'Details', semantic_class: 'detail', fields: [] }
const revision = { id: 'revision-1', profile_id: 'profile', revision: 1, fingerprint: 'sha256:one', contract }
const value = { ...emptyOutputDraft('profile_bound_generic'), profileContract: contract,
  profilePin: { profile_id: 'profile', profile_revision_id: revision.id, revision: 1, fingerprint: revision.fingerprint } }

describe('profile revision comparison', () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset())
    api.getGenericProfile.mockResolvedValue({ profile: { id: 'profile', head_revision: 2 }, revision: { ...revision, id: 'revision-2', revision: 2 }, can_edit: true })
    api.compareGenericProfileRevision.mockImplementation(async (_id, number) => ({
      base_revision: number === 1 ? revision : { ...revision, id: 'revision-2', revision: 2 },
      proposed_fingerprint: 'sha256:proposed',
      compatibility: [{ path: 'attributes.name', code: 'required_changed', breaking: true, before: false, after: true }],
    }))
  })

  it('compares the selected pin without loading or saving a revision', async () => {
    const load = vi.fn()
    render(<ProfileRevisionReview value={value} onLoadRevision={load} onMakeCopy={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Compare with selected revision' }))
    await screen.findByText(/Some edits change requirements/)
    expect(api.compareGenericProfileRevision).toHaveBeenCalledWith('profile', 1, contract)
    expect(load).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Load compared revision into draft' }))
    const dialog = screen.getByRole('dialog', { name: 'Replace draft structure?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Keep editing' }))
    expect(load).not.toHaveBeenCalled()
  })

  it('compares the current head and loads only after explicit confirmation', async () => {
    const load = vi.fn()
    render(<ProfileRevisionReview value={value} onLoadRevision={load} onMakeCopy={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Compare with latest revision' }))
    await screen.findByText('Saved revision 2 → your draft')
    expect(api.compareGenericProfileRevision).toHaveBeenCalledWith('profile', 2, contract)
    fireEvent.click(screen.getByRole('button', { name: 'Load compared revision into draft' }))
    fireEvent.click(screen.getByRole('button', { name: 'Load revision' }))
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ revision: expect.objectContaining({ revision: 2, id: 'revision-2' }) }))
  })

  it('marks a comparison stale after editing and prevents loading it', async () => {
    const props = { value, onLoadRevision: vi.fn(), onMakeCopy: vi.fn() }
    const view = render(<ProfileRevisionReview {...props} />)
    fireEvent.click(screen.getByRole('button', { name: 'Compare with selected revision' }))
    await screen.findByText(/Some edits change requirements/)
    view.rerender(<ProfileRevisionReview {...props} value={{ ...value, profileContract: { ...contract, name: 'New edit' } }} />)
    expect(screen.getByText(/comparison is out of date/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Load compared revision into draft' })).toBeDisabled()
    expect(props.onLoadRevision).not.toHaveBeenCalled()
  })

  it('keeps the draft when comparison fails and offers a separate-copy action', async () => {
    api.compareGenericProfileRevision.mockRejectedValue(new Error('Check required fields'))
    const copy = vi.fn()
    const load = vi.fn()
    render(<ProfileRevisionReview value={value} onLoadRevision={load} onMakeCopy={copy} />)
    fireEvent.click(screen.getByRole('button', { name: 'Compare with selected revision' }))
    await screen.findByText(/Check required fields/)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Compare with selected revision' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Use a separate profile copy' }))
    expect(copy).toHaveBeenCalledOnce()
    expect(load).not.toHaveBeenCalled()
  })
})

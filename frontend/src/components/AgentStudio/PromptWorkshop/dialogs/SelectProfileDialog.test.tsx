import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SelectProfileDialog from './SelectProfileDialog'

const api = vi.hoisted(() => ({ listGenericProfiles: vi.fn(), getGenericProfile: vi.fn(), getGenericProfileRevision: vi.fn() }))
vi.mock('@/services/genericProfileService', () => api)

const profile = { id: 'profile-1', name: 'Collected details', semantic_class: 'detail', head_revision: 2 }
const revision = { id: 'revision-2', profile_id: profile.id, revision: 2, fingerprint: 'sha256:revision',
  contract: { name: 'Saved details', description: 'One collected detail', semantic_class: 'detail', fields: [{ key: 'source_label', display_name: 'Source label', value_schema: { kind: 'string' } }] } }

describe('existing Output Structure picker', () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset())
    api.listGenericProfiles.mockResolvedValue({ profiles: [profile], next_cursor: null })
    api.getGenericProfile.mockResolvedValue({ profile, revision, can_edit: true, compatibility: [] })
    api.getGenericProfileRevision.mockResolvedValue(revision)
  })

  it('previews the exact listed revision without applying until Use', async () => {
    const select = vi.fn()
    api.getGenericProfile.mockResolvedValue({ profile: { ...profile, head_revision: 3 }, revision: { ...revision, revision: 3 }, can_edit: false, compatibility: [] })
    render(<SelectProfileDialog onSelect={select} onClose={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Use this revision' })).toBeDisabled()
    fireEvent.click(await screen.findByRole('button', { name: /Collected details/ }))
    await screen.findByText('Saved details · revision 2')
    expect(api.getGenericProfileRevision).toHaveBeenCalledWith('profile-1', 2)
    expect(screen.getByText(/A newer revision exists/)).toBeInTheDocument()
    expect(screen.getByText(/This source is not editable/)).toBeInTheDocument()
    expect(select).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Use this revision' }))
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ revision, can_edit: false }))
  })

  it('retries the same failed page without losing earlier choices', async () => {
    api.listGenericProfiles.mockResolvedValueOnce({ profiles: [profile], next_cursor: 'opaque-next' })
      .mockRejectedValueOnce(new Error('Temporarily unavailable'))
      .mockResolvedValueOnce({ profiles: [{ ...profile, id: 'profile-2', name: 'Second structure' }], next_cursor: null })
    render(<SelectProfileDialog onSelect={vi.fn()} onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Load more structures' }))
    await screen.findByText('Temporarily unavailable')
    expect(screen.getByRole('button', { name: /Collected details/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByRole('button', { name: /Second structure/ })
    expect(api.listGenericProfiles.mock.calls.map(([cursor]) => cursor)).toEqual([undefined, 'opaque-next', 'opaque-next'])
  })

  it('does not apply a failed or mismatched revision and allows retry', async () => {
    api.getGenericProfileRevision.mockResolvedValueOnce({ ...revision, profile_id: 'wrong-profile' })
    const select = vi.fn()
    render(<SelectProfileDialog onSelect={select} onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Collected details/ }))
    await screen.findByText(/selected revision identity changed/)
    expect(screen.getByRole('button', { name: 'Use this revision' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /Collected details/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Use this revision' })).toBeEnabled())
    expect(select).not.toHaveBeenCalled()
  })

  it('ignores in-flight results after cancellation and unmount', async () => {
    let finish!: (value: unknown) => void
    api.getGenericProfileRevision.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const select = vi.fn()
    const close = vi.fn()
    const view = render(<SelectProfileDialog onSelect={select} onClose={close} />)
    fireEvent.click(await screen.findByRole('button', { name: /Collected details/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(close).toHaveBeenCalledOnce()
    view.unmount()
    await act(async () => finish(revision))
    expect(select).not.toHaveBeenCalled()
  })
})

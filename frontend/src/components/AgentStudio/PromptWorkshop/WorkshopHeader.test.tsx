import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import WorkshopHeader, { type WorkshopHeaderProps } from './WorkshopHeader'

function renderHeader(overrides: Partial<WorkshopHeaderProps> = {}) {
  const props: WorkshopHeaderProps = {
    icon: 'Dv',
    name: 'GROUP_A disease validator',
    originLabel: 'Template: disease_validator',
    saveState: 'idle',
    lastSavedAt: null,
    dirty: false,
    canSave: false,
    canDelete: true,
    saving: false,
    onOpen: vi.fn(),
    onNew: vi.fn(),
    onSave: vi.fn(),
    onSaveAs: vi.fn(),
    onManage: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  }
  render(<WorkshopHeader {...props} />)
  return props
}

describe('WorkshopHeader', () => {
  it('shows the agent name and origin line', () => {
    renderHeader()
    expect(screen.getByRole('heading', { name: 'GROUP_A disease validator' })).toBeInTheDocument()
    expect(screen.getByText('Template: disease_validator')).toBeInTheDocument()
  })

  it('falls back to "New agent" when the name is blank', () => {
    renderHeader({ name: '   ', originLabel: 'Not saved yet' })
    expect(screen.getByRole('heading', { name: 'New agent' })).toBeInTheDocument()
  })

  it('renders the unsaved pill and enables Save when dirty', () => {
    renderHeader({ dirty: true, canSave: true })
    expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes')
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('disables Save and hides the pill when clean with nothing saved this session', () => {
    renderHeader({ dirty: false, canSave: false })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('shows the saving pill while a save runs', () => {
    renderHeader({ saving: true, saveState: 'saving', canSave: false })
    expect(screen.getByRole('status')).toHaveTextContent('Saving…')
  })

  it('shows the saved pill with a relative time', () => {
    renderHeader({ saveState: 'saved', lastSavedAt: Date.now() - 2 * 60_000 })
    expect(screen.getByRole('status')).toHaveTextContent('Saved 2 min ago')
  })

  it('shows the failed pill after a save error', () => {
    renderHeader({ saveState: 'failed', dirty: true, canSave: true })
    expect(screen.getByRole('status')).toHaveTextContent('Save failed')
  })

  it('wires Open, New, and Save', () => {
    const props = renderHeader({ canSave: true })
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    fireEvent.click(screen.getByRole('button', { name: 'New' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(props.onOpen).toHaveBeenCalledTimes(1)
    expect(props.onNew).toHaveBeenCalledTimes(1)
    expect(props.onSave).toHaveBeenCalledTimes(1)
  })

  it('exposes Save as, Manage, and Delete in the overflow menu', async () => {
    const props = renderHeader()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Save as…' }))
    expect(props.onSaveAs).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Manage agents…' }))
    expect(props.onManage).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete agent' }))
    expect(props.onDelete).toHaveBeenCalledTimes(1)
  })

  it('disables Delete agent for an unsaved draft', async () => {
    renderHeader({ canDelete: false })
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(await screen.findByRole('menuitem', { name: 'Delete agent' })).toHaveAttribute('aria-disabled', 'true')
  })

  it('does not render a File menu or an Editing caption', () => {
    renderHeader()
    expect(screen.queryByText('File')).not.toBeInTheDocument()
    expect(screen.queryByText(/Editing:/)).not.toBeInTheDocument()
  })
})

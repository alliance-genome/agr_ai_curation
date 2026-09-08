import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import SaveVersionDialog from './SaveVersionDialog'

describe('SaveVersionDialog', () => {
  it('asks for an optional note and lists the changed sections for an existing agent', () => {
    const onConfirm = vi.fn()
    render(
      <SaveVersionDialog
        open
        agentName="GROUP_A disease validator"
        nextVersion={7}
        isNewAgent={false}
        changedSections={['Your prompt', 'GROUP_A instructions']}
        saving={false}
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />
    )
    const dialog = screen.getByRole('dialog', { name: /Save as version 7/ })
    expect(dialog).toHaveTextContent('GROUP_A disease validator')
    expect(dialog).toHaveTextContent('Changed since v6: Your prompt, GROUP_A instructions.')
    const note = within(dialog).getByLabelText('Note (optional)')
    expect(note).toHaveFocus()
    fireEvent.change(note, { target: { value: 'Strict primary-label rule' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(onConfirm).toHaveBeenCalledWith('Strict primary-label rule')
  })

  it('submits on Enter from the note field', () => {
    const onConfirm = vi.fn()
    render(
      <SaveVersionDialog open agentName="A" nextVersion={2} isNewAgent={false} changedSections={['Setup']} saving={false} onConfirm={onConfirm} onClose={vi.fn()} />
    )
    fireEvent.keyDown(screen.getByLabelText('Note (optional)'), { key: 'Enter' })
    expect(onConfirm).toHaveBeenCalledWith('')
  })

  it('skips the note for a brand-new agent', () => {
    const onConfirm = vi.fn()
    render(
      <SaveVersionDialog open agentName="" nextVersion={1} isNewAgent changedSections={[]} saving={false} onConfirm={onConfirm} onClose={vi.fn()} />
    )
    const dialog = screen.getByRole('dialog', { name: /Save new agent/ })
    expect(dialog).toHaveTextContent('New agent')
    expect(dialog).toHaveTextContent('Creates version 1 of this agent.')
    expect(within(dialog).queryByLabelText('Note (optional)')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(onConfirm).toHaveBeenCalledWith('')
  })

  it('closes on Cancel and Escape but not while saving', () => {
    const onClose = vi.fn()
    const { rerender } = render(
      <SaveVersionDialog open agentName="A" nextVersion={2} isNewAgent={false} changedSections={[]} saving={false} onConfirm={vi.fn()} onClose={onClose} />
    )
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(2)

    rerender(
      <SaveVersionDialog open agentName="A" nextVersion={2} isNewAgent={false} changedSections={[]} saving onConfirm={vi.fn()} onClose={onClose} />
    )
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})

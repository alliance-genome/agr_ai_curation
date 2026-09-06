import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import WorkshopStartScreen from './WorkshopStartScreen'

describe('WorkshopStartScreen', () => {
  it('offers the three starting points and reports the chosen one', () => {
    const onChoose = vi.fn()
    render(<WorkshopStartScreen onChoose={onChoose} hasTemplates hasSavedAgents />)

    expect(screen.getByRole('group', { name: 'Start a new agent' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /From a template/ }))
    fireEvent.click(screen.getByRole('button', { name: /From scratch/ }))
    fireEvent.click(screen.getByRole('button', { name: /Clone one of yours/ }))
    expect(onChoose.mock.calls.map((call) => call[0])).toEqual(['template', 'scratch', 'clone'])
  })

  it('offers custom extraction and explains the distinction without starting a draft from help', async () => {
    const start = vi.fn()
    render(<WorkshopStartScreen onChoose={vi.fn()} onCustomExtraction={start} hasTemplates hasSavedAgents />)
    fireEvent.click(screen.getByRole('button', { name: 'About custom extraction' }))
    const dialog = screen.getByRole('dialog', { name: 'Custom data extraction' })
    expect(within(dialog).getByText(/not automatically ready for Alliance submission/)).toBeInTheDocument()
    expect(start).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    fireEvent.click(await screen.findByRole('button', { name: /^Custom data extraction Choose/ }))
    expect(start).toHaveBeenCalledTimes(1)
  })

  it('disables cloning when there are no saved agents and says why', () => {
    render(<WorkshopStartScreen onChoose={vi.fn()} hasTemplates hasSavedAgents={false} />)
    const clone = screen.getByRole('button', { name: /Clone one of yours/ })
    expect(clone).toBeDisabled()
    expect(clone).toHaveTextContent('You have no saved agents yet.')
  })

  it('disables the template choice when no templates are installed', () => {
    render(<WorkshopStartScreen onChoose={vi.fn()} hasTemplates={false} hasSavedAgents />)
    expect(screen.getByRole('button', { name: /From a template/ })).toBeDisabled()
  })
})

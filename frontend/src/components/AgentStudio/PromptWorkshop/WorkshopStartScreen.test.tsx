import { fireEvent, render, screen } from '@testing-library/react'
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

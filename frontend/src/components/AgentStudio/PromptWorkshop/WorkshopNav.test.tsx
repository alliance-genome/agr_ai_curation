import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import WorkshopNav, { type WorkshopNavProps } from './WorkshopNav'

function renderNav(overrides: Partial<WorkshopNavProps> = {}) {
  const props: WorkshopNavProps = {
    section: 'setup',
    onSectionChange: vi.fn(),
    dirty: { setup: false, prompt: false, tools: false, groups: [], any: false },
    toolCount: 3,
    versionCount: 6,
    onAskClaude: vi.fn(),
    ...overrides,
  }
  render(<WorkshopNav {...props} />)
  return props
}

describe('WorkshopNav', () => {
  it('renders a navigation landmark with the four sections and marks the current one', () => {
    renderNav({ section: 'prompt' })
    const nav = screen.getByRole('navigation', { name: 'Agent Workshop sections' })
    expect(nav).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Setup/ })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('button', { name: /^Prompt/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: /^Tools/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Versions/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Reference/ })).not.toBeInTheDocument()
  })

  it('shows attached tool and version counts as hints', () => {
    renderNav()
    expect(screen.getByRole('button', { name: 'Tools, 3 attached' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Versions, 6' })).toBeInTheDocument()
  })

  it('flags sections with unsaved edits, including group overrides under Prompt', () => {
    renderNav({ dirty: { setup: true, prompt: false, tools: true, groups: ['GROUP_A'], any: true } })
    expect(screen.getByRole('button', { name: 'Setup, unsaved edits' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prompt, unsaved edits' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tools, 3 attached, unsaved edits' })).toBeInTheDocument()
  })

  it('switches sections and opens the AI Chat discussion', () => {
    const props = renderNav()
    fireEvent.click(screen.getByRole('button', { name: /^Tools/ }))
    expect(props.onSectionChange).toHaveBeenCalledWith('tools')
    fireEvent.click(screen.getByRole('button', { name: 'Ask AI Chat' }))
    expect(props.onAskClaude).toHaveBeenCalledTimes(1)
  })

  it('omits the Help group when AI Chat is unavailable', () => {
    renderNav({ onAskClaude: undefined })
    expect(screen.queryByRole('button', { name: 'Ask AI Chat' })).not.toBeInTheDocument()
    expect(screen.queryByText('Help')).not.toBeInTheDocument()
  })
})

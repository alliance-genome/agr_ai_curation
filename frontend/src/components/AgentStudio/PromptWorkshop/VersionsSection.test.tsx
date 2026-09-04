import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { buildExecutionRevision as buildVersion } from '@/test/fixtures/agentExecutionRevision'

import VersionsSection from './VersionsSection'

describe('VersionsSection', () => {
  it('renders versions newest first, marks the current one, and reverts per row', () => {
    const onRevert = vi.fn()
    render(
      <VersionsSection
        versions={[{ ...buildVersion(1), notes: 'Curator saved note' }, buildVersion(3), buildVersion(2)]}
        currentRevisionId="version-2"
        hasAgent
        saving={false}
        onRevert={onRevert}
      />
    )
    const table = screen.getByRole('table', { name: 'Version history' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('v3'),
      expect.stringContaining('v2'),
      expect.stringContaining('v1'),
    ])
    expect(rows[0]).not.toHaveAttribute('aria-current')
    expect(rows[1]).toHaveAttribute('aria-current', 'true')
    expect(rows[1]).toHaveTextContent('Current')
    expect(rows[1]).toHaveTextContent('gpt-5.6-terra · 0 tools · No structured output')
    expect(within(rows[1]).queryByRole('button')).not.toBeInTheDocument()

    fireEvent.click(within(rows[2]).getByRole('button', { name: 'Restore configuration 1' }))
    expect(onRevert).toHaveBeenCalledWith({ ...buildVersion(1), notes: 'Curator saved note' })
    expect(rows[2]).toHaveTextContent('Curator saved note')
    expect(screen.getByText(/Restore copies the complete saved configuration/)).toBeInTheDocument()
  })

  it('disables Revert while a save runs', () => {
    render(<VersionsSection versions={[buildVersion(1), buildVersion(2)]} currentRevisionId="version-2" hasAgent saving onRevert={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Restore configuration 1' })).toBeDisabled()
  })

  it('explains the empty states', () => {
    const { rerender } = render(<VersionsSection versions={[]} hasAgent={false} saving={false} onRevert={vi.fn()} />)
    expect(screen.getByText('Save this agent to start its version history.')).toBeInTheDocument()
    rerender(<VersionsSection versions={[]} hasAgent saving={false} onRevert={vi.fn()} />)
    expect(screen.getByText('No saved configurations yet.')).toBeInTheDocument()
  })

  it('distinguishes loading and errors and exposes older pages', () => {
    const props = { versions: [buildVersion(1)], hasAgent: true, saving: false, onRevert: vi.fn() }
    const { rerender } = render(<VersionsSection {...props} loading />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading saved configurations')
    expect(screen.getByRole('button', { name: 'Restore configuration 1' })).toBeDisabled()
    const onLoadMore = vi.fn()
    const onRetry = vi.fn()
    rerender(<VersionsSection {...props} error="History unavailable" hasMore onLoadMore={onLoadMore} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('History unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading configurations' }))
    expect(onRetry).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: 'Load older configurations' })).not.toBeInTheDocument()
    rerender(<VersionsSection {...props} hasMore onLoadMore={onLoadMore} />)
    fireEvent.click(screen.getByRole('button', { name: 'Load older configurations' }))
    expect(onLoadMore).toHaveBeenCalledOnce()
  })
})

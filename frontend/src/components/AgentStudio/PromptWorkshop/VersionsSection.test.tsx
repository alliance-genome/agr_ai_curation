import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { CustomAgentVersion } from '@/types/promptExplorer'

import VersionsSection from './VersionsSection'

function buildVersion(version: number, notes?: string): CustomAgentVersion {
  return {
    id: `v-${version}`,
    custom_agent_id: 'agent',
    version,
    custom_prompt: 'p',
    group_prompt_overrides: {},
    allowed_group_ids: [],
    notes,
    created_at: `2026-08-2${version}T10:00:00Z`,
  }
}

describe('VersionsSection', () => {
  it('renders versions newest first, marks the current one, and reverts per row', () => {
    const onRevert = vi.fn()
    render(
      <VersionsSection
        versions={[buildVersion(1, 'First'), buildVersion(3), buildVersion(2, 'Second')]}
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
    expect(rows[0]).toHaveAttribute('aria-current', 'true')
    expect(rows[0]).toHaveTextContent('Current')
    expect(rows[0]).toHaveTextContent('No note')
    expect(within(rows[0]).queryByRole('button')).not.toBeInTheDocument()

    fireEvent.click(within(rows[2]).getByRole('button', { name: 'Revert to version 1' }))
    expect(onRevert).toHaveBeenCalledWith(1)
    expect(screen.getByText('Revert creates a new version from the old one. Nothing is deleted.')).toBeInTheDocument()
  })

  it('disables Revert while a save runs', () => {
    render(<VersionsSection versions={[buildVersion(1), buildVersion(2)]} hasAgent saving onRevert={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Revert to version 1' })).toBeDisabled()
  })

  it('explains the empty states', () => {
    const { rerender } = render(<VersionsSection versions={[]} hasAgent={false} saving={false} onRevert={vi.fn()} />)
    expect(screen.getByText('Save this agent to start its version history.')).toBeInTheDocument()
    rerender(<VersionsSection versions={[]} hasAgent saving={false} onRevert={vi.fn()} />)
    expect(screen.getByText('No versions yet.')).toBeInTheDocument()
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GenericProfileContract } from '@/services/genericProfileService'
import ProfileCandidateComparison, { type ProfileCandidateStatus } from './ProfileCandidateComparison'

const before: GenericProfileContract = { name: 'Record', semantic_class: 'record', fields: [
  { key: 'title', source_labels: ['Heading'], value_schema: { kind: 'string' } },
] }
const candidate = { ...before, fields: [{ ...before.fields[0], source_labels: ['Paper title'] }] }

describe('profile candidate comparison seam', () => {
  it.each<ProfileCandidateStatus>(['proposed', 'applied', 'canceled', 'stale', 'undone'])('renders injected %s state without owning mutation', (status) => {
    const apply = vi.fn(), cancel = vi.fn(), undo = vi.fn()
    render(<ProfileCandidateComparison before={before} candidate={candidate} origin="AI Chat fixture" status={status}
      onApply={apply} onCancel={cancel} onUndo={undo} />)
    expect(screen.getByText('Source: AI Chat fixture')).toBeInTheDocument()
    expect(screen.getByText('fields[0].source_labels[0]')).toBeInTheDocument()
    expect(screen.getByText('Before: Heading')).toBeInTheDocument()
    expect(screen.getByText('After: Paper title')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply to draft' }).hasAttribute('disabled')).toBe(status !== 'proposed')
    expect(screen.getByRole('button', { name: 'Undo applied changes' }).hasAttribute('disabled')).toBe(status !== 'applied')
    if (status === 'proposed') { fireEvent.click(screen.getByRole('button', { name: 'Apply to draft' })); expect(apply).toHaveBeenCalledOnce() }
    expect(before.fields[0].source_labels).toEqual(['Heading'])
    expect(candidate.fields[0].source_labels).toEqual(['Paper title'])
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { buildExecutionRevision } from '@/test/fixtures/agentExecutionRevision'
import SavedExecutionSummary from './SavedExecutionSummary'

describe('saved executable summary', () => {
  it('shows saved model settings, tools and exact profile identity separately from unsaved edits', () => {
    const revision = buildExecutionRevision(3)
    revision.snapshot.model_temperature = 0
    revision.snapshot.tool_ids = ['read_chunk', 'finalize_extraction']
    revision.snapshot.output_contract = { output_state: 'structured_extraction', output_mode: 'profile_bound_generic',
      generic_profile_ref: { profile_id: 'profile-1', profile_revision_id: 'profile-revision-2', revision: 2, fingerprint: 'sha256:profile' } }
    render(<SavedExecutionSummary revision={revision} />)
    fireEvent.click(screen.getByText('Saved configuration · revision 3'))
    expect(screen.getByText(/not the current unsaved draft/)).toBeInTheDocument()
    expect(screen.getByText(/temperature: 0/)).toHaveTextContent(revision.snapshot.model_id)
    expect(screen.getByText(/Tools:/)).toHaveTextContent('read_chunk, finalize_extraction')
    expect(screen.getByText('Custom Output Structure revision 2')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Technical revision identifiers'))
    expect(screen.getByText(/Agent revision:/)).toHaveTextContent(revision.id)
    expect(screen.getByText(/Profile: profile-1/)).toHaveTextContent('profile-revision-2')
  })

  it('does not confuse no output and unprofiled generic extraction', () => {
    const revision = buildExecutionRevision(1)
    revision.snapshot.output_contract = { output_state: 'none' }
    const view = render(<SavedExecutionSummary revision={revision} />)
    expect(screen.getByText('No structured output')).toBeInTheDocument()
    view.rerender(<SavedExecutionSummary revision={{ ...revision, snapshot: { ...revision.snapshot,
      output_contract: { output_state: 'structured_extraction', output_mode: 'unprofiled_generic' } } }} />)
    expect(screen.getByText('Flexible generic extraction (no profile)')).toBeInTheDocument()
  })
})

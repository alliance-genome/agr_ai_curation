import type { ComponentProps } from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type { CurationReviewSession } from '@/features/curation/types'
import theme from '@/theme'
import WorkspaceHeader from './WorkspaceHeader'

function buildSession(): CurationReviewSession {
  return {
    session_id: 'session-1',
    status: 'in_progress',
    adapter: {
      adapter_key: 'gene',
      profile_key: 'human',
      display_label: 'Gene',
      profile_label: 'Human',
      color_token: 'green',
      metadata: {},
    },
    document: {
      document_id: 'document-1',
      title: 'Annotation-ready paper',
      pmid: '123456',
      doi: '10.1000/example',
      pdf_url: '/api/documents/document-1.pdf',
      viewer_url: '/api/documents/document-1.pdf',
    },
    progress: {
      total_candidates: 5,
      reviewed_candidates: 3,
      pending_candidates: 2,
      accepted_candidates: 2,
      rejected_candidates: 1,
      manual_candidates: 0,
    },
    current_candidate_id: 'candidate-1',
    prepared_at: '2026-03-20T12:00:00Z',
    warnings: [],
    tags: [],
    session_version: 2,
    extraction_results: [],
  }
}

function renderHeader(props?: Partial<ComponentProps<typeof WorkspaceHeader>>) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <WorkspaceHeader session={buildSession()} {...props} />
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('WorkspaceHeader', () => {
  it('renders the title, metadata, badges, and navigation placeholders', () => {
    renderHeader()

    expect(
      screen.getByRole('link', { name: /back to inventory/i }),
    ).toHaveAttribute('href', '/curation')
    expect(screen.getByText('Annotation-ready paper')).toBeInTheDocument()
    expect(screen.getByText('PMID 123456 • DOI 10.1000/example')).toBeInTheDocument()
    expect(screen.getByText('Gene / Human')).toBeInTheDocument()
    expect(screen.getByText('3/5')).toBeInTheDocument()
    expect(screen.getByText('In Progress')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /previous session/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /next session/i })).toBeDisabled()
  })

  it('calls the provided session navigation callbacks when enabled', async () => {
    const user = userEvent.setup()
    const onPreviousSession = vi.fn()
    const onNextSession = vi.fn()

    renderHeader({
      nextDisabled: false,
      onNextSession,
      onPreviousSession,
      previousDisabled: false,
    })

    await user.click(screen.getByRole('button', { name: /previous session/i }))
    await user.click(screen.getByRole('button', { name: /next session/i }))

    expect(onPreviousSession).toHaveBeenCalledTimes(1)
    expect(onNextSession).toHaveBeenCalledTimes(1)
  })

  it('renders a custom navigation slot when provided', () => {
    renderHeader({
      navigationSlot: <div>Queue navigation slot</div>,
    })

    expect(screen.getByText('Queue navigation slot')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /previous session/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /next session/i })).not.toBeInTheDocument()
  })
})

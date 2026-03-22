import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationQueueNavigationRequest, CurationQueueNavigationState } from '../services/curationQueueNavigationService'
import type { CurationNextSessionResponse, CurationSessionSummary } from '../types'
import QueueNavigationButton from './QueueNavigationButton'

const theme = createTheme()

const queueRequest: CurationQueueNavigationRequest = {
  filters: {
    statuses: ['in_progress'],
    adapter_keys: ['gene'],
    profile_keys: [],
    domain_keys: [],
    curator_ids: [],
    tags: [],
    flow_run_id: null,
    document_id: null,
    search: 'APOE',
    prepared_between: null,
    last_worked_between: null,
    saved_view_id: null,
  },
  sort_by: 'prepared_at',
  sort_direction: 'desc',
}

function buildSessionSummary(sessionId: string): CurationSessionSummary {
  return {
    session_id: sessionId,
    status: 'in_progress',
    adapter: {
      adapter_key: 'gene',
      profile_key: 'alpha',
      display_label: 'Gene Adapter',
      profile_label: 'Alpha Profile',
      color_token: 'teal',
      metadata: {},
    },
    document: {
      document_id: `doc-${sessionId}`,
      title: `Document ${sessionId}`,
      pmid: null,
      doi: null,
      citation_label: null,
      pdf_url: null,
      viewer_url: null,
      publication_year: 2026,
    },
    flow_run_id: null,
    progress: {
      total_candidates: 4,
      reviewed_candidates: 2,
      pending_candidates: 2,
      accepted_candidates: 1,
      rejected_candidates: 1,
      manual_candidates: 0,
    },
    validation: null,
    evidence: null,
    current_candidate_id: null,
    assigned_curator: null,
    created_by: null,
    prepared_at: '2026-03-20T12:00:00Z',
    last_worked_at: '2026-03-21T12:00:00Z',
    notes: null,
    warnings: [],
    tags: [],
  }
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

function WorkspaceDestination() {
  const params = useParams()
  const location = useLocation()
  const queueState = location.state as CurationQueueNavigationState | null

  return (
    <>
      <div>Workspace route for {params.sessionId}</div>
      <div data-testid="location-state">
        {JSON.stringify(queueState)}
      </div>
    </>
  )
}

function renderButton() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={['/curation']}>
          <Routes>
            <Route path="/curation" element={<QueueNavigationButton request={queueRequest} />} />
            <Route path="/curation/:sessionId" element={<WorkspaceDestination />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

describe('QueueNavigationButton', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('navigates to the returned session and preserves queue state', async () => {
    const user = userEvent.setup()
    const response: CurationNextSessionResponse = {
      session: buildSessionSummary('session-2'),
      queue_context: {
        filters: queueRequest.filters ?? undefined,
        sort_by: 'prepared_at',
        sort_direction: 'desc',
        position: 1,
        total_sessions: 3,
        previous_session_id: null,
        next_session_id: 'session-3',
      },
    }

    vi.mocked(global.fetch).mockResolvedValue(jsonResponse(response))

    renderButton()

    await user.click(screen.getByRole('button', { name: /next unreviewed/i }))

    expect(await screen.findByText('Workspace route for session-2')).toBeInTheDocument()
    expect(screen.getByTestId('location-state')).toHaveTextContent('"position":1')
    expect(screen.getByTestId('location-state')).toHaveTextContent('"next_session_id":"session-3"')

    await waitFor(() => {
      expect(vi.mocked(global.fetch)).toHaveBeenCalledWith(
        expect.stringContaining('/api/curation-workspace/sessions/next?'),
        expect.objectContaining({
          credentials: 'include',
        }),
      )
    })
  })

  it('disables the button and shows a message when the queue is exhausted', async () => {
    const user = userEvent.setup()
    const response: CurationNextSessionResponse = {
      session: null,
      queue_context: {
        filters: queueRequest.filters ?? undefined,
        sort_by: 'prepared_at',
        sort_direction: 'desc',
        total_sessions: 0,
      },
    }

    vi.mocked(global.fetch).mockResolvedValue(jsonResponse(response))

    renderButton()

    const button = screen.getByRole('button', { name: /next unreviewed/i })
    await user.click(button)

    expect(await screen.findByText('No more sessions match the current queue.')).toBeInTheDocument()
    expect(button).toBeDisabled()
  })
})

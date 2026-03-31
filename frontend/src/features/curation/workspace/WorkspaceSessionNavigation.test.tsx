import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationQueueNavigationRequest, CurationQueueNavigationState } from '../services/curationQueueNavigationService'
import type { CurationNextSessionResponse, CurationSessionSummary } from '../types'
import WorkspaceSessionNavigation from './WorkspaceSessionNavigation'

const theme = createTheme()

const queueRequest: CurationQueueNavigationRequest = {
  filters: {
    statuses: ['in_progress'],
    adapter_keys: ['gene'],
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
      display_label: 'Gene Adapter',
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

function LocationProbe() {
  const params = useParams()
  const location = useLocation()
  const queueState = location.state as CurationQueueNavigationState | null

  return (
    <>
      <div data-testid="location-path">{location.pathname}</div>
      <div data-testid="location-session">{params.sessionId}</div>
      <div data-testid="location-state">{JSON.stringify(queueState)}</div>
    </>
  )
}

function renderNavigation(props?: {
  currentSessionId?: string
  queueRequest?: CurationQueueNavigationRequest | null
}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  })

  const currentSessionId = props?.currentSessionId ?? 'session-2'
  const queueRequestProp = props && 'queueRequest' in props
    ? props.queueRequest
    : queueRequest

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[`/curation/${currentSessionId}`]}>
          <Routes>
            <Route
              path="/curation/:sessionId"
              element={(
                <>
                  <WorkspaceSessionNavigation
                    currentSessionId={currentSessionId}
                    queueRequest={queueRequestProp}
                  />
                  <LocationProbe />
                </>
              )}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

describe('WorkspaceSessionNavigation', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost')
      const direction = url.searchParams.get('direction')

      if (direction === 'previous') {
        const response: CurationNextSessionResponse = {
          session: buildSessionSummary('session-1'),
          queue_context: {
            filters: queueRequest.filters ?? undefined,
            sort_by: 'prepared_at',
            sort_direction: 'desc',
            position: 1,
            total_sessions: 3,
            previous_session_id: null,
            next_session_id: 'session-2',
          },
        }

        return jsonResponse(response)
      }

      if (direction === 'next') {
        const response: CurationNextSessionResponse = {
          session: buildSessionSummary('session-3'),
          queue_context: {
            filters: queueRequest.filters ?? undefined,
            sort_by: 'prepared_at',
            sort_direction: 'desc',
            position: 3,
            total_sessions: 3,
            previous_session_id: 'session-2',
            next_session_id: null,
          },
        }

        return jsonResponse(response)
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`)
    }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('loads adjacent sessions, shows queue position, and navigates to the next session', async () => {
    const user = userEvent.setup()
    renderNavigation()

    expect(await screen.findByText('Queue 2 of 3')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /next session/i }))

    await waitFor(() => {
      expect(screen.getByTestId('location-path')).toHaveTextContent('/curation/session-3')
    })

    expect(screen.getByTestId('location-state')).toHaveTextContent('"position":3')
    expect(screen.getByTestId('location-state')).toHaveTextContent('"previous_session_id":"session-2"')
  })

  it('keeps navigation disabled and explains why when no queue context is available', () => {
    renderNavigation({ queueRequest: null })

    expect(
      screen.getByText(
        'Queue navigation is available when you open a session from the inventory queue.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /previous session/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /next session/i })).toBeDisabled()
    expect(vi.mocked(global.fetch)).not.toHaveBeenCalled()
  })
})

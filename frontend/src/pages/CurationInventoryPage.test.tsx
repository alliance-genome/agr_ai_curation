import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CurationInventoryPage from './CurationInventoryPage'
import type {
  CurationSavedViewListResponse,
  CurationSessionListResponse,
  CurationSessionStatsResponse,
} from '../features/curation/types'

const theme = createTheme()

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

function SessionDestination() {
  const params = useParams()
  const location = useLocation()

  return (
    <>
      <div>Workspace route for {params.sessionId}</div>
      <div data-testid="location-state">{JSON.stringify(location.state)}</div>
    </>
  )
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={['/curation']}>
          <Routes>
            <Route path="/curation" element={<CurationInventoryPage />} />
            <Route path="/curation/:sessionId" element={<SessionDestination />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  )
}

describe('CurationInventoryPage', () => {
  const listResponse: CurationSessionListResponse = {
    sessions: [
      {
        session_id: 'session-1',
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
          document_id: 'doc-1',
          title: 'Alpha paper',
          pmid: '123456',
          doi: null,
          citation_label: null,
          pdf_url: null,
          viewer_url: null,
          publication_year: 2025,
        },
        flow_run_id: 'flow-1',
        progress: {
          total_candidates: 8,
          reviewed_candidates: 5,
          pending_candidates: 3,
          accepted_candidates: 4,
          rejected_candidates: 1,
          manual_candidates: 0,
        },
        validation: {
          state: 'completed',
          counts: {
            validated: 5,
            ambiguous: 1,
            not_found: 1,
            invalid_format: 0,
            conflict: 0,
            skipped: 1,
            overridden: 0,
          },
          last_validated_at: '2026-03-20T10:00:00Z',
          stale_field_keys: [],
          warnings: [],
        },
        evidence: {
          total_anchor_count: 10,
          resolved_anchor_count: 8,
          viewer_highlightable_anchor_count: 7,
          quality_counts: {
            exact_quote: 3,
            normalized_quote: 2,
            section_only: 1,
            page_only: 1,
            document_only: 1,
            unresolved: 2,
          },
          degraded: false,
          warnings: [],
        },
        current_candidate_id: 'candidate-1',
        assigned_curator: {
          actor_id: 'curator-1',
          display_name: 'Alex Curator',
          email: 'alex@example.org',
        },
        created_by: {
          actor_id: 'curator-2',
          display_name: 'Jamie Creator',
          email: 'jamie@example.org',
        },
        prepared_at: '2026-03-18T10:00:00Z',
        last_worked_at: '2026-03-20T09:00:00Z',
        notes: 'Ready for review',
        warnings: [],
        tags: ['priority'],
      },
    ],
    page_info: {
      page: 1,
      page_size: 25,
      total_items: 1,
      total_pages: 1,
      has_next_page: false,
      has_previous_page: false,
    },
    applied_filters: {
      statuses: [],
      adapter_keys: [],
      profile_keys: [],
      domain_keys: [],
      curator_ids: [],
      tags: [],
      flow_run_id: null,
      origin_session_id: null,
      document_id: null,
      search: null,
      prepared_between: null,
      last_worked_between: null,
      saved_view_id: null,
    },
    sort_by: 'prepared_at',
    sort_direction: 'desc',
    flow_run_groups: [],
  }

  const statsResponse: CurationSessionStatsResponse = {
    stats: {
      total_sessions: 12,
      domain_count: 2,
      new_sessions: 4,
      in_progress_sessions: 3,
      ready_for_submission_sessions: 2,
      paused_sessions: 1,
      submitted_sessions: 1,
      rejected_sessions: 1,
      assigned_to_current_user: 2,
      assigned_to_others: 4,
      submitted_last_7_days: 1,
    },
    applied_filters: listResponse.applied_filters,
  }

  const savedViewResponse: CurationSavedViewListResponse = {
    views: [
      {
        view_id: 'saved-view-1',
        name: 'My pending sessions',
        description: 'Only my active gene sessions',
        filters: {
          statuses: ['new'],
          adapter_keys: ['gene'],
          profile_keys: ['alpha'],
          domain_keys: [],
          curator_ids: [],
          tags: [],
          flow_run_id: null,
          origin_session_id: 'chat-session-7',
          document_id: null,
          search: 'pending',
          prepared_between: null,
          last_worked_between: null,
          saved_view_id: null,
        },
        sort_by: 'adapter',
        sort_direction: 'asc',
        is_default: false,
        created_by: {
          actor_id: 'curator-1',
          display_name: 'Alex Curator',
          email: 'alex@example.org',
        },
        created_at: '2026-03-22T13:20:00Z',
        updated_at: '2026-03-22T13:20:00Z',
      },
    ],
  }

  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)

        if (url.startsWith('/api/curation-workspace/sessions/stats')) {
          return jsonResponse(statsResponse)
        }

        if (url === '/api/curation-workspace/views') {
          return jsonResponse(savedViewResponse)
        }

        if (url.startsWith('/api/curation-workspace/sessions')) {
          return jsonResponse(listResponse)
        }

        throw new Error(`Unexpected request: ${url}`)
      })
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders inventory data and navigates into a session workspace route', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('Curation Inventory')).toBeInTheDocument()
    expect(await screen.findByText('Alpha paper')).toBeInTheDocument()
    expect(screen.getByText('Gene Adapter / Alpha Profile')).toBeInTheDocument()
    expect(screen.getByText('Alex Curator')).toBeInTheDocument()
    expect(screen.getByText('8 / 10 resolved')).toBeInTheDocument()

    await user.click(screen.getByText('Alpha paper'))

    expect(await screen.findByText('Workspace route for session-1')).toBeInTheDocument()
    expect(screen.getByTestId('location-state')).toHaveTextContent('"queueRequest"')
    expect(screen.getByTestId('location-state')).toHaveTextContent('"sort_by":"prepared_at"')
  })

  it('re-queries the inventory when filters change', async () => {
    renderPage()

    await screen.findByText('Alpha paper')

    fireEvent.click(screen.getByRole('button', { name: /New 4/i }))
    await waitFor(() => {
      const sessionListCalls = vi
        .mocked(global.fetch)
        .mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.startsWith('/api/curation-workspace/sessions?'))

      expect(sessionListCalls.some((url) => url.includes('status=new'))).toBe(true)
    })

    fireEvent.click(screen.getByRole('button', { name: /Adapter \/ Profile/i }))
    await waitFor(() => {
      const sessionListCalls = vi
        .mocked(global.fetch)
        .mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.startsWith('/api/curation-workspace/sessions?'))

      expect(sessionListCalls.some((url) => url.includes('sort_by=adapter'))).toBe(true)
    })

    fireEvent.change(screen.getByLabelText('Search sessions'), {
      target: { value: 'beta' },
    })
    await waitFor(() => {
      const sessionListCalls = vi
        .mocked(global.fetch)
        .mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.startsWith('/api/curation-workspace/sessions?'))

      expect(sessionListCalls.some((url) => url.includes('search=beta'))).toBe(true)
    })

    expect(screen.getAllByText('All').length).toBeGreaterThanOrEqual(2)
  }, 10000)

  it('applies a saved view through the inventory hook and re-queries the session list', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Alpha paper')

    await user.selectOptions(
      await screen.findByLabelText('Saved view'),
      savedViewResponse.views[0].view_id,
    )

    await waitFor(() => {
      const sessionListCalls = vi
        .mocked(global.fetch)
        .mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.startsWith('/api/curation-workspace/sessions?'))

      expect(
        sessionListCalls.some((url) =>
          url.includes('status=new') &&
          url.includes('adapter_key=gene') &&
          url.includes('profile_key=alpha') &&
          url.includes('search=pending') &&
          url.includes('sort_by=adapter') &&
          url.includes('sort_direction=asc') &&
          url.includes('saved_view_id=saved-view-1') &&
          !url.includes('origin_session_id=')
        ),
      ).toBe(true)
    })
  })
})

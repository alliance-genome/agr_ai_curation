import type { ComponentProps } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { Table, TableBody, TableCell, TableRow } from '@mui/material'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type {
  CurationFlowRunSessionsResponse,
  CurationFlowRunSummary,
  CurationSessionFilters,
  CurationSessionSummary,
} from '../types'
import BatchGroupRow from './BatchGroupRow'

const theme = createTheme()

const defaultFilters: CurationSessionFilters = {
  statuses: [],
  adapter_keys: [],
  curator_ids: [],
  tags: [],
  flow_run_id: null,
  document_id: null,
  search: null,
  prepared_between: null,
  last_worked_between: null,
  saved_view_id: null,
}

const flowRun: CurationFlowRunSummary = {
  flow_run_id: 'flow-alpha',
  display_label: 'flow-alpha',
  session_count: 2,
  reviewed_count: 1,
  pending_count: 2,
  submitted_count: 0,
  last_activity_at: '2026-03-21T19:00:00Z',
}

function buildSession(sessionId: string, title: string): CurationSessionSummary {
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
      document_id: `${sessionId}-doc`,
      title,
      pmid: null,
      doi: null,
      citation_label: null,
      pdf_url: null,
      viewer_url: null,
      publication_year: 2026,
    },
    flow_run_id: 'flow-alpha',
    progress: {
      total_candidates: 2,
      reviewed_candidates: 1,
      pending_candidates: 1,
      accepted_candidates: 0,
      rejected_candidates: 0,
      manual_candidates: 0,
    },
    validation: null,
    evidence: null,
    current_candidate_id: null,
    assigned_curator: null,
    created_by: null,
    prepared_at: '2026-03-21T18:00:00Z',
    last_worked_at: '2026-03-21T19:00:00Z',
    notes: null,
    warnings: [],
    tags: [],
  }
}

function groupedResponse(
  session: CurationSessionSummary,
  page: number
): CurationFlowRunSessionsResponse {
  return {
    flow_run: flowRun,
    sessions: [session],
    page_info: {
      page,
      page_size: 1,
      total_items: 2,
      total_pages: 2,
      has_next_page: page < 2,
      has_previous_page: page > 1,
    },
  }
}

function singlePageGroupedResponse(session: CurationSessionSummary): CurationFlowRunSessionsResponse {
  return {
    flow_run: {
      ...flowRun,
      session_count: 1,
      reviewed_count: 0,
      pending_count: 1,
    },
    sessions: [session],
    page_info: {
      page: 1,
      page_size: 1,
      total_items: 1,
      total_pages: 1,
      has_next_page: false,
      has_previous_page: false,
    },
  }
}

function renderBatchGroupRow(
  overrides: Partial<ComponentProps<typeof BatchGroupRow>> = {}
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  })

  const renderSessionRow: ComponentProps<typeof BatchGroupRow>['renderSessionRow'] =
    overrides.renderSessionRow ??
    ((session) => (
      <TableRow key={session.session_id}>
        <TableCell colSpan={9}>{session.document.title}</TableCell>
      </TableRow>
    ))

  function buildTree(props: Partial<ComponentProps<typeof BatchGroupRow>> = {}) {
    return (
      <QueryClientProvider client={queryClient}>
        <ThemeProvider theme={theme}>
          <Table>
            <TableBody>
              <BatchGroupRow
                colSpan={9}
                filters={defaultFilters}
                flowRun={flowRun}
                pageSize={1}
                renderSessionRow={renderSessionRow}
                {...overrides}
                {...props}
              />
            </TableBody>
          </Table>
        </ThemeProvider>
      </QueryClientProvider>
    )
  }

  const renderResult = render(buildTree())

  return {
    ...renderResult,
    rerenderBatchGroupRow: (props: Partial<ComponentProps<typeof BatchGroupRow>> = {}) => {
      renderResult.rerender(buildTree(props))
    },
  }
}

describe('BatchGroupRow', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows aggregate stats and loads paginated sessions when expanded', async () => {
    const user = userEvent.setup()

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)

        if (url.includes('page=2')) {
          return new Response(JSON.stringify(groupedResponse(buildSession('session-2', 'Batch beta'), 2)))
        }

        return new Response(JSON.stringify(groupedResponse(buildSession('session-1', 'Batch alpha'), 1)))
      })
    )

    renderBatchGroupRow()

    expect(screen.getByText('Flow run flow-alpha')).toBeInTheDocument()
    expect(screen.getByText('2 sessions')).toBeInTheDocument()
    expect(screen.getByText('1 reviewed')).toBeInTheDocument()
    expect(screen.getByText('2 pending')).toBeInTheDocument()

    await user.click(screen.getByText('Flow run flow-alpha'))

    expect(await screen.findByText('Batch alpha')).toBeInTheDocument()
    expect(screen.getByText('Showing 1-1 of 2 sessions')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /go to page 2/i }))

    await waitFor(() => {
      expect(screen.getByText('Batch beta')).toBeInTheDocument()
    })

    const fetchCalls = vi.mocked(global.fetch).mock.calls

    expect(String(fetchCalls[0][0])).toBe(
      '/api/curation-workspace/flow-runs/flow-alpha/sessions?page=1&page_size=1'
    )
    expect(fetchCalls[0][1]?.credentials).toBe('include')
    expect(fetchCalls[0][1]?.headers).toBeInstanceOf(Headers)

    expect(String(fetchCalls[1][0])).toBe(
      '/api/curation-workspace/flow-runs/flow-alpha/sessions?page=2&page_size=1'
    )
    expect(fetchCalls[1][1]?.credentials).toBe('include')
    expect(fetchCalls[1][1]?.headers).toBeInstanceOf(Headers)
  })

  it('resets grouped pagination when the filter scope changes', async () => {
    const user = userEvent.setup()

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)

        if (url.includes('status=submitted')) {
          return new Response(
            JSON.stringify(singlePageGroupedResponse(buildSession('session-3', 'Filtered alpha')))
          )
        }

        if (url.includes('page=2')) {
          return new Response(JSON.stringify(groupedResponse(buildSession('session-2', 'Batch beta'), 2)))
        }

        return new Response(JSON.stringify(groupedResponse(buildSession('session-1', 'Batch alpha'), 1)))
      })
    )

    const { rerenderBatchGroupRow } = renderBatchGroupRow()

    await user.click(screen.getByText('Flow run flow-alpha'))
    expect(await screen.findByText('Batch alpha')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /go to page 2/i }))
    expect(await screen.findByText('Batch beta')).toBeInTheDocument()

    rerenderBatchGroupRow({
      filters: {
        ...defaultFilters,
        statuses: ['submitted'],
      },
    })

    await waitFor(() => {
      expect(screen.getByText('Filtered alpha')).toBeInTheDocument()
    })
    expect(screen.queryByText('No sessions matched this flow run.')).not.toBeInTheDocument()

    const fetchCalls = vi.mocked(global.fetch).mock.calls

    expect(fetchCalls).toHaveLength(3)
    expect(String(fetchCalls[2][0])).toBe(
      '/api/curation-workspace/flow-runs/flow-alpha/sessions?status=submitted&page=1&page_size=1'
    )
  })
})

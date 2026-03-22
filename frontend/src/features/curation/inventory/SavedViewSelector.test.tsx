import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SavedViewSelector from './SavedViewSelector'
import type { CurationSavedView } from '../types'

const theme = createTheme()

function buildSavedView(overrides: Partial<CurationSavedView> = {}): CurationSavedView {
  return {
    view_id: 'saved-view-1',
    name: 'My pending sessions',
    description: 'Only active sessions assigned to me.',
    filters: {
      statuses: ['in_progress'],
      adapter_keys: ['gene'],
      profile_keys: ['alpha'],
      domain_keys: [],
      curator_ids: ['user-1'],
      tags: [],
      flow_run_id: null,
      origin_session_id: null,
      document_id: null,
      search: 'pending',
      prepared_between: null,
      last_worked_between: null,
      saved_view_id: null,
    },
    sort_by: 'prepared_at',
    sort_direction: 'desc',
    is_default: false,
    created_by: {
      actor_id: 'user-1',
      display_name: 'Curator One',
      email: 'user-1@example.org',
    },
    created_at: '2026-03-22T13:15:00Z',
    updated_at: '2026-03-22T13:15:00Z',
    ...overrides,
  }
}

function renderSelector(props: Partial<ComponentProps<typeof SavedViewSelector>> = {}) {
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

  const onApplyView = vi.fn()
  const onClearSelection = vi.fn()

  const result = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <SavedViewSelector
          filters={{
            statuses: [],
            adapter_keys: ['gene'],
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
          }}
          onApplyView={onApplyView}
          onClearSelection={onClearSelection}
          selectedViewId={null}
          sortBy="prepared_at"
          sortDirection="desc"
          {...props}
        />
      </ThemeProvider>
    </QueryClientProvider>
  )

  return {
    ...result,
    onApplyView,
    onClearSelection,
  }
}

describe('SavedViewSelector', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      }
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('applies a saved view when the selection changes', async () => {
    const savedView = buildSavedView()

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            views: [savedView],
          }),
          {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
            },
          }
        )
      )
    )

    const user = userEvent.setup()
    const { onApplyView } = renderSelector()

    await screen.findByRole('option', { name: 'My pending sessions' })
    const selector = await screen.findByLabelText('Saved view')
    await user.selectOptions(selector, savedView.view_id)

    expect(onApplyView).toHaveBeenCalledWith(savedView)
  })

  it('creates a saved view from the current inventory state', async () => {
    let savedViews: CurationSavedView[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = init?.method ?? 'GET'

        if (url === '/api/curation-workspace/views' && method === 'GET') {
          return new Response(JSON.stringify({ views: savedViews }), {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
            },
          })
        }

        if (url === '/api/curation-workspace/views' && method === 'POST') {
          const requestBody = JSON.parse(String(init?.body))
          const createdView = buildSavedView({
            name: requestBody.name,
            description: requestBody.description,
            is_default: requestBody.is_default,
          })
          savedViews = [createdView]

          return new Response(JSON.stringify({ view: createdView }), {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
            },
          })
        }

        throw new Error(`Unexpected request: ${method} ${url}`)
      })
    )

    const user = userEvent.setup()
    const { onApplyView } = renderSelector({
      filters: {
        statuses: ['in_progress'],
        adapter_keys: ['gene'],
        profile_keys: [],
        domain_keys: [],
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        origin_session_id: 'chat-session-12',
        document_id: null,
        search: 'pending',
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: 'existing-view',
      },
    })

    await user.click(await screen.findByRole('button', { name: 'Save current' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: 'View name' }), 'Team queue')
    await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Shared handoff view')
    await user.click(within(dialog).getByLabelText('Mark as my default saved view'))
    await user.click(within(dialog).getByRole('button', { name: 'Save view' }))

    await waitFor(() => {
      expect(onApplyView).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Team queue',
          description: 'Shared handoff view',
          is_default: true,
        })
      )
    })

    const postCall = vi
      .mocked(global.fetch)
      .mock.calls
      .find(([url, init]) => String(url) === '/api/curation-workspace/views' && init?.method === 'POST')
    expect(postCall).toBeDefined()

    const requestBody = JSON.parse(String(postCall?.[1]?.body))
    expect(requestBody).toMatchObject({
      name: 'Team queue',
      description: 'Shared handoff view',
      sort_by: 'prepared_at',
      sort_direction: 'desc',
      is_default: true,
    })
    expect(requestBody.filters.origin_session_id).toBeNull()
    expect(requestBody.filters.saved_view_id).toBeNull()
    expect(requestBody.filters.statuses).toEqual(['in_progress'])
  }, 10_000)

  it('deletes the selected saved view and clears the selection', async () => {
    const savedView = buildSavedView()
    let savedViews: CurationSavedView[] = [savedView]

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = init?.method ?? 'GET'

        if (url === '/api/curation-workspace/views' && method === 'GET') {
          return new Response(JSON.stringify({ views: savedViews }), {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
            },
          })
        }

        if (url === `/api/curation-workspace/views/${savedView.view_id}` && method === 'DELETE') {
          savedViews = []

          return new Response(JSON.stringify({ deleted_view_id: savedView.view_id }), {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
            },
          })
        }

        throw new Error(`Unexpected request: ${method} ${url}`)
      })
    )

    const user = userEvent.setup()
    const { onClearSelection } = renderSelector({
      selectedViewId: savedView.view_id,
    })

    await screen.findByText('Only active sessions assigned to me.')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await screen.findByRole('dialog')
    expect(screen.getByText('Delete saved view?')).toBeInTheDocument()
    expect(screen.getByText(`Delete "${savedView.name}" permanently?`)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete saved view' }))

    await waitFor(() => {
      expect(onClearSelection).toHaveBeenCalled()
    })
  })

  it('does not delete a saved view until the confirmation is accepted', async () => {
    const savedView = buildSavedView()
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ views: [savedView] }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
        },
      })
    )
    vi.stubGlobal('fetch', fetchSpy)

    const user = userEvent.setup()
    const { onClearSelection } = renderSelector({
      selectedViewId: savedView.view_id,
    })

    await screen.findByText('Only active sessions assigned to me.')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await screen.findByRole('dialog')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => {
      expect(screen.queryByText('Delete saved view?')).not.toBeInTheDocument()
    })

    expect(onClearSelection).not.toHaveBeenCalled()
    expect(
      fetchSpy.mock.calls.some(
        ([url, init]) =>
          String(url) === `/api/curation-workspace/views/${savedView.view_id}` &&
          init?.method === 'DELETE'
      )
    ).toBe(false)
  })
})

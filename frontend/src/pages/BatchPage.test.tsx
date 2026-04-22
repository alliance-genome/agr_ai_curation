import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BatchPage from './BatchPage'
import { DEFAULT_FLOW_LIST_PAGE_SIZE } from '@/services/agentStudioService'

vi.mock('@/components/AuditPanel', () => ({
  default: () => <div data-testid="audit-panel" />,
}))

vi.mock('@/components/Chat/FeedbackDialog', () => ({
  default: () => null,
}))

vi.mock('@/features/curation/components/PreparedReviewAndCurateButton', () => ({
  default: () => <button type="button">Review & Curate</button>,
}))

vi.mock('@/services/feedbackService', () => ({
  submitFeedback: vi.fn(),
}))

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { email: 'curator@example.org' },
  }),
}))

const mockFetch = vi.fn()
global.fetch = mockFetch

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: '/batch',
          state: {
            selectedDocumentIds: ['doc-1'],
            selectedDocuments: [{ id: 'doc-1', title: 'Alpha paper' }],
          },
        },
      ]}
    >
      <BatchPage />
    </MemoryRouter>,
  )
}

describe('BatchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url

      if (url === `/api/flows?page=1&page_size=${DEFAULT_FLOW_LIST_PAGE_SIZE}`) {
        return Promise.resolve(
          jsonResponse({
            flows: [
              {
                id: 'flow-1',
                user_id: 7,
                name: 'Evidence Flow',
                description: 'Collects structured evidence',
                step_count: 2,
                execution_count: 3,
                last_executed_at: '2026-04-03T00:00:00Z',
                created_at: '2026-04-02T00:00:00Z',
                updated_at: '2026-04-03T00:00:00Z',
              },
            ],
            total: 1,
            page: 1,
            page_size: DEFAULT_FLOW_LIST_PAGE_SIZE,
          }),
        )
      }

      if (url === '/api/batches') {
        return Promise.resolve(
          jsonResponse({
            batches: [],
          }),
        )
      }

      return Promise.reject(new Error(`Unexpected fetch: ${url}`))
    })
  })

  it('requests flows through the shared page-size contract', async () => {
    renderPage()

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(`/api/flows?page=1&page_size=${DEFAULT_FLOW_LIST_PAGE_SIZE}`)
    })
  })

  it('surfaces shared flow load errors in the setup panel', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url

      if (url === `/api/flows?page=1&page_size=${DEFAULT_FLOW_LIST_PAGE_SIZE}`) {
        return Promise.resolve(new Response(null, { status: 401 }))
      }

      if (url === '/api/batches') {
        return Promise.resolve(
          jsonResponse({
            batches: [],
          }),
        )
      }

      return Promise.reject(new Error(`Unexpected fetch: ${url}`))
    })

    renderPage()

    expect(await screen.findByText('Please log in to view your flows')).toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BatchPage, { mapBatchDocument } from './BatchPage'
import { DEFAULT_FLOW_LIST_PAGE_SIZE } from '@/services/agentStudioService'
import { submitFeedback } from '@/services/feedbackService'

vi.mock('@/components/AuditPanel', () => ({
  default: () => <div data-testid="audit-panel" />,
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
      expect(mockFetch).toHaveBeenCalledWith(`/api/flows?page=1&page_size=${DEFAULT_FLOW_LIST_PAGE_SIZE}`, {
        credentials: 'include',
      })
    })
  })

  it('preserves authoritative handoff identities in the batch detail mapping', () => {
    const extractionResultRefs = [
      {
        result_ref: 'extraction-result:extract-gene',
        extraction_result_id: 'extract-gene',
        adapter_key: 'gene',
      },
    ]

    expect(mapBatchDocument({
      id: 'batch-doc-1',
      document_id: 'doc-1',
      document_title: 'Alpha paper',
      position: 0,
      status: 'completed',
      review_session_ids: ['review-gene'],
      adapter_keys: ['gene'],
      extraction_result_ids: ['extract-gene'],
      extraction_result_refs: extractionResultRefs,
      flow_run_id: 'flow-run-1',
      origin_session_id: 'origin-1',
    })).toEqual(expect.objectContaining({
      review_session_ids: ['review-gene'],
      adapter_keys: ['gene'],
      extraction_result_ids: ['extract-gene'],
      extraction_result_refs: extractionResultRefs,
      flow_run_id: 'flow-run-1',
      origin_session_id: 'origin-1',
    }))
  })

  it('distinguishes authoritative zero sessions from legacy missing IDs', () => {
    expect(mapBatchDocument({
      id: 'batch-doc-zero',
      document_id: 'doc-zero',
      position: 0,
      status: 'completed',
      review_session_ids: null,
    }).review_session_ids).toEqual([])

    expect(mapBatchDocument({
      id: 'batch-doc-legacy',
      document_id: 'doc-legacy',
      position: 1,
      status: 'completed',
    }).review_session_ids).toBeUndefined()
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

  it('opens completed document feedback modeless with the active batch session and trace id', async () => {
    vi.mocked(submitFeedback).mockResolvedValue({
      status: 'success',
      feedback_id: 'feedback-1',
      message: 'Feedback submitted.',
    })
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
            batches: [
              {
                id: 'batch-1',
                flow_id: 'flow-1',
                flow_name: 'Evidence Flow',
                status: 'completed',
                total_documents: 1,
                completed_documents: 1,
                failed_documents: 0,
                created_at: '2026-04-03T00:00:00Z',
              },
            ],
          }),
        )
      }

      if (url === '/api/batches/batch-1') {
        return Promise.resolve(
          jsonResponse({
            id: 'batch-1',
            flow_id: 'flow-1',
            status: 'completed',
            total_documents: 1,
            completed_documents: 1,
            failed_documents: 0,
            documents: [
              {
                id: 'batch-doc-1',
                document_id: 'doc-1',
                document_title: 'Alpha paper',
                position: 1,
                status: 'completed',
                processing_time_ms: 42,
                trace_id: 'trace-batch-doc-1',
              },
            ],
          }),
        )
      }

      return Promise.reject(new Error(`Unexpected fetch: ${url}`))
    })

    renderPage()

    const evidenceFlowLabels = await screen.findAllByText('Evidence Flow')
    fireEvent.click(evidenceFlowLabels[evidenceFlowLabels.length - 1])
    await screen.findByText('Batch Complete')

    fireEvent.click(screen.getByRole('button', { name: 'document actions' }))
    fireEvent.click(await screen.findByText('Provide Feedback'))

    const dialog = screen.getByRole('dialog', { name: 'Provide Feedback' })
    expect(dialog).toHaveAttribute('aria-modal', 'false')
    await waitFor(() => {
      expect(document.querySelector('.MuiBackdrop-root')).not.toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText(/enter your detailed feedback here/i), {
      target: { value: 'Batch document trace feedback' },
    })
    fireEvent.click(screen.getByText('Batch Complete'))

    expect(screen.getByPlaceholderText(/enter your detailed feedback here/i)).toHaveValue(
      'Batch document trace feedback'
    )

    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(submitFeedback).toHaveBeenCalledWith({
        session_id: 'batch-1',
        curator_id: 'curator@example.org',
        feedback_text: 'Batch document trace feedback',
        trace_ids: ['trace-batch-doc-1'],
      })
    })
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BatchPage, { mapBatchDocument } from './BatchPage'
import { DEFAULT_FLOW_LIST_PAGE_SIZE } from '@/services/agentStudioService'
import { submitFeedback } from '@/services/feedbackService'

const openCurationWorkspaceMock = vi.fn()

vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace',
  )
  return {
    ...actual,
    openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
  }
})

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

class MockEventSource {
  static instances: MockEventSource[] = []

  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()

  constructor(public readonly url: string) {
    MockEventSource.instances.push(this)
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)
  }
}

vi.stubGlobal('EventSource', MockEventSource)

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
    MockEventSource.instances = []
    openCurationWorkspaceMock.mockResolvedValue('review-gene')

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

  it('maps absent or null review-session IDs to authoritative zero', () => {
    expect(mapBatchDocument({
      id: 'batch-doc-zero',
      document_id: 'doc-zero',
      position: 0,
      status: 'completed',
      review_session_ids: null,
    }).review_session_ids).toEqual([])

    expect(mapBatchDocument({
      id: 'batch-doc-missing-ids',
      document_id: 'doc-missing-ids',
      position: 1,
      status: 'completed',
    }).review_session_ids).toEqual([])
  })

  it('hydrates a completed row from the live snapshot and opens its exact review session', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url

      if (url === `/api/flows?page=1&page_size=${DEFAULT_FLOW_LIST_PAGE_SIZE}`) {
        return Promise.resolve(jsonResponse({
          flows: [],
          total: 0,
          page: 1,
          page_size: DEFAULT_FLOW_LIST_PAGE_SIZE,
        }))
      }

      if (url === '/api/batches') {
        return Promise.resolve(jsonResponse({
          batches: [{
            id: 'batch-live',
            flow_id: 'flow-1',
            flow_name: 'Evidence Flow',
            status: 'running',
            total_documents: 1,
            completed_documents: 0,
            failed_documents: 0,
            created_at: '2026-04-03T00:00:00Z',
          }],
        }))
      }

      if (url === '/api/batches/batch-live') {
        return Promise.resolve(jsonResponse({
          id: 'batch-live',
          flow_id: 'flow-1',
          status: 'running',
          total_documents: 1,
          completed_documents: 0,
          failed_documents: 0,
          documents: [{
            id: 'batch-doc-1',
            document_id: 'doc-1',
            document_title: 'Alpha paper',
            position: 0,
            status: 'pending',
          }],
        }))
      }

      return Promise.reject(new Error(`Unexpected fetch: ${url}`))
    })

    renderPage()

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
    MockEventSource.instances[0].emit({
      type: 'DOCUMENT_STATUS',
      batch_id: 'batch-live',
      document_id: 'doc-1',
      batch_document_id: 'batch-doc-1',
      position: 0,
      status: 'completed',
      review_session_ids: ['review-gene'],
      adapter_keys: ['gene'],
      extraction_result_ids: ['extract-gene'],
      extraction_result_refs: [{
        result_ref: 'extraction-result:extract-gene',
        extraction_result_id: 'extract-gene',
        adapter_key: 'gene',
      }],
      flow_run_id: 'flow-run-1',
      origin_session_id: 'origin-1',
      processing_time_ms: 1200,
    })

    fireEvent.click(await screen.findByRole('button', { name: 'Review & Curate' }))
    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(expect.objectContaining({
        sessionId: 'review-gene',
        documentId: 'doc-1',
        flowRunId: 'flow-run-1',
        originSessionId: 'origin-1',
      }))
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
                result_file_path: '/api/files/file-csv/download',
                result_files: [
                  {
                    file_id: 'file-csv',
                    filename: 'alleles.csv',
                    format: 'csv',
                    download_url: '/api/files/file-csv/download',
                  },
                  {
                    file_id: 'file-json',
                    filename: 'genes.json',
                    format: 'json',
                    download_url: '/api/files/file-json/download',
                  },
                ],
                output_status: 'partial',
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
    expect(screen.getByText('Partial output · alleles.csv · genes.json')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download alleles.csv' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download genes.json' })).toBeInTheDocument()

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

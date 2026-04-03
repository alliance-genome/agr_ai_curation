import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

import CurationFlows from '../../components/RightPanel/Tools/CurationFlows'
import type { SSEEvent } from '../../hooks/useChatStream'
import { notifyFlowListInvalidated } from '@/features/flows/flowListInvalidation'

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

const mockFetch = vi.fn()
global.fetch = mockFetch

const mockCreateObjectURL = vi.fn(() => 'blob:flow-evidence')
const mockRevokeObjectURL = vi.fn()
global.URL.createObjectURL = mockCreateObjectURL
global.URL.revokeObjectURL = mockRevokeObjectURL

function flowListResponse(
  flows: Array<{
    id: string
    user_id: number
    name: string
    description: string | null
    step_count: number
    execution_count: number
    last_executed_at: string | null
    created_at: string
    updated_at: string
  }> = [
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
) {
  return new Response(
    JSON.stringify({
      flows,
      total: flows.length,
      page: 1,
      page_size: 50,
    }),
    {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
      },
    },
  )
}

function completedRunEvent(overrides: Partial<SSEEvent> = {}): SSEEvent {
  return {
    type: 'FLOW_FINISHED',
    session_id: 'session-123',
    flow_id: 'flow-1',
    flow_name: 'Evidence Flow',
    flow_run_id: 'flow-run-123',
    document_id: 'document-123',
    origin_session_id: 'session-123',
    adapter_keys: ['gene'],
    status: 'completed',
    total_evidence_records: 4,
    ...overrides,
  }
}

const renderComponent = (sseEvents: SSEEvent[]) => render(
  <MemoryRouter>
    <CurationFlows
      sessionId="session-123"
      sseEvents={sseEvents}
      onExecuteFlow={vi.fn(async () => {})}
      isExecuting={false}
      currentDocumentId="document-123"
    />
  </MemoryRouter>,
)

describe('CurationFlows', () => {
  const originalCreateElement = document.createElement.bind(document)
  let mockLink: HTMLAnchorElement

  beforeEach(() => {
    vi.clearAllMocks()
    openCurationWorkspaceMock.mockResolvedValue('curation-session-1')
    mockFetch.mockResolvedValue(flowListResponse())

    mockLink = originalCreateElement('a')
    vi.spyOn(mockLink, 'click').mockImplementation(() => {})
    vi.spyOn(document, 'createElement').mockImplementation(((tagName: string) => {
      if (tagName.toLowerCase() === 'a') {
        return mockLink
      }
      return originalCreateElement(tagName)
    }) as typeof document.createElement)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the latest completed flow run surface from shared SSE events', async () => {
    renderComponent([completedRunEvent()])

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/flows?page=1&page_size=50')
    })

    expect(screen.getByText('Latest flow run')).toBeInTheDocument()
    expect(screen.getByText('Evidence Flow')).toBeInTheDocument()
    expect(screen.getByText(/4 evidence records ready/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Review & Curate/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Export Evidence/i })).toBeEnabled()
  })

  it('reuses the curation workspace launcher for the completion card', async () => {
    const user = userEvent.setup()

    renderComponent([completedRunEvent()])

    const reviewButton = await screen.findByRole('button', { name: /Review & Curate/i })
    await user.click(reviewButton)

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          documentId: 'document-123',
          flowRunId: 'flow-run-123',
          originSessionId: 'session-123',
          adapterKeys: ['gene'],
          navigate: expect.any(Function),
        }),
      )
    })
  })

  it('does not infer missing curation scope from ambient component state', async () => {
    renderComponent([
      completedRunEvent({
        document_id: undefined,
        origin_session_id: undefined,
      }),
    ])

    const reviewButton = await screen.findByRole('button', { name: /Review & Curate/i })
    expect(reviewButton).toBeDisabled()
    expect(
      screen.getByText(/does not have enough document scope metadata/i),
    ).toBeInTheDocument()
  })

  it('downloads evidence export from the flow evidence endpoint', async () => {
    const user = userEvent.setup()

    mockFetch
      .mockResolvedValueOnce(flowListResponse())
      .mockResolvedValueOnce(
        new Response('evidence_record_id,entity\nrecord-1,gene-1\n', {
          status: 200,
          headers: {
            'Content-Type': 'text/csv',
            'Content-Disposition': 'attachment; filename="flow-flow-run-123-evidence.csv"',
          },
        }),
      )

    renderComponent([completedRunEvent()])

    const exportButton = await screen.findByRole('button', { name: /Export Evidence/i })
    await user.click(exportButton)
    await user.click(await screen.findByRole('menuitem', { name: /Download CSV/i }))

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/flows/runs/flow-run-123/evidence/export?format=csv',
        { credentials: 'include' },
      )
    })

    expect(mockLink.download).toBe('flow-flow-run-123-evidence.csv')
    expect(mockLink.click).toHaveBeenCalled()
    expect(mockCreateObjectURL).toHaveBeenCalledTimes(1)
    expect(mockRevokeObjectURL).toHaveBeenCalledWith('blob:flow-evidence')
  })

  it('ignores malformed flow finished events that cannot drive completion state', async () => {
    renderComponent([
      completedRunEvent({ status: '' }),
      completedRunEvent({ flow_name: '' }),
      completedRunEvent({ total_evidence_records: 'not-a-number' as unknown as number }),
    ])

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/flows?page=1&page_size=50')
    })

    expect(screen.queryByText('Latest flow run')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Export Evidence/i })).not.toBeInTheDocument()
  })

  it('surfaces an error when the export response omits attachment filename', async () => {
    const user = userEvent.setup()

    mockFetch
      .mockResolvedValueOnce(flowListResponse())
      .mockResolvedValueOnce(
        new Response('evidence_record_id,entity\nrecord-1,gene-1\n', {
          status: 200,
          headers: {
            'Content-Type': 'text/csv',
          },
        }),
      )

    renderComponent([completedRunEvent()])

    const exportButton = await screen.findByRole('button', { name: /Export Evidence/i })
    await user.click(exportButton)
    await user.click(await screen.findByRole('menuitem', { name: /Download CSV/i }))

    await waitFor(() => {
      expect(screen.getByText('Download response is missing Content-Disposition header.')).toBeInTheDocument()
    })

    expect(mockLink.click).not.toHaveBeenCalled()
    expect(mockCreateObjectURL).not.toHaveBeenCalled()
    expect(mockRevokeObjectURL).not.toHaveBeenCalled()
  })

  it('refreshes the flow list when flow invalidation is broadcast', async () => {
    mockFetch
      .mockResolvedValueOnce(flowListResponse())
      .mockResolvedValueOnce(
        flowListResponse([
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
          {
            id: 'flow-2',
            user_id: 7,
            name: 'Fresh Flow',
            description: 'Saved from Agent Studio',
            step_count: 3,
            execution_count: 0,
            last_executed_at: null,
            created_at: '2026-04-03T01:00:00Z',
            updated_at: '2026-04-03T01:00:00Z',
          },
        ]),
      )

    renderComponent([])

    await screen.findByText(/Evidence Flow/)

    act(() => {
      notifyFlowListInvalidated({ flowId: 'flow-2', reason: 'created' })
    })

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    expect(await screen.findByText(/Fresh Flow/)).toBeInTheDocument()
  })
})

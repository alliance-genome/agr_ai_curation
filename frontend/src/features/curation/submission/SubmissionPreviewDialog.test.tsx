import type { ComponentProps } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import type {
  CurationCandidate,
  CurationReviewSession,
  CurationSubmissionPreviewResponse,
  SubmissionMode,
} from '@/features/curation/types'
import SubmissionPreviewDialog from './SubmissionPreviewDialog'

const serviceMocks = vi.hoisted(() => ({
  fetchSubmissionPreview: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  fetchSubmissionPreview: serviceMocks.fetchSubmissionPreview,
}))

function buildSession(): CurationReviewSession {
  return {
    session_id: 'session-1',
    status: 'in_progress',
    adapter: {
      adapter_key: 'reference_adapter',
      display_label: 'Reference adapter',
      color_token: 'green',
      metadata: {},
    },
    document: {
      document_id: 'document-1',
      title: 'Submission-ready paper',
      pdf_url: '/api/documents/document-1.pdf',
      viewer_url: '/api/documents/document-1.pdf',
    },
    progress: {
      total_candidates: 2,
      reviewed_candidates: 1,
      pending_candidates: 1,
      accepted_candidates: 1,
      rejected_candidates: 0,
      manual_candidates: 0,
    },
    current_candidate_id: 'candidate-ready',
    prepared_at: '2026-03-20T12:00:00Z',
    warnings: [],
    tags: [],
    session_version: 2,
    extraction_results: [],
  }
}

function buildCandidates(): CurationCandidate[] {
  return [
    {
      candidate_id: 'candidate-ready',
      session_id: 'session-1',
      source: 'extracted',
      status: 'accepted',
      order: 0,
      adapter_key: 'reference_adapter',
      display_label: 'Accepted candidate',
      draft: {
        draft_id: 'draft-1',
        candidate_id: 'candidate-ready',
        adapter_key: 'reference_adapter',
        version: 1,
        title: 'Accepted draft',
        fields: [],
        created_at: '2026-03-20T12:00:00Z',
        updated_at: '2026-03-20T12:00:00Z',
        metadata: {},
      },
      evidence_anchors: [],
      created_at: '2026-03-20T12:00:00Z',
      updated_at: '2026-03-20T12:00:00Z',
      metadata: {},
    },
    {
      candidate_id: 'candidate-pending',
      session_id: 'session-1',
      source: 'manual',
      status: 'pending',
      order: 1,
      adapter_key: 'reference_adapter',
      display_label: 'Pending candidate',
      draft: {
        draft_id: 'draft-2',
        candidate_id: 'candidate-pending',
        adapter_key: 'reference_adapter',
        version: 1,
        title: 'Pending draft',
        fields: [],
        created_at: '2026-03-20T12:00:00Z',
        updated_at: '2026-03-20T12:00:00Z',
        metadata: {},
      },
      evidence_anchors: [],
      created_at: '2026-03-20T12:00:00Z',
      updated_at: '2026-03-20T12:00:00Z',
      metadata: {},
    },
  ]
}

function buildResponse({
  mode = 'preview',
  readyCandidateIds = ['candidate-ready'],
  invalidCount = 0,
  payloadText = null,
  filename = null,
}: {
  mode?: SubmissionMode
  readyCandidateIds?: string[]
  invalidCount?: number
  payloadText?: string | null
  filename?: string | null
} = {}): CurationSubmissionPreviewResponse {
  return {
    submission: {
      submission_id: 'submission-1',
      session_id: 'session-1',
      adapter_key: 'reference_adapter',
      mode,
      target_key: 'review_export_bundle',
      status: mode === 'export' ? 'export_ready' : 'preview_ready',
      readiness: [
        {
          candidate_id: 'candidate-ready',
          ready: readyCandidateIds.includes('candidate-ready'),
          blocking_reasons: readyCandidateIds.includes('candidate-ready')
            ? []
            : ['Gene symbol is empty or invalid.'],
          warnings: [],
        },
        {
          candidate_id: 'candidate-pending',
          ready: readyCandidateIds.includes('candidate-pending'),
          blocking_reasons: ['Candidate is still pending curator review.'],
          warnings: [],
        },
      ],
      payload: {
        mode,
        target_key: 'review_export_bundle',
        adapter_key: 'reference_adapter',
        candidate_ids: readyCandidateIds,
        payload_json: {
          candidate_count: readyCandidateIds.length,
          candidates: readyCandidateIds,
        },
        payload_text: payloadText,
        filename,
        content_type: payloadText ? 'application/json' : null,
        warnings: readyCandidateIds.length === 0
          ? ['No accepted candidates are ready for submission.']
          : [],
      },
      requested_at: '2026-03-21T12:00:00Z',
      completed_at: '2026-03-21T12:00:01Z',
      validation_errors: [],
      warnings: [],
    },
    session_validation: {
      snapshot_id: 'snapshot-1',
      scope: 'session',
      session_id: 'session-1',
      adapter_key: 'reference_adapter',
      state: 'completed',
      field_results: {},
      summary: {
        state: 'completed',
        counts: {
          validated: 0,
          ambiguous: 0,
          not_found: 0,
          invalid_format: invalidCount,
          conflict: 0,
          skipped: 1,
          overridden: 0,
        },
        warnings: [],
        stale_field_keys: [],
        last_validated_at: '2026-03-21T12:00:00Z',
      },
      requested_at: '2026-03-21T12:00:00Z',
      completed_at: '2026-03-21T12:00:01Z',
      warnings: [],
    },
  }
}

function renderDialog(
  props: Partial<ComponentProps<typeof SubmissionPreviewDialog>> = {},
) {
  return render(
    <ThemeProvider theme={theme}>
      <SubmissionPreviewDialog
        candidates={buildCandidates()}
        onClose={vi.fn()}
        open
        session={buildSession()}
        {...props}
      />
    </ThemeProvider>,
  )
}

beforeEach(() => {
  serviceMocks.fetchSubmissionPreview.mockReset()
  vi.restoreAllMocks()
  global.URL.createObjectURL = vi.fn(() => 'blob:submission-preview')
  global.URL.revokeObjectURL = vi.fn()
})

describe('SubmissionPreviewDialog', () => {
  it('loads preview mode with readiness and payload details', async () => {
    serviceMocks.fetchSubmissionPreview.mockResolvedValue(buildResponse())

    renderDialog()

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenCalledWith({
        session_id: 'session-1',
        mode: 'preview',
        include_payload: true,
      })
    })

    expect(screen.getByText('Accepted candidate')).toBeInTheDocument()
    expect(screen.getByText('Pending candidate')).toBeInTheDocument()
    expect(screen.getByText('Inspect the assembled submission payload without side effects.')).toBeInTheDocument()
    expect(screen.getByText(/"candidate_count": 1/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh preview' })).toBeEnabled()
  })

  it('switches to export mode and disables download when no exporter is configured', async () => {
    const user = userEvent.setup()

    serviceMocks.fetchSubmissionPreview
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(buildResponse({ mode: 'export' }))

    renderDialog()

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenCalledTimes(1)
    })

    await user.click(screen.getByRole('button', { name: 'Export mode' }))

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenLastCalledWith({
        session_id: 'session-1',
        mode: 'export',
        include_payload: true,
      })
    })

    expect(screen.getByText(
      'Inspect the assembled export payload. Download stays disabled until an adapter-owned exporter is configured.',
    )).toBeInTheDocument()
    expect(screen.getByText(
      'No exporter is configured for this adapter yet. You can still inspect the assembled payload below.',
    )).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download bundle' })).toBeDisabled()
    expect(global.URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('downloads an adapter-owned export bundle when the response includes one', async () => {
    const user = userEvent.setup()
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    serviceMocks.fetchSubmissionPreview
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(
        buildResponse({
          mode: 'export',
          payloadText: '{\"candidate_count\":1}',
          filename: 'submission-export.json',
        }),
      )

    renderDialog()

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenCalledTimes(1)
    })

    await user.click(screen.getByRole('button', { name: 'Export mode' }))

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenLastCalledWith({
        session_id: 'session-1',
        mode: 'export',
        include_payload: true,
      })
    })

    await user.click(screen.getByRole('button', { name: 'Download bundle' }))

    expect(global.URL.createObjectURL).toHaveBeenCalledTimes(1)
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith('blob:submission-preview')
  })

  it('gates submit mode when no candidates are ready and no transport is available', async () => {
    const user = userEvent.setup()

    serviceMocks.fetchSubmissionPreview
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(
        buildResponse({
          mode: 'direct_submit',
          readyCandidateIds: [],
          invalidCount: 1,
        }),
      )

    renderDialog()

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenCalledTimes(1)
    })

    await user.click(screen.getByRole('button', { name: 'Submit mode' }))

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenLastCalledWith({
        session_id: 'session-1',
        mode: 'direct_submit',
        include_payload: true,
      })
    })

    expect(screen.getByText('No submission transport is configured for this adapter yet.')).toBeInTheDocument()
    expect(screen.getByText(/Session validation summary: 1 invalid/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()
  })

  it('allows direct submit when at least one candidate is ready even if other session validation issues remain', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const submitResponse = buildResponse({
      mode: 'direct_submit',
      readyCandidateIds: ['candidate-ready'],
      invalidCount: 1,
    })

    serviceMocks.fetchSubmissionPreview
      .mockResolvedValueOnce(buildResponse())
      .mockResolvedValueOnce(submitResponse)

    renderDialog({
      onSubmit,
      submitAvailable: true,
    })

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenCalledTimes(1)
    })

    await user.click(screen.getByRole('button', { name: 'Submit mode' }))

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenLastCalledWith({
        session_id: 'session-1',
        mode: 'direct_submit',
        include_payload: true,
      })
    })

    expect(screen.getByText(/Session validation summary: 1 invalid/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Submit' }))

    expect(onSubmit).toHaveBeenCalledWith(submitResponse)
  })

  it('renders service errors when preview loading fails', async () => {
    serviceMocks.fetchSubmissionPreview.mockRejectedValue(new Error('Preview failed'))

    renderDialog()

    expect(await screen.findByText('Preview failed')).toBeInTheDocument()
  })
})

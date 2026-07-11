import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CurationWorkspace, DomainEnvelopeReviewRowsResponse } from '@/features/curation/types'
import {
  buildCurationWorkspaceEnvelopeReviewRowsRequests,
  buildDomainEnvelopeReviewRowsPath,
  CurationWorkspaceRequestError,
  executeCurationSubmission,
  fetchCurationWorkspaceEnvelopeReviewRows,
  fetchDomainEnvelopeReviewRows,
  fetchSubmissionPreview,
  patchCurationEnvelopeField,
} from './curationWorkspaceService'

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'domain-pack',
        display_label: 'Domain pack',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Envelope workspace',
      },
      progress: {
        total_candidates: 3,
        reviewed_candidates: 0,
        pending_candidates: 3,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      prepared_at: '2026-05-10T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    candidates: [
      {
        candidate_id: 'candidate-gene',
        session_id: 'session-1',
        source: 'extracted',
        status: 'pending',
        order: 0,
        adapter_key: 'domain-pack',
        projection_ref: {
          envelope_id: 'env gene',
          object_id: 'gene-1',
          envelope_revision: 2,
        },
        draft: {
          draft_id: 'draft-gene',
          candidate_id: 'candidate-gene',
          adapter_key: 'domain-pack',
          version: 1,
          fields: [],
          created_at: '2026-05-10T12:00:00Z',
          updated_at: '2026-05-10T12:00:00Z',
          metadata: {},
        },
        evidence_anchors: [],
        created_at: '2026-05-10T12:00:00Z',
        updated_at: '2026-05-10T12:00:00Z',
        metadata: {},
      },
      {
        candidate_id: 'candidate-allele',
        session_id: 'session-1',
        source: 'extracted',
        status: 'pending',
        order: 1,
        adapter_key: 'domain-pack',
        projection_ref: {
          envelope_id: 'env gene',
          object_id: 'allele-1',
          envelope_revision: 2,
        },
        draft: {
          draft_id: 'draft-allele',
          candidate_id: 'candidate-allele',
          adapter_key: 'domain-pack',
          version: 1,
          fields: [],
          created_at: '2026-05-10T12:00:00Z',
          updated_at: '2026-05-10T12:00:00Z',
          metadata: {},
        },
        evidence_anchors: [],
        created_at: '2026-05-10T12:00:00Z',
        updated_at: '2026-05-10T12:00:00Z',
        metadata: {},
      },
      {
        candidate_id: 'candidate-chemical',
        session_id: 'session-1',
        source: 'extracted',
        status: 'pending',
        order: 2,
        adapter_key: 'domain-pack',
        projection_ref: {
          envelope_id: 'env-chemical',
          object_id: 'chemical-1',
          envelope_revision: 1,
        },
        draft: {
          draft_id: 'draft-chemical',
          candidate_id: 'candidate-chemical',
          adapter_key: 'domain-pack',
          version: 1,
          fields: [],
          created_at: '2026-05-10T12:00:00Z',
          updated_at: '2026-05-10T12:00:00Z',
          metadata: {},
        },
        evidence_anchors: [],
        created_at: '2026-05-10T12:00:00Z',
        updated_at: '2026-05-10T12:00:00Z',
        metadata: {},
      },
    ],
    evidence_anchor_projections: [],
    validation_summary_projections: [],
    active_candidate_id: null,
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function reviewRowsResponse(
  envelopeId: string,
  envelopeRevision: number,
): DomainEnvelopeReviewRowsResponse {
  return {
    envelope_id: envelopeId,
    envelope_revision: envelopeRevision,
    row_count: 0,
    rows: [],
  }
}

function jsonResponse(payload: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
    ...init,
  })
}

describe('curationWorkspaceService envelope review rows', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('builds the persisted domain-envelope review-row endpoint path', () => {
    expect(buildDomainEnvelopeReviewRowsPath({
      envelope_id: 'env gene',
      envelope_revision: 2,
    })).toBe('/api/curation-workspace/domain-envelopes/env%20gene/review-rows?revision=2')

    expect(buildDomainEnvelopeReviewRowsPath({
      envelope_id: 'env-gene',
      envelope_revision: null,
    })).toBe('/api/curation-workspace/domain-envelopes/env-gene/review-rows')
  })

  it('deduplicates envelope review-row requests by envelope and revision', () => {
    expect(buildCurationWorkspaceEnvelopeReviewRowsRequests(buildWorkspace())).toEqual([
      {
        envelope_id: 'env gene',
        envelope_revision: 2,
      },
      {
        envelope_id: 'env-chemical',
        envelope_revision: 1,
      },
    ])
  })

  it('fetches domain-envelope review rows with credentials', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(reviewRowsResponse('env gene', 2)), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        }),
      ),
    )

    await fetchDomainEnvelopeReviewRows({
      envelope_id: 'env gene',
      envelope_revision: 2,
    })

    const [url, init] = vi.mocked(global.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/curation-workspace/domain-envelopes/env%20gene/review-rows?revision=2')
    expect(init?.credentials).toBe('include')
  })

  it('fetches each envelope revision needed by a workspace projection set', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)

        if (url.includes('env%20gene')) {
          return new Response(JSON.stringify(reviewRowsResponse('env gene', 2)), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }

        if (url.includes('env-chemical')) {
          return new Response(JSON.stringify(reviewRowsResponse('env-chemical', 1)), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }

        throw new Error(`Unexpected request: ${url}`)
      }),
    )

    const responses = await fetchCurationWorkspaceEnvelopeReviewRows(buildWorkspace())

    expect(responses.map((response) => response.envelope_id)).toEqual([
      'env gene',
      'env-chemical',
    ])
    expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(2)
  })

  it('preserves the HTTP status on workspace request failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(
        { detail: 'Draft version conflict' },
        { status: 409 },
      )),
    )

    const error = await fetchDomainEnvelopeReviewRows({
      envelope_id: 'env-gene',
      envelope_revision: 2,
    }).catch((requestError: unknown) => requestError)

    expect(error).toBeInstanceOf(CurationWorkspaceRequestError)
    expect(error).toMatchObject({
      status: 409,
      message: 'Draft version conflict',
    })
  })

  it('sends explicit envelope field patch operations unchanged', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({
          accepted: true,
          envelope_id: 'env gene',
          previous_revision: 2,
          envelope_revision: 3,
          object_id: 'gene-1',
          object_type: 'gene',
          field_path: 'gene.symbol',
          operation: 'replace',
          before: 'abc',
          value: 'def',
          projection_ref: {
            envelope_id: 'env gene',
            object_id: 'gene-1',
            envelope_revision: 3,
          },
          candidate: null,
          session: null,
          action_log_entry: null,
          history_event_ids: ['history-1'],
          projection_candidate_ids: ['candidate-gene'],
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await patchCurationEnvelopeField({
      session_id: 'session-1',
      envelope_id: 'env gene',
      expected_revision: 2,
      object_id: 'gene-1',
      field_path: 'gene.symbol',
      operation: 'replace',
      before: 'abc',
      value: 'def',
      patch_id: 'patch-1',
    })

    const [url, init] = vi.mocked(global.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/curation-workspace/sessions/session-1/envelopes/env%20gene/field')
    expect(JSON.parse(String(init?.body))).toEqual({
      session_id: 'session-1',
      envelope_id: 'env gene',
      expected_revision: 2,
      object_id: 'gene-1',
      field_path: 'gene.symbol',
      operation: 'replace',
      before: 'abc',
      value: 'def',
      patch_id: 'patch-1',
    })
  })
})

describe('curationWorkspaceService submission requests', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts envelope revision checks when fetching submission readiness', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        submission: {
          submission_id: 'submission-1',
          session_id: 'session-1',
          adapter_key: 'gene',
          mode: 'export',
          target_key: 'review_export_bundle',
          status: 'export_ready',
          readiness: [],
          payload: null,
          requested_at: '2026-05-10T12:00:00Z',
          validation_errors: [],
          warnings: [],
          submission_state: {},
          target_result_history: [],
        },
      })
    )
    vi.stubGlobal('fetch', fetchMock)

    await fetchSubmissionPreview({
      session_id: 'session-1',
      mode: 'export',
      target_key: 'review_export_bundle',
      include_payload: true,
      expected_envelope_revisions: {
        'envelope-1': 7,
      },
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/curation-workspace/sessions/session-1/submission-preview',
      expect.objectContaining({
        credentials: 'include',
        method: 'POST',
        body: JSON.stringify({
          session_id: 'session-1',
          mode: 'export',
          target_key: 'review_export_bundle',
          include_payload: true,
          expected_envelope_revisions: {
            'envelope-1': 7,
          },
        }),
      }),
    )
  })

  it('posts direct submission requests to the session submit endpoint', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        submission: {
          submission_id: 'submission-1',
          session_id: 'session-1',
          adapter_key: 'gene',
          mode: 'direct_submit',
          target_key: 'review_export_bundle',
          status: 'accepted',
          readiness: [],
          payload: null,
          requested_at: '2026-05-10T12:00:00Z',
          validation_errors: [],
          warnings: [],
          submission_state: {},
          target_result_history: [],
        },
        session: {
          session_id: 'session-1',
          status: 'submitted',
          adapter: {
            adapter_key: 'gene',
            metadata: {},
          },
          document: {
            document_id: 'document-1',
            title: 'Document',
          },
          progress: {
            total_candidates: 1,
            reviewed_candidates: 1,
            pending_candidates: 0,
            accepted_candidates: 1,
            rejected_candidates: 0,
            manual_candidates: 0,
          },
          prepared_at: '2026-05-10T11:00:00Z',
          warnings: [],
          tags: [],
          session_version: 2,
          extraction_results: [],
        },
        action_log_entry: {
          action_id: 'action-1',
          session_id: 'session-1',
          action_type: 'submission_executed',
          actor_type: 'user',
          occurred_at: '2026-05-10T12:00:01Z',
          changed_field_keys: [],
          evidence_anchor_ids: [],
          metadata: {},
        },
      })
    )
    vi.stubGlobal('fetch', fetchMock)

    await executeCurationSubmission({
      session_id: 'session-1',
      target_key: 'review_export_bundle',
      candidate_ids: ['candidate-1'],
      mode: 'direct_submit',
      expected_envelope_revisions: {
        'envelope-1': 7,
      },
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/curation-workspace/sessions/session-1/submit',
      expect.objectContaining({
        credentials: 'include',
        method: 'POST',
        body: JSON.stringify({
          session_id: 'session-1',
          target_key: 'review_export_bundle',
          candidate_ids: ['candidate-1'],
          mode: 'direct_submit',
          expected_envelope_revisions: {
            'envelope-1': 7,
          },
        }),
      }),
    )
  })
})

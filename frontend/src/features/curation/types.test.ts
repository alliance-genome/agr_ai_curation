import { describe, expect, it } from 'vitest'

import {
  CURATION_CANDIDATE_ACTIONS,
  CURATION_SESSION_SORT_FIELDS,
  CURATION_SESSION_STATUSES,
  CURATION_SUBMISSION_STATUSES,
  type CurationWorkspaceResponse,
} from './types'

describe('curation workspace substrate types', () => {
  it('exposes the expected shared enum surfaces', () => {
    expect(CURATION_SESSION_STATUSES).toEqual([
      'new',
      'in_progress',
      'paused',
      'ready_for_submission',
      'submitted',
      'rejected',
    ])

    expect(CURATION_CANDIDATE_ACTIONS).toEqual([
      'accept',
      'reject',
      'reset',
    ])

    expect(CURATION_SESSION_SORT_FIELDS).toEqual([
      'prepared_at',
      'last_worked_at',
      'status',
      'document_title',
      'candidate_count',
      'validation',
      'evidence',
      'curator',
    ])

    expect(CURATION_SUBMISSION_STATUSES).toEqual([
      'preview_ready',
      'export_ready',
      'queued',
      'accepted',
      'validation_errors',
      'conflict',
      'manual_review_required',
      'failed',
    ])
  })

  it('mirrors the backend workspace payload shape', () => {
    const workspaceResponse: CurationWorkspaceResponse = {
      workspace: {
        session: {
          session_id: 'session-1',
          status: 'in_progress',
          adapter: {
            adapter_key: 'disease',
            profile_key: 'primary',
            display_label: 'Disease',
            profile_label: 'Primary',
            color_token: 'teal',
            metadata: {},
          },
          document: {
            document_id: 'document-1',
            title: 'Shared workspace contract paper',
            pmid: '123456',
            citation_label: 'PMID:123456',
            pdf_url: '/api/documents/document-1/pdf',
            viewer_url: '/documents/document-1/viewer',
          },
          flow_run_id: 'flow-run-1',
          progress: {
            total_candidates: 2,
            reviewed_candidates: 1,
            pending_candidates: 1,
            accepted_candidates: 1,
            rejected_candidates: 0,
            manual_candidates: 0,
          },
          validation: {
            state: 'completed',
            counts: {
              validated: 4,
              ambiguous: 1,
              not_found: 0,
              invalid_format: 0,
              conflict: 0,
              skipped: 0,
              overridden: 0,
            },
            last_validated_at: '2026-03-20T22:10:00Z',
            stale_field_keys: [],
            warnings: [],
          },
          evidence: {
            total_anchor_count: 3,
            resolved_anchor_count: 3,
            viewer_highlightable_anchor_count: 2,
            quality_counts: {
              exact_quote: 2,
              normalized_quote: 1,
              section_only: 0,
              page_only: 0,
              document_only: 0,
              unresolved: 0,
            },
            degraded: false,
            warnings: [],
          },
          current_candidate_id: 'candidate-1',
          assigned_curator: {
            actor_id: 'user-1',
            display_name: 'Curator One',
          },
          created_by: {
            actor_id: 'user-1',
            display_name: 'Curator One',
          },
          prepared_at: '2026-03-20T22:00:00Z',
          last_worked_at: '2026-03-20T22:15:00Z',
          notes: 'Ready for review',
          warnings: [],
          tags: ['priority'],
          session_version: 2,
          extraction_results: [
            {
              extraction_result_id: 'extract-1',
              document_id: 'document-1',
              adapter_key: 'disease',
              profile_key: 'primary',
              agent_key: 'curation_prep',
              source_kind: 'chat',
              candidate_count: 2,
              payload_json: { ok: true },
              created_at: '2026-03-20T21:55:00Z',
              metadata: {},
            },
          ],
          latest_submission: {
            submission_id: 'submission-1',
            session_id: 'session-1',
            adapter_key: 'disease',
            mode: 'preview',
            target_system: 'file_export',
            status: 'preview_ready',
            readiness: [
              {
                candidate_id: 'candidate-1',
                ready: true,
                blocking_reasons: [],
                warnings: [],
              },
            ],
            payload: {
              mode: 'preview',
              target_system: 'file_export',
              adapter_key: 'disease',
              candidate_ids: ['candidate-1'],
              payload_json: { ok: true },
              warnings: [],
            },
            requested_at: '2026-03-20T22:18:00Z',
            validation_errors: [],
            warnings: [],
          },
        },
        candidates: [
          {
            candidate_id: 'candidate-1',
            session_id: 'session-1',
            source: 'extracted',
            status: 'accepted',
            order: 0,
            adapter_key: 'disease',
            display_label: 'APOE association',
            confidence: 0.92,
            unresolved_ambiguities: [],
            draft: {
              draft_id: 'draft-1',
              candidate_id: 'candidate-1',
              adapter_key: 'disease',
              version: 3,
              fields: [
                {
                  field_key: 'gene_symbol',
                  label: 'Gene symbol',
                  value: 'APOE',
                  seed_value: 'APOE',
                  order: 0,
                  required: true,
                  read_only: false,
                  dirty: false,
                  stale_validation: false,
                  evidence_anchor_ids: ['anchor-1'],
                  validation_result: {
                    status: 'validated',
                    resolver: 'agr_db',
                    candidate_matches: [],
                    warnings: [],
                  },
                  metadata: {},
                },
              ],
              created_at: '2026-03-20T22:01:00Z',
              updated_at: '2026-03-20T22:12:00Z',
              metadata: {},
            },
            evidence_anchors: [
              {
                anchor_id: 'anchor-1',
                candidate_id: 'candidate-1',
                source: 'extracted',
                field_keys: ['gene_symbol'],
                field_group_keys: ['primary'],
                is_primary: true,
                anchor: {
                  anchor_kind: 'snippet',
                  locator_quality: 'exact_quote',
                  supports_decision: 'supports',
                  snippet_text: 'APOE was linked to the phenotype.',
                  chunk_ids: ['chunk-1'],
                },
                created_at: '2026-03-20T22:02:00Z',
                updated_at: '2026-03-20T22:02:00Z',
                warnings: [],
              },
            ],
            validation: {
              state: 'completed',
              counts: {
                validated: 1,
                ambiguous: 0,
                not_found: 0,
                invalid_format: 0,
                conflict: 0,
                skipped: 0,
                overridden: 0,
              },
              stale_field_keys: [],
              warnings: [],
            },
            evidence_summary: {
              total_anchor_count: 1,
              resolved_anchor_count: 1,
              viewer_highlightable_anchor_count: 1,
              quality_counts: {
                exact_quote: 1,
                normalized_quote: 0,
                section_only: 0,
                page_only: 0,
                document_only: 0,
                unresolved: 0,
              },
              degraded: false,
              warnings: [],
            },
            created_at: '2026-03-20T22:01:00Z',
            updated_at: '2026-03-20T22:12:00Z',
            metadata: {},
          },
        ],
        active_candidate_id: 'candidate-1',
        queue_context: {
          filters: {
            statuses: ['in_progress'],
            search: 'APOE',
          },
          sort_by: 'prepared_at',
          sort_direction: 'desc',
          position: 1,
          total_sessions: 3,
          next_session_id: 'session-2',
        },
        action_log: [
          {
            action_id: 'action-1',
            session_id: 'session-1',
            candidate_id: 'candidate-1',
            action_type: 'candidate_accepted',
            actor_type: 'user',
            actor: {
              actor_id: 'user-1',
              display_name: 'Curator One',
            },
            occurred_at: '2026-03-20T22:12:00Z',
            previous_candidate_status: 'pending',
            new_candidate_status: 'accepted',
            changed_field_keys: [],
            evidence_anchor_ids: ['anchor-1'],
            metadata: {},
          },
        ],
        submission_history: [],
      },
    }

    expect(workspaceResponse.workspace.session.status).toBe('in_progress')
    expect(workspaceResponse.workspace.candidates[0].draft.fields[0].field_key).toBe(
      'gene_symbol'
    )
    expect(
      workspaceResponse.workspace.candidates[0].evidence_anchors[0].anchor.locator_quality
    ).toBe('exact_quote')
    expect(workspaceResponse.workspace.queue_context?.next_session_id).toBe('session-2')
  })
})

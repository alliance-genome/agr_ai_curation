import { describe, expect, it } from 'vitest'

import type { CurationWorkspace } from '../types'
import {
  appendWorkspaceActionLogEntry,
  applyDraftFieldChangesToWorkspace,
  buildWorkspaceExpectedEnvelopeRevisions,
  mergeSubmissionExecutionIntoWorkspace,
} from './workspaceState'

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'gene',
        display_label: 'Gene',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Workspace document',
      },
      progress: {
        total_candidates: 1,
        reviewed_candidates: 0,
        pending_candidates: 1,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: 'candidate-1',
      prepared_at: '2026-03-20T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    entity_tags: [],
    candidates: [
      {
        candidate_id: 'candidate-1',
        session_id: 'session-1',
        source: 'manual',
        status: 'pending',
        order: 0,
        adapter_key: 'gene',
        display_label: 'Candidate 1',
        projection_ref: {
          envelope_id: 'envelope-1',
          object_id: 'object-1',
          envelope_revision: 4,
        },
        draft: {
          draft_id: 'draft-1',
          candidate_id: 'candidate-1',
          adapter_key: 'gene',
          version: 1,
          fields: [
            {
              field_key: 'gene_symbol',
              label: 'Gene symbol',
              value: 'BRCA1',
              seed_value: 'BRCA1',
              field_type: 'string',
              group_key: 'primary_data',
              group_label: 'Primary data',
              order: 0,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {},
            },
          ],
          created_at: '2026-03-20T12:00:00Z',
          updated_at: '2026-03-20T12:00:00Z',
          metadata: {},
        },
        evidence_anchors: [],
        created_at: '2026-03-20T12:00:00Z',
        updated_at: '2026-03-20T12:00:00Z',
        metadata: {},
      },
    ],
    active_candidate_id: 'candidate-1',
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function getOnlyField(workspace: CurationWorkspace) {
  return workspace.candidates[0].draft.fields[0]
}

describe('workspaceState', () => {
  it('clears stale validation when a queued revert restores the seed value', () => {
    const workspace = buildWorkspace()

    const editedWorkspace = applyDraftFieldChangesToWorkspace(
      workspace,
      'candidate-1',
      [
        {
          field_key: 'gene_symbol',
          value: 'BRCA2',
        },
      ],
    )

    expect(getOnlyField(editedWorkspace)).toMatchObject({
      value: 'BRCA2',
      dirty: true,
      stale_validation: true,
    })

    const revertedWorkspace = applyDraftFieldChangesToWorkspace(
      editedWorkspace,
      'candidate-1',
      [
        {
          field_key: 'gene_symbol',
          revert_to_seed: true,
        },
      ],
    )

    expect(getOnlyField(revertedWorkspace)).toMatchObject({
      value: 'BRCA1',
      dirty: false,
      stale_validation: false,
    })
  })

  it('appends action-log entries without duplicating an existing action id', () => {
    const workspace = buildWorkspace()
    const actionLogEntry = {
      action_id: 'action-1',
      session_id: 'session-1',
      candidate_id: 'candidate-1',
      action_type: 'candidate_accepted' as const,
      actor_type: 'user' as const,
      occurred_at: '2026-03-20T13:00:00Z',
      changed_field_keys: [],
      evidence_anchor_ids: [],
      metadata: {},
    }

    const firstAppend = appendWorkspaceActionLogEntry(workspace, actionLogEntry)
    const secondAppend = appendWorkspaceActionLogEntry(firstAppend, actionLogEntry)

    expect(firstAppend.action_log).toHaveLength(1)
    expect(secondAppend.action_log).toHaveLength(1)
    expect(secondAppend.action_log[0]).toMatchObject({
      action_id: 'action-1',
      action_type: 'candidate_accepted',
    })
  })

  it('builds expected envelope revision checks from workspace projections', () => {
    const workspace = buildWorkspace()

    expect(buildWorkspaceExpectedEnvelopeRevisions(workspace.candidates)).toEqual({
      'envelope-1': 4,
    })
  })

  it('merges executed submissions into session state, history, and action log', () => {
    const workspace = buildWorkspace()
    const response = {
      submission: {
        submission_id: 'submission-1',
        session_id: 'session-1',
        adapter_key: 'gene',
        mode: 'direct_submit' as const,
        target_key: 'review_export_bundle',
        status: 'accepted' as const,
        readiness: [
          {
            candidate_id: 'candidate-1',
            ready: true,
            blocking_reasons: [],
            warnings: [],
            blockers: [],
          },
        ],
        payload: null,
        requested_at: '2026-03-20T14:00:00Z',
        completed_at: '2026-03-20T14:00:01Z',
        external_reference: 'noop:review_export_bundle:1',
        response_message: 'Accepted.',
        validation_errors: [],
        warnings: [],
        submission_state: {},
        target_result_history: [],
      },
      session: {
        ...workspace.session,
        status: 'submitted' as const,
        latest_submission: {
          submission_id: 'submission-1',
          session_id: 'session-1',
          adapter_key: 'gene',
          mode: 'direct_submit' as const,
          target_key: 'review_export_bundle',
          status: 'accepted' as const,
          readiness: [],
          payload: null,
          requested_at: '2026-03-20T14:00:00Z',
          validation_errors: [],
          warnings: [],
          submission_state: {},
          target_result_history: [],
        },
      },
      action_log_entry: {
        action_id: 'action-submit-1',
        session_id: 'session-1',
        action_type: 'submission_executed' as const,
        actor_type: 'user' as const,
        occurred_at: '2026-03-20T14:00:01Z',
        changed_field_keys: [],
        evidence_anchor_ids: [],
        metadata: {},
      },
    }

    const nextWorkspace = mergeSubmissionExecutionIntoWorkspace(workspace, response)

    expect(nextWorkspace.session.status).toBe('submitted')
    expect(nextWorkspace.submission_history).toHaveLength(1)
    expect(nextWorkspace.submission_history[0].submission_id).toBe('submission-1')
    expect(nextWorkspace.action_log).toHaveLength(1)
    expect(nextWorkspace.action_log[0].action_type).toBe('submission_executed')
  })
})

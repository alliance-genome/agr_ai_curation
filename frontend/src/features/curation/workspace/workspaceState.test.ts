import { describe, expect, it } from 'vitest'

import type { CurationWorkspace } from '../types'
import {
  appendWorkspaceActionLogEntry,
  applyDraftFieldChangesToWorkspace,
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
    candidates: [
      {
        candidate_id: 'candidate-1',
        session_id: 'session-1',
        source: 'manual',
        status: 'pending',
        order: 0,
        adapter_key: 'gene',
        display_label: 'Candidate 1',
        unresolved_ambiguities: [],
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
})

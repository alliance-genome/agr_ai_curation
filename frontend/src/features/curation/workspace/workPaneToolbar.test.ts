import { describe, expect, it } from 'vitest'

import type { CurationCandidate } from '@/features/curation/types'
import { countValidatedPending } from './workPaneToolbar'

function candidate(overrides: Partial<CurationCandidate> = {}): CurationCandidate {
  return {
    candidate_id: 'candidate-1',
    session_id: 'session-1',
    source: 'extracted',
    status: 'pending',
    order: 0,
    adapter_key: 'domain-pack',
    draft: {
      draft_id: 'draft-1',
      candidate_id: 'candidate-1',
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
    ...overrides,
  }
}

function validatedCandidate(id: string): CurationCandidate {
  return candidate({
    candidate_id: id,
    validation: {
      state: 'completed',
      counts: {
        validated: 2,
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
  })
}

describe('countValidatedPending', () => {
  it('counts pending candidates with completed non-blocking validation', () => {
    expect(countValidatedPending([
      validatedCandidate('validated-a'),
      validatedCandidate('validated-b'),
      candidate({ candidate_id: 'accepted', status: 'accepted' }),
    ])).toBe(2)
  })

  it('excludes candidates with unresolved envelope findings', () => {
    expect(countValidatedPending([
      {
        ...validatedCandidate('blocked'),
        validation_summary_projections: [
          {
            summary_id: 'summary-1',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.symbol',
            envelope_revision: 1,
            status: 'blocked',
            highest_severity: 'error',
            finding_count: 1,
            open_finding_count: 1,
            finding_ids: ['finding-1'],
            codes: ['blocked'],
            messages: ['Blocked.'],
            findings: [],
          },
        ],
      },
    ])).toBe(0)
  })

  it('counts pending candidates whose envelope summaries are all resolved', () => {
    expect(countValidatedPending([
      candidate({
        candidate_id: 'resolved-envelope',
        validation_summary_projections: [
          {
            summary_id: 'summary-1',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.symbol',
            envelope_revision: 1,
            status: 'resolved',
            highest_severity: null,
            finding_count: 1,
            open_finding_count: 0,
            finding_ids: ['finding-1'],
            codes: ['resolved'],
            messages: ['Resolved.'],
            findings: [],
          },
        ],
      }),
    ])).toBe(1)
  })
})

import { describe, expect, it } from 'vitest'

import type { CurationCandidate } from '@/features/curation/types'
import { canAcceptPendingCandidate, countValidatedPending } from './workPaneToolbar'

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
        conflict: 0, unresolved: 0,
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

describe('canAcceptPendingCandidate', () => {
  const skippedOnly: CurationCandidate['validation'] = {
    state: 'completed',
    counts: {
      validated: 0,
      ambiguous: 0,
      not_found: 0,
      invalid_format: 0,
      conflict: 0,
      unresolved: 0,
      skipped: 7,
      overridden: 0,
    },
    stale_field_keys: [],
    warnings: ['No envelope validation findings targeted this field.'],
  }

  it('lets a curator accept a pending record that has no validators to wait for', () => {
    const record = candidate({ has_active_validators: false, validation: skippedOnly })

    expect(canAcceptPendingCandidate(record)).toBe(true)
    expect(countValidatedPending([record])).toBe(0)
  })

  it('still requires a completed validation result when validators exist', () => {
    expect(canAcceptPendingCandidate(candidate({
      has_active_validators: true,
      validation: skippedOnly,
    }))).toBe(false)
    expect(canAcceptPendingCandidate(validatedCandidate('validated'))).toBe(true)
  })

  it('never offers Accept on an already decided record', () => {
    expect(canAcceptPendingCandidate(candidate({
      has_active_validators: false,
      status: 'accepted',
    }))).toBe(false)
  })

  it('keeps a blocking finding authoritative when no validators are configured', () => {
    expect(canAcceptPendingCandidate(candidate({
      has_active_validators: false,
      validation_summary_projections: [{
        status: 'blocked',
        open_finding_count: 1,
      } as NonNullable<CurationCandidate['validation_summary_projections']>[number]],
    }))).toBe(false)
  })
})

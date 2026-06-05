import { describe, expect, it } from 'vitest'

import type {
  CurationDraftField,
  DomainEnvelopeValidationStatus,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'
import { fieldState, sortFieldsNeedsReviewFirst } from './fieldState'

function field(overrides: Partial<CurationDraftField> = {}): CurationDraftField {
  return {
    field_key: 'field_a',
    label: 'Field A',
    value: 'value',
    seed_value: 'value',
    field_type: 'string',
    group_key: 'details',
    group_label: 'Details',
    order: 0,
    required: false,
    read_only: false,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: {},
    ...overrides,
  }
}

function summary(status: DomainEnvelopeValidationStatus): DomainEnvelopeValidationSummaryProjection {
  return {
    summary_id: `summary-${status}`,
    envelope_id: 'envelope-1',
    object_id: 'object-1',
    object_type: 'gene',
    field_path: 'field_a',
    envelope_revision: 1,
    status,
    highest_severity: null,
    finding_count: 1,
    open_finding_count: status === 'resolved' || status === 'waived' ? 0 : 1,
    finding_ids: [],
    codes: [],
    messages: [],
    findings: [],
  }
}

describe('fieldState', () => {
  it('flags unresolved and blocked validation summaries as needs-review', () => {
    expect(fieldState(field(), [summary('unresolved')])).toBe('needs-review')
    expect(fieldState(field(), [summary('blocked')])).toBe('needs-review')
  })

  it('flags resolved and waived validation summaries as resolved', () => {
    expect(fieldState(field(), [summary('resolved')])).toBe('resolved')
    expect(fieldState(field(), [summary('waived')])).toBe('resolved')
  })

  it('flags planned, under-development, and missing summaries as ai-unconfirmed', () => {
    expect(fieldState(field(), [summary('planned')])).toBe('ai-unconfirmed')
    expect(fieldState(field(), [summary('under_development')])).toBe('ai-unconfirmed')
    expect(fieldState(field(), [])).toBe('ai-unconfirmed')
  })

  it('does not read legacy field validation_result status values', () => {
    expect(
      fieldState(
        field({
          validation_result: {
            status: 'conflict',
            candidate_matches: [],
            warnings: ['Legacy conflict.'],
          },
        }),
        [],
      ),
    ).toBe('ai-unconfirmed')
  })
})

describe('sortFieldsNeedsReviewFirst', () => {
  it('floats needs-review fields to the top, preserving order otherwise', () => {
    const fields = [
      field({ field_key: 'normal_a', order: 0 }),
      field({ field_key: 'blocked_b', order: 1 }),
      field({ field_key: 'normal_c', order: 2 }),
      field({ field_key: 'unresolved_d', order: 3 }),
    ]
    const summariesByKey = new Map([
      ['blocked_b', [summary('blocked')]],
      ['unresolved_d', [summary('unresolved')]],
    ])

    expect(
      sortFieldsNeedsReviewFirst(
        fields,
        (candidateField) => summariesByKey.get(candidateField.field_key) ?? [],
      ).map((candidateField) => candidateField.field_key),
    ).toEqual(['blocked_b', 'unresolved_d', 'normal_a', 'normal_c'])
  })
})

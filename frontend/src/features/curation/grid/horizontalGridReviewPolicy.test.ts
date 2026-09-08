import { describe, expect, it } from 'vitest'

import type { CurationDraftField } from '@/features/curation/types'
import { isHorizontalGridDecisionField } from './horizontalGridReviewPolicy'

function metadata(policy: Record<string, unknown>): Record<string, unknown> {
  return { workspace_display: { review_policy: policy } }
}

function field(fieldPath: string, groupKey: string | null): CurationDraftField {
  return {
    field_key: fieldPath,
    group_key: groupKey,
    metadata: { source_field_path: fieldPath },
  } as unknown as CurationDraftField
}

describe('horizontal grid review policy', () => {
  it('selects custom decision groups without package or object identifiers', () => {
    const grouped = metadata({ mode: 'groups', decision_groups: ['identity', 'decision'] })
    expect(isHorizontalGridDecisionField(grouped, field('record.label', 'identity'))).toBe(true)
    expect(isHorizontalGridDecisionField(grouped, field('classification', 'decision'))).toBe(true)
    expect(isHorizontalGridDecisionField(grouped, field('notes', 'context'))).toBe(false)
    expect(isHorizontalGridDecisionField(grouped, field('ungrouped', null))).toBe(false)
  })

  it('selects full envelope field paths for a custom package', () => {
    const selected = metadata({ mode: 'fields', decision_fields: ['record.label', 'description'] })
    expect(isHorizontalGridDecisionField(selected, field('record.label', null))).toBe(true)
    expect(isHorizontalGridDecisionField(selected, field('description', 'context'))).toBe(true)
    expect(isHorizontalGridDecisionField(selected, field('label', null))).toBe(false)
    expect(isHorizontalGridDecisionField(selected, field('confidence', null))).toBe(false)
  })

  it.each([null, {}, { workspace_display: {} }, { workspace_display: { review_policy: null } }])(
    'keeps every field when live metadata is undeclared: %j',
    (rowMetadata) => {
      for (const path of ['label', 'confidence', 'notes', 'new_decision']) {
        expect(isHorizontalGridDecisionField(rowMetadata, field(path, null))).toBe(true)
      }
    },
  )

  it('honors explicit all mode', () => {
    expect(isHorizontalGridDecisionField(metadata({ mode: 'all' }), field('notes', null))).toBe(true)
  })

  it.each([
    { workspace_display: [] },
    { workspace_display: { review_policy: [] } },
    metadata({ mode: 'unknown' }),
    metadata({ mode: 'groups' }),
    metadata({ mode: 'fields', decision_fields: [''] }),
  ])('reports malformed declarations instead of silently changing visibility: %j', (rowMetadata) => {
    expect(() => isHorizontalGridDecisionField(rowMetadata, field('label', null))).toThrow('workspace_display')
  })
})

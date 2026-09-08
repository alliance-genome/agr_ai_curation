import { describe, expect, it } from 'vitest'

import type { CurationCandidate, CurationDraftField } from '@/features/curation/types'
import {
  isHorizontalGridDecisionField,
} from './horizontalGridReviewPolicy'

function candidate(domainPackId: string, objectType: string, policy?: Record<string, unknown>): CurationCandidate {
  return {
    adapter_key: domainPackId,
    metadata: {
      domain_pack_id: domainPackId, object_type: objectType,
      review_row_metadata: { workspace_display: { review_policy: policy } },
    },
  } as unknown as CurationCandidate
}

function field(fieldPath: string, groupKey: string | null): CurationDraftField {
  return {
    field_key: fieldPath,
    group_key: groupKey,
    metadata: { source_field_path: fieldPath },
  } as unknown as CurationDraftField
}

describe('horizontal grid review policy', () => {
  it('separates gene and GO decisions from supporting envelope context', () => {
    const gene = candidate('gene', 'gene_mention_evidence', { mode: 'groups', decision_groups: ['identity'] })
    expect(isHorizontalGridDecisionField(gene, field('gene_symbol', 'identity'))).toBe(true)
    expect(isHorizontalGridDecisionField(gene, field('section', 'evidence_location'))).toBe(false)
    expect(isHorizontalGridDecisionField(gene, field('confidence', 'provenance'))).toBe(false)

    const go = candidate('agr.alliance.go', 'GOCuratableObject', { mode: 'groups', decision_groups: ['identity', 'annotation'] })
    expect(isHorizontalGridDecisionField(go, field('go_term.curie', 'annotation'))).toBe(true)
    expect(isHorizontalGridDecisionField(go, field('rationale', 'evidence'))).toBe(false)
    expect(isHorizontalGridDecisionField(go, field('provider_context', 'provider'))).toBe(false)
  })

  it('keeps generic record values while hiding extraction-process context', () => {
    const genericObject = candidate('generic', 'generic_object', { mode: 'fields', decision_fields: ['label', 'class_key', 'semantic_class', 'description', 'attributes'] })
    expect(isHorizontalGridDecisionField(genericObject, field('description', null))).toBe(true)
    expect(isHorizontalGridDecisionField(genericObject, field('confidence', null))).toBe(false)
    expect(isHorizontalGridDecisionField(genericObject, field('classification_notes', null))).toBe(false)

    const reagent = candidate('generic', 'generic_reagent_candidate', { mode: 'fields', decision_fields: ['label', 'class_key', 'source', 'source_identifier', 'count', 'reagent_type'] })
    expect(isHorizontalGridDecisionField(reagent, field('source_identifier', null))).toBe(true)
    expect(isHorizontalGridDecisionField(reagent, field('source_label', null))).toBe(false)
  })

  it('keeps all configured fields for export-shaped envelopes and unknown future types', () => {
    const disease = candidate('agr.alliance.disease', 'DiseaseAnnotation')
    expect(isHorizontalGridDecisionField(
      disease,
      field('data_provider.abbreviation', 'provenance'),
    )).toBe(true)

    const future = candidate('future.pack', 'FutureObject')
    expect(isHorizontalGridDecisionField(future, field('new_field', 'context'))).toBe(true)
  })
  it('applies custom package declarations without domain or object identifiers', () => {
    const custom = candidate('', '', { mode: 'fields', decision_fields: ['record.label'] })
    expect(isHorizontalGridDecisionField(custom, field('record.label', null))).toBe(true)
    expect(isHorizontalGridDecisionField(custom, field('notes', null))).toBe(false)
    const grouped = candidate('org.custom', 'Record', { mode: 'groups', decision_groups: ['decision'] })
    expect(isHorizontalGridDecisionField(grouped, field('record.label', 'decision'))).toBe(true)
    expect(isHorizontalGridDecisionField(grouped, field('notes', null))).toBe(false)
  })

  it.each([{}, { review_row_metadata: {} }, { review_row_metadata: { workspace_display: {} } }])(
    'keeps every field when metadata is undeclared: %j',
    (metadata) => {
      const undeclared = candidate('org.custom', 'Record')
      undeclared.metadata = metadata
      for (const path of ['label', 'confidence', 'notes', 'new_decision']) {
        expect(isHorizontalGridDecisionField(undeclared, field(path, null))).toBe(true)
      }
    },
  )

  it('honors explicit all mode', () => {
    expect(isHorizontalGridDecisionField(candidate('org.custom', 'Record', { mode: 'all' }), field('notes', null))).toBe(true)
  })

  it('reports malformed declarations instead of silently changing field visibility', () => {
    expect(() => isHorizontalGridDecisionField(candidate('org.custom', 'Record', { mode: 'groups' }), field('label', null))).toThrow('workspace_display.review_policy')
  })
})

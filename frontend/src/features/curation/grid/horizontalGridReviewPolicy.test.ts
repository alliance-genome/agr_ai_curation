import { describe, expect, it } from 'vitest'

import type { CurationCandidate, CurationDraftField } from '@/features/curation/types'
import {
  HORIZONTAL_GRID_REVIEW_POLICIES,
  isHorizontalGridDecisionField,
} from './horizontalGridReviewPolicy'

function candidate(domainPackId: string, objectType: string): CurationCandidate {
  return {
    adapter_key: domainPackId,
    metadata: { domain_pack_id: domainPackId, object_type: objectType },
  } as unknown as CurationCandidate
}

function field(fieldPath: string, groupKey: string | null): CurationDraftField {
  return {
    field_key: fieldPath,
    group_key: groupKey,
    metadata: { source_field_path: fieldPath },
  } as unknown as CurationDraftField
}

function configuredDomainPackId(objectType: string): string {
  const matchingKeys = Object.keys(HORIZONTAL_GRID_REVIEW_POLICIES)
    .filter((key) => key.endsWith(`:${objectType}`))
  expect(matchingKeys).toHaveLength(1)
  return matchingKeys[0].slice(0, matchingKeys[0].indexOf(':'))
}

describe('horizontal grid review policy', () => {
  it('catalogs every current workspace envelope object type', () => {
    const objectTypes = Object.keys(HORIZONTAL_GRID_REVIEW_POLICIES)
      .map((key) => key.slice(key.indexOf(':') + 1))
      .sort()
    expect(objectTypes).toEqual([
      'AGMDiseaseAnnotation',
      'Allele',
      'AlleleDiseaseAnnotation',
      'AllelePaperEvidenceAssociation',
      'DiseaseAnnotation',
      'GOCuratableObject',
      'GeneDiseaseAnnotation',
      'GeneExpressionAnnotation',
      'PhenotypeAnnotation',
      'PhenotypeSubject',
      'gene_mention_evidence',
      'generic_claim',
      'generic_object',
      'generic_reagent_candidate',
    ])
  })

  it('separates gene and GO decisions from supporting envelope context', () => {
    const gene = candidate('gene', 'gene_mention_evidence')
    expect(isHorizontalGridDecisionField(gene, field('gene_symbol', 'identity'))).toBe(true)
    expect(isHorizontalGridDecisionField(gene, field('section', 'evidence_location'))).toBe(false)
    expect(isHorizontalGridDecisionField(gene, field('confidence', 'provenance'))).toBe(false)

    const go = candidate(configuredDomainPackId('GOCuratableObject'), 'GOCuratableObject')
    expect(isHorizontalGridDecisionField(go, field('go_term.curie', 'annotation'))).toBe(true)
    expect(isHorizontalGridDecisionField(go, field('rationale', 'evidence'))).toBe(false)
    expect(isHorizontalGridDecisionField(go, field('provider_context', 'provider'))).toBe(false)
  })

  it('keeps generic record values while hiding extraction-process context', () => {
    const genericObject = candidate('generic', 'generic_object')
    expect(isHorizontalGridDecisionField(genericObject, field('description', null))).toBe(true)
    expect(isHorizontalGridDecisionField(genericObject, field('confidence', null))).toBe(false)
    expect(isHorizontalGridDecisionField(genericObject, field('classification_notes', null))).toBe(false)

    const reagent = candidate('generic', 'generic_reagent_candidate')
    expect(isHorizontalGridDecisionField(reagent, field('source_identifier', null))).toBe(true)
    expect(isHorizontalGridDecisionField(reagent, field('source_label', null))).toBe(false)
  })

  it('keeps all configured fields for export-shaped envelopes and unknown future types', () => {
    const disease = candidate(configuredDomainPackId('DiseaseAnnotation'), 'DiseaseAnnotation')
    expect(isHorizontalGridDecisionField(
      disease,
      field('data_provider.abbreviation', 'provenance'),
    )).toBe(true)

    const future = candidate('future.pack', 'FutureObject')
    expect(isHorizontalGridDecisionField(future, field('new_field', 'context'))).toBe(true)
  })
})

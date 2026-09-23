import { describe, expect, it } from 'vitest'

import type { DomainEnvelopeReviewResolvedValue } from '@/features/curation/types'
import {
  HORIZONTAL_GRID_OVERRIDE_INCOMPLETE_MESSAGE,
  horizontalGridOverridePatch,
  horizontalGridOverrideProblem,
  horizontalGridRemoveOverridePatch,
  isHorizontalGridProfileAttributePath,
} from './horizontalGridOverride'

function value(overrides: Partial<DomainEnvelopeReviewResolvedValue> = {}): DomainEnvelopeReviewResolvedValue {
  return {
    value_path: 'subject',
    display_text: 'UNRESOLVED',
    mention: 'abc-1',
    resolution_state: 'unresolved',
    lookup_outcome: 'not_found',
    lookup_result: 'Not found',
    validator_explanation: null,
    validator_curator_message: null,
    override_disagreements: [],
    identity_field_paths: ['subject.curie', 'subject.name', 'subject.taxon'],
    id_key: 'curie',
    label_key: 'name',
    validated_keys: ['taxon'],
    stored_identity: { curie: null, name: null, taxon: null },
    container_protected: false,
    ...overrides,
  }
}

const IDENTITY = { identifier: ' GENE:2 ', name: 'abc-2' }

describe('curator override patches', () => {
  it('sends a field value as one replace_identity naming only its identity keys', () => {
    expect(horizontalGridOverridePatch(value(), IDENTITY)).toEqual({
      operation: 'replace_identity',
      field_path: 'subject.curie',
      value: { curie: 'GENE:2', name: 'abc-2' },
      before: { curie: null, name: null },
    })
    expect(horizontalGridRemoveOverridePatch(value({
      stored_identity: { curie: 'GENE:2', name: 'abc-2', taxon: 'TAXON:9' },
    }))).toEqual({
      operation: 'replace_identity',
      field_path: 'subject.curie',
      value: { curie: null, name: null, taxon: null },
      before: { curie: 'GENE:2', name: 'abc-2', taxon: 'TAXON:9' },
    })
  })

  it('names the bare identity field for a value that is the object itself', () => {
    const root = value({
      value_path: '',
      id_key: 'primary_external_id',
      label_key: 'gene_symbol',
      stored_identity: { primary_external_id: null, gene_symbol: null },
    })
    expect(horizontalGridOverridePatch(root, IDENTITY)).toMatchObject({
      operation: 'replace_identity',
      field_path: 'primary_external_id',
    })
  })

  it('sends a profile attribute value as one whole-value replace', () => {
    const stored = {
      mention: 'abc one',
      curie: null,
      name: null,
      proposed_curie: 'GENE:1',
      resolution_state: 'unresolved',
      lookup_outcome: 'not_found',
    }
    const profile = value({
      value_path: 'attributes.genes[0]',
      identity_field_paths: ['attributes.genes[0].curie', 'attributes.genes[0].name'],
      validated_keys: [],
      stored_identity: { curie: null, name: null },
      stored_value: stored,
    })

    expect(horizontalGridOverridePatch(profile, IDENTITY)).toEqual({
      operation: 'replace',
      field_path: 'attributes.genes[0]',
      // Every other key stays as stored: paper wording, state and the proposal.
      value: { ...stored, curie: 'GENE:2', name: 'abc-2' },
      before: stored,
    })
    expect(horizontalGridRemoveOverridePatch(profile)).toEqual({
      operation: 'replace',
      field_path: 'attributes.genes[0]',
      value: { ...stored, curie: null, name: null },
      before: stored,
    })
    expect(() => horizontalGridOverridePatch({ ...profile, stored_value: null }, IDENTITY)).toThrow(
      "Profile value 'attributes.genes[0]' carries no stored value to replace",
    )
  })

  it('recognises profile attribute paths as the backend does', () => {
    expect(['attributes', 'attributes.gene', 'attributes[0]'].map(isHorizontalGridProfileAttributePath))
      .toEqual([true, true, true])
    expect(['subject', 'attributes_extra', 'x.attributes'].map(isHorizontalGridProfileAttributePath))
      .toEqual([false, false, false])
  })

  it('needs both the identifier and the name', () => {
    expect(horizontalGridOverrideProblem(value(), { identifier: 'GENE:2', name: ' ' }))
      .toBe(HORIZONTAL_GRID_OVERRIDE_INCOMPLETE_MESSAGE)
    expect(horizontalGridOverrideProblem(value(), IDENTITY)).toBeNull()
  })
})

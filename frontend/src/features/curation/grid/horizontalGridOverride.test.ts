import { describe, expect, it } from 'vitest'

import type { DomainEnvelopeReviewResolvedValue } from '@/features/curation/types'
import {
  horizontalGridIdentityKeyLabel,
  horizontalGridIdentityKeys,
  horizontalGridOverrideIdentity,
  horizontalGridOverridePatch,
  horizontalGridOverrideProblem,
  horizontalGridRemovableElement,
  horizontalGridRemoveElementPatch,
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
    stored_identity: { curie: null, name: null, taxon: 'NCBITaxon:6239' },
    container_protected: false,
    overridable: true,
    ...overrides,
  }
}

const IDENTITY = { curie: ' GENE:2 ', name: 'abc-2', taxon: 'NCBITaxon:7227' }

describe('curator override patches', () => {
  it('sends every identity key of a field value, validated keys too, as one replace_identity', () => {
    expect(horizontalGridOverridePatch(value(), IDENTITY)).toEqual({
      operation: 'replace_identity',
      field_path: 'subject.curie',
      value: { curie: 'GENE:2', name: 'abc-2', taxon: 'NCBITaxon:7227' },
      before: { curie: null, name: null, taxon: 'NCBITaxon:6239' },
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

  it('prefills every identity key from the stored value and labels it for curators', () => {
    const vocabularyTerm = value({
      id_key: 'id',
      label_key: 'name',
      validated_keys: ['vocabulary', 'curie'],
      stored_identity: { id: 'ONT:1', name: 'adult', vocabulary: 'stage_uberon_slim_terms', curie: null },
    })

    expect(horizontalGridIdentityKeys(vocabularyTerm)).toEqual(['id', 'name', 'vocabulary', 'curie'])
    expect(horizontalGridOverrideIdentity(vocabularyTerm)).toEqual({
      id: 'ONT:1', name: 'adult', vocabulary: 'stage_uberon_slim_terms', curie: '',
    })
    expect(horizontalGridIdentityKeys(vocabularyTerm).map((key) => horizontalGridIdentityKeyLabel(vocabularyTerm, key)))
      .toEqual(['Identifier', 'Name', 'Vocabulary', 'CURIE'])
    expect(horizontalGridIdentityKeyLabel(value(), 'reference_id')).toBe('Reference ID')
  })

  it('names the bare identity field for a value that is the object itself', () => {
    const root = value({
      value_path: '',
      id_key: 'primary_external_id',
      label_key: 'gene_symbol',
      stored_identity: { primary_external_id: null, gene_symbol: null, taxon: null },
    })
    expect(horizontalGridOverridePatch(root, { primary_external_id: 'GENE:2', gene_symbol: 'abc-2', taxon: 'T:1' }))
      .toMatchObject({
        operation: 'replace_identity',
        field_path: 'primary_external_id',
        value: { primary_external_id: 'GENE:2', gene_symbol: 'abc-2', taxon: 'T:1' },
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

  it('requires the identifier and the name with the backend\'s wording; a validated key may be empty', () => {
    expect(horizontalGridOverrideProblem(value(), { ...IDENTITY, curie: '', name: ' ' }))
      .toBe('Enter both the identifier and the name for a curator override.')
    expect(horizontalGridOverrideProblem(value(), { ...IDENTITY, name: ' ' }))
      .toBe('Enter the name for a curator override.')
    expect(horizontalGridOverrideProblem(value(), { ...IDENTITY, curie: '' }))
      .toBe('Enter the identifier for a curator override.')
    expect(horizontalGridOverrideProblem(value({ label_key: null }), { curie: '', taxon: 'T:1' }))
      .toBe('Enter the identifier for a curator override.')
    expect(horizontalGridOverrideProblem(value({ id_key: null }), { name: '', taxon: 'T:1' }))
      .toBe('Enter the name for a curator override.')
    expect(horizontalGridOverrideProblem(value(), { ...IDENTITY, taxon: '' })).toBeNull()
    // An empty validated key is still sent, as null.
    expect(horizontalGridOverridePatch(value(), { ...IDENTITY, taxon: ' ' }).value)
      .toEqual({ curie: 'GENE:2', name: 'abc-2', taxon: null })
  })

  it('removes a stored list element with the element itself as before', () => {
    const element = value({
      value_path: 'evidence_codes[1]',
      stored_value: { mention: 'IGI', curie: null, resolution_state: 'unresolved', lookup_outcome: 'not_found' },
    })
    expect(horizontalGridRemovableElement(element)).toBe(true)
    expect(horizontalGridRemoveElementPatch(element)).toEqual({
      operation: 'remove',
      field_path: 'evidence_codes[1]',
      value: null,
      before: element.stored_value,
    })
    // Only a list element with its stored value can be removed.
    expect(horizontalGridRemovableElement(value())).toBe(false)
    expect(horizontalGridRemovableElement({ ...element, stored_value: null })).toBe(false)
  })
})

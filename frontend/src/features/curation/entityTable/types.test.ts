import { describe, expect, it } from 'vitest'
import {
  ENTITY_TAG_DECISIONS,
  ENTITY_TAG_SOURCES,
  DB_VALIDATION_STATUSES,
  ENTITY_TYPE_CODES,
  getEntityTypeLabel,
  type EntityTag,
} from './types'

describe('EntityTag type constants', () => {
  it('defines three decision states', () => {
    expect(ENTITY_TAG_DECISIONS).toEqual(['pending', 'accepted', 'rejected'])
  })

  it('defines two source types', () => {
    expect(ENTITY_TAG_SOURCES).toEqual(['ai', 'manual'])
  })

  it('defines three DB validation statuses', () => {
    expect(DB_VALIDATION_STATUSES).toEqual(['validated', 'ambiguous', 'not_found'])
  })

  it('defines the literature UI entity type ATP codes', () => {
    expect(ENTITY_TYPE_CODES).toContain('ATP:0000005') // gene
    expect(ENTITY_TYPE_CODES).toContain('ATP:0000006') // allele
    expect(ENTITY_TYPE_CODES).toContain('ATP:0000123') // species
  })

  it('allows constructing a valid EntityTag object', () => {
    const tag: EntityTag = {
      tag_id: 'tag-1',
      entity_name: 'daf-2',
      entity_type: 'ATP:0000005',
      species: 'NCBITaxon:6239',
      topic: 'gene expression',
      db_status: 'validated',
      db_entity_id: 'WBGene00000898',
      source: 'ai',
      decision: 'pending',
      evidence: {
        sentence_text: 'The daf-2 receptor regulates lifespan.',
        page_number: 3,
        section_title: 'Results',
        chunk_ids: ['chunk-1'],
      },
      notes: null,
    }
    expect(tag.tag_id).toBe('tag-1')
    expect(tag.evidence?.sentence_text).toContain('daf-2')
  })

  it('fails loudly when an unknown entity type code is rendered', () => {
    expect(() => getEntityTypeLabel('CUSTOM:entity_type')).toThrow(/Unknown entity type code/i)
  })
})

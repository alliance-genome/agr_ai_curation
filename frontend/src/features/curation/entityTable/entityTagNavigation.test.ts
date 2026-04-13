import type { CurationEvidenceRecord } from '@/features/curation/types'
import { describe, expect, it } from 'vitest'
import { buildEntityTagNavigationCommand } from './entityTagNavigation'
import type { EntityTag } from './types'

const makeTag = (overrides: Partial<EntityTag> = {}): EntityTag => ({
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
  ...overrides,
})

const makeEvidenceRecord = (
  overrides: Partial<CurationEvidenceRecord> = {},
): CurationEvidenceRecord => ({
  anchor_id: 'anchor-1',
  candidate_id: 'tag-1',
  source: 'extracted',
  field_keys: ['gene_symbol'],
  field_group_keys: ['primary'],
  is_primary: true,
  anchor: {
    anchor_kind: 'snippet',
    locator_quality: 'exact_quote',
    supports_decision: 'supports',
    sentence_text: 'The PDF anchor quote is longer than the legacy preview.',
    snippet_text: 'The PDF anchor quote is longer than the legacy preview.',
    viewer_search_text: 'The PDF anchor quote is longer than the legacy preview.',
    viewer_highlightable: true,
    page_number: 7,
    section_title: 'Detailed Results',
    chunk_ids: ['chunk-7'],
  },
  created_at: '2026-03-31T00:00:00Z',
  updated_at: '2026-03-31T00:00:00Z',
  warnings: [],
  ...overrides,
})

describe('buildEntityTagNavigationCommand', () => {
  it('builds a navigation command from a tag with evidence', () => {
    const command = buildEntityTagNavigationCommand(makeTag())

    expect(command.anchorId).toContain('entity-tag:tag-1')
    expect(command.searchText).toBe('The daf-2 receptor regulates lifespan.')
    expect(command.pageNumber).toBe(3)
    expect(command.sectionTitle).toBe('Results')
    expect(command.mode).toBe('select')
    expect(command.anchor.anchor_kind).toBe('sentence')
    expect(command.anchor.locator_quality).toBe('exact_quote')
    expect(command.anchor.sentence_text).toBe('The daf-2 receptor regulates lifespan.')
    expect(command.anchor.page_number).toBe(3)
    expect(command.anchor.section_title).toBe('Results')
    expect(command.anchor.viewer_search_text).toBe('The daf-2 receptor regulates lifespan.')
    expect(command.anchor.viewer_highlightable).toBe(true)
    expect(command.anchor.chunk_ids).toEqual(['chunk-1'])
  })

  it('returns null for a tag without evidence', () => {
    const command = buildEntityTagNavigationCommand(
      makeTag({ evidence: null }),
    )
    expect(command).toBeNull()
  })

  it('returns null for a tag with empty sentence text', () => {
    const command = buildEntityTagNavigationCommand(
      makeTag({ evidence: { sentence_text: '  ', page_number: 3, section_title: 'Results', chunk_ids: [] } }),
    )
    expect(command).toBeNull()
  })

  it('prefers candidate evidence anchors over flattened tag preview evidence', () => {
    const command = buildEntityTagNavigationCommand(makeTag(), makeEvidenceRecord())

    expect(command?.anchorId).toBe('anchor-1')
    expect(command?.searchText).toBe('The PDF anchor quote is longer than the legacy preview.')
    expect(command?.pageNumber).toBe(7)
    expect(command?.sectionTitle).toBe('Detailed Results')
    expect(command?.anchor.chunk_ids).toEqual(['chunk-7'])
  })

  it('uses the displayed sentence quote for navigation even when persisted anchor search text is noisier', () => {
    const command = buildEntityTagNavigationCommand(
      makeTag(),
      makeEvidenceRecord({
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'normalized_quote',
          supports_decision: 'supports',
          sentence_text: 'crb accumulated to a higher molar abundance in mutant fly eyes.',
          snippet_text: '2.3. crb accumulated to a higher molar abundance in mutant fly eyes.',
          normalized_text: 'crb accumulated to a higher molar abundance in mutant fly eyes.',
          viewer_search_text: '2.3. crb accumulated to a higher molar abundance in mutant fly eyes.',
          viewer_highlightable: true,
          page_number: 6,
          section_title: 'Results',
          subsection_title: 'The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes',
          chunk_ids: ['chunk-6'],
        },
      }),
    )

    expect(command?.searchText).toBe('crb accumulated to a higher molar abundance in mutant fly eyes.')
    expect(command?.anchor.snippet_text).toBe('crb accumulated to a higher molar abundance in mutant fly eyes.')
    expect(command?.anchor.sentence_text).toBe('crb accumulated to a higher molar abundance in mutant fly eyes.')
    expect(command?.anchor.viewer_search_text).toBe('crb accumulated to a higher molar abundance in mutant fly eyes.')
    expect(command?.anchor.subsection_title).toBe('The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes')
    expect(command?.anchor.locator_quality).toBe('exact_quote')
  })
})

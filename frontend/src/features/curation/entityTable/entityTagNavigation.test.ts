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
})

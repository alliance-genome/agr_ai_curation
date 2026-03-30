import { renderHook, act } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useEntityTagState } from './useEntityTagState'
import type { EntityTag } from './types'

const makeTags = (): EntityTag[] => [
  {
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
      sentence_text: 'daf-2 regulates lifespan.',
      page_number: 3,
      section_title: 'Results',
      chunk_ids: ['c1'],
    },
    notes: null,
  },
  {
    tag_id: 'tag-2',
    entity_name: 'ins-1',
    entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239',
    topic: 'gene expression',
    db_status: 'ambiguous',
    db_entity_id: null,
    source: 'ai',
    decision: 'pending',
    evidence: {
      sentence_text: 'ins-1 is an insulin peptide.',
      page_number: 5,
      section_title: 'Discussion',
      chunk_ids: ['c2'],
    },
    notes: null,
  },
]

describe('useEntityTagState', () => {
  it('reflects the externally selected tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags(), 'tag-1'))

    expect(result.current.selectedTagId).toBe('tag-1')
    expect(result.current.selectedTag?.entity_name).toBe('daf-2')
  })

  it('tracks pending and validated-pending counts from workspace tags', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags(), null))

    expect(result.current.pendingCount).toBe(2)
    expect(result.current.validatedPendingCount).toBe(1)
  })

  it('starts editing an existing tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags(), null))

    act(() => result.current.startEditing('tag-2'))

    expect(result.current.editingTagId).toBe('tag-2')
  })

  it('adds a blank manual tag in edit mode', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags(), null))

    act(() => result.current.addManualTag())

    expect(result.current.tags).toHaveLength(3)
    expect(result.current.manualTag?.source).toBe('manual')
    expect(result.current.manualTag?.entity_name).toBe('')
    expect(result.current.manualTag?.entity_type).toBe('')
    expect(result.current.selectedTagId).toBe(result.current.manualTag?.tag_id)
    expect(result.current.editingTagId).toBe(result.current.manualTag?.tag_id)
  })

  it('removes an unsaved manual tag when editing is cancelled', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags(), null))

    act(() => result.current.addManualTag())
    act(() => result.current.cancelEditing())

    expect(result.current.tags).toHaveLength(2)
    expect(result.current.manualTag).toBeNull()
    expect(result.current.selectedTagId).toBeNull()
    expect(result.current.editingTagId).toBeNull()
  })

  it('returns to the external selection when a workspace row is selected after manual add', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags(), 'tag-1'))

    act(() => result.current.addManualTag())
    act(() => result.current.selectTag())

    expect(result.current.selectedTagId).toBe('tag-1')
    expect(result.current.selectedTag?.entity_name).toBe('daf-2')
  })
})

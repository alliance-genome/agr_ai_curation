import { renderHook, act } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useEntityTagState } from './useEntityTagState'
import type { EntityTag } from './types'

const makeTags = (): EntityTag[] => [
  {
    tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
    db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'daf-2 regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
    notes: null,
  },
  {
    tag_id: 'tag-2', entity_name: 'ins-1', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'ambiguous',
    db_entity_id: null, source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'ins-1 is an insulin peptide.', page_number: 5, section_title: 'Discussion', chunk_ids: ['c2'] },
    notes: null,
  },
]

describe('useEntityTagState', () => {
  it('initializes with tags and no selection', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    expect(result.current.tags).toHaveLength(2)
    expect(result.current.selectedTagId).toBeNull()
    expect(result.current.editingTagId).toBeNull()
  })

  it('selects a tag by id', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.selectTag('tag-1'))
    expect(result.current.selectedTagId).toBe('tag-1')
    expect(result.current.selectedTag?.entity_name).toBe('daf-2')
  })

  it('accepts a pending tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.acceptTag('tag-1'))
    const tag = result.current.tags.find(t => t.tag_id === 'tag-1')
    expect(tag?.decision).toBe('accepted')
  })

  it('rejects a pending tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.rejectTag('tag-2'))
    const tag = result.current.tags.find(t => t.tag_id === 'tag-2')
    expect(tag?.decision).toBe('rejected')
  })

  it('accepts all validated tags', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.acceptAllValidated())
    const tag1 = result.current.tags.find(t => t.tag_id === 'tag-1')
    const tag2 = result.current.tags.find(t => t.tag_id === 'tag-2')
    expect(tag1?.decision).toBe('accepted')  // validated + pending
    expect(tag2?.decision).toBe('pending')   // ambiguous, not auto-accepted
  })

  it('enters and exits edit mode', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.startEditing('tag-1'))
    expect(result.current.editingTagId).toBe('tag-1')
    act(() => result.current.cancelEditing())
    expect(result.current.editingTagId).toBeNull()
  })

  it('updates a tag via saveEdit', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.startEditing('tag-1'))
    act(() => result.current.saveEdit('tag-1', { entity_name: 'daf-2 (edited)', topic: 'phenotype' }))
    const tag = result.current.tags.find(t => t.tag_id === 'tag-1')
    expect(tag?.entity_name).toBe('daf-2 (edited)')
    expect(tag?.topic).toBe('phenotype')
    expect(result.current.editingTagId).toBeNull()
  })

  it('adds a new manual tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.addManualTag())
    expect(result.current.tags).toHaveLength(3)
    const newTag = result.current.tags[2]
    expect(newTag.source).toBe('manual')
    expect(newTag.decision).toBe('pending')
    expect(newTag.entity_name).toBe('')
    expect(result.current.editingTagId).toBe(newTag.tag_id)
  })

  it('returns pending count', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    expect(result.current.pendingCount).toBe(2)
    act(() => result.current.acceptTag('tag-1'))
    expect(result.current.pendingCount).toBe(1)
  })
})

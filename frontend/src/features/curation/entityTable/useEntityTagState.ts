import { useCallback, useMemo, useState } from 'react'
import type { EntityTag } from './types'

let nextManualId = 1

function generateManualTagId(): string {
  return `manual-${Date.now()}-${nextManualId++}`
}

export function useEntityTagState(initialTags: EntityTag[]) {
  const [tags, setTags] = useState<EntityTag[]>(initialTags)
  const [selectedTagId, setSelectedTagId] = useState<string | null>(null)
  const [editingTagId, setEditingTagId] = useState<string | null>(null)

  const selectedTag = useMemo(
    () => tags.find((t) => t.tag_id === selectedTagId) ?? null,
    [tags, selectedTagId],
  )

  const pendingCount = useMemo(
    () => tags.filter((t) => t.decision === 'pending').length,
    [tags],
  )

  const selectTag = useCallback((tagId: string) => {
    setSelectedTagId(tagId)
  }, [])

  const updateTagDecision = useCallback(
    (tagId: string, decision: EntityTag['decision']) => {
      setTags((prev) =>
        prev.map((t) => (t.tag_id === tagId ? { ...t, decision } : t)),
      )
    },
    [],
  )

  const acceptTag = useCallback(
    (tagId: string) => updateTagDecision(tagId, 'accepted'),
    [updateTagDecision],
  )

  const rejectTag = useCallback(
    (tagId: string) => updateTagDecision(tagId, 'rejected'),
    [updateTagDecision],
  )

  const acceptAllValidated = useCallback(() => {
    setTags((prev) =>
      prev.map((t) =>
        t.decision === 'pending' && t.db_status === 'validated'
          ? { ...t, decision: 'accepted' }
          : t,
      ),
    )
  }, [])

  const startEditing = useCallback((tagId: string) => {
    setEditingTagId(tagId)
  }, [])

  const cancelEditing = useCallback(() => {
    setEditingTagId(null)
  }, [])

  const saveEdit = useCallback(
    (tagId: string, updates: Partial<EntityTag>) => {
      setTags((prev) =>
        prev.map((t) => (t.tag_id === tagId ? { ...t, ...updates } : t)),
      )
      setEditingTagId(null)
    },
    [],
  )

  const addManualTag = useCallback(() => {
    const newTag: EntityTag = {
      tag_id: generateManualTagId(),
      entity_name: '',
      entity_type: 'ATP:0000005',
      species: '',
      topic: '',
      db_status: 'not_found',
      db_entity_id: null,
      source: 'manual',
      decision: 'pending',
      evidence: null,
      notes: null,
    }
    setTags((prev) => [...prev, newTag])
    setEditingTagId(newTag.tag_id)
  }, [])

  return {
    tags,
    selectedTagId,
    selectedTag,
    editingTagId,
    pendingCount,
    selectTag,
    acceptTag,
    rejectTag,
    acceptAllValidated,
    startEditing,
    cancelEditing,
    saveEdit,
    addManualTag,
  }
}

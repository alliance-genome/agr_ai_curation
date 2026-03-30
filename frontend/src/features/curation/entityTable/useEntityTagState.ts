import { useCallback, useMemo, useState } from 'react'
import type { EntityTag } from './types'

let nextManualId = 1

function generateManualTagId(): string {
  return `manual-${Date.now()}-${nextManualId++}`
}

export function useEntityTagState(
  tags: EntityTag[],
  externalSelectedTagId: string | null,
) {
  const [editingTagId, setEditingTagId] = useState<string | null>(null)
  const [manualTag, setManualTag] = useState<EntityTag | null>(null)
  const [manualSelectedTagId, setManualSelectedTagId] = useState<string | null>(null)

  const displayTags = useMemo(
    () => (manualTag ? [...tags, manualTag] : tags),
    [manualTag, tags],
  )

  const selectedTagId = manualSelectedTagId ?? externalSelectedTagId

  const selectedTag = useMemo(() => {
    if (!selectedTagId) {
      return null
    }

    return displayTags.find((tag) => tag.tag_id === selectedTagId) ?? null
  }, [displayTags, selectedTagId])

  const pendingCount = useMemo(
    () => displayTags.filter((tag) => tag.decision === 'pending').length,
    [displayTags],
  )

  const validatedPendingCount = useMemo(
    () =>
      displayTags.filter(
        (tag) => tag.decision === 'pending' && tag.db_status === 'validated',
      ).length,
    [displayTags],
  )

  const selectTag = useCallback(() => {
    setManualSelectedTagId(null)
  }, [])

  const startEditing = useCallback((tagId: string) => {
    setEditingTagId(tagId)
  }, [])

  const cancelEditing = useCallback(() => {
    if (manualTag && editingTagId === manualTag.tag_id) {
      setManualTag(null)
      setManualSelectedTagId(null)
    }

    setEditingTagId(null)
  }, [editingTagId, manualTag])

  const addManualTag = useCallback(() => {
    const newTag: EntityTag = {
      tag_id: generateManualTagId(),
      entity_name: '',
      entity_type: '',
      species: '',
      topic: '',
      db_status: 'not_found',
      db_entity_id: null,
      source: 'manual',
      decision: 'pending',
      evidence: null,
      notes: null,
    }

    setManualTag(newTag)
    setManualSelectedTagId(newTag.tag_id)
    setEditingTagId(newTag.tag_id)
  }, [])

  return {
    tags: displayTags,
    selectedTagId,
    selectedTag,
    editingTagId,
    pendingCount,
    validatedPendingCount,
    manualTag,
    selectTag,
    startEditing,
    cancelEditing,
    addManualTag,
  }
}

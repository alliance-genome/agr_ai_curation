import { useCallback, useEffect } from 'react'
import { Box, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material'
import { dispatchPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import { useEntityTagState } from './useEntityTagState'
import { buildEntityTagNavigationCommand } from './entityTagNavigation'
import EntityTagToolbar from './EntityTagToolbar'
import EntityTagRow from './EntityTagRow'
import InlineEditRow from './InlineEditRow'
import EvidencePreviewPane from './EvidencePreviewPane'
import type { EntityTag } from './types'

interface EntityTagTableProps {
  tags: EntityTag[]
  selectedTagId: string | null
  onSelectTag: (tagId: string) => void
  onAcceptTag: (tagId: string) => Promise<void> | void
  onRejectTag: (tagId: string) => Promise<void> | void
  onAcceptAllValidated: (tagIds: string[]) => Promise<void> | void
  onSaveTag: (tagId: string, updates: Partial<EntityTag>) => Promise<void> | void
  onCreateManualTag: (tag: EntityTag) => Promise<string> | string
}

const HEADER_CELLS = ['Entity', 'Type', 'Species', 'Topic', 'DB Status', 'Source', 'Decision']

export default function EntityTagTable({
  tags,
  selectedTagId,
  onSelectTag,
  onAcceptTag,
  onRejectTag,
  onAcceptAllValidated,
  onSaveTag,
  onCreateManualTag,
}: EntityTagTableProps) {
  const state = useEntityTagState(tags, selectedTagId)
  const selectedTag = state.selectedTag
  const selectedTagIdValue = state.selectedTagId

  useEffect(() => {
    if (!selectedTagIdValue || !selectedTag) {
      return
    }

    const command = buildEntityTagNavigationCommand(selectedTag)
    if (command) {
      dispatchPDFViewerNavigateEvidence(command)
    }
  }, [selectedTagIdValue])

  const handleSelect = useCallback((tagId: string) => {
    state.selectTag()
    onSelectTag(tagId)
  }, [onSelectTag, state])

  const handleShowInPdf = useCallback(
    (tag: EntityTag) => {
      const command = buildEntityTagNavigationCommand(tag)
      if (command) dispatchPDFViewerNavigateEvidence(command)
    },
    [],
  )

  const handleAccept = useCallback(async (tagId: string) => {
    try {
      await onAcceptTag(tagId)
    } catch (error) {
      console.error(`Failed to accept entity tag ${tagId}.`, error)
      // Keep the current row state in place when the workspace mutation fails.
    }
  }, [onAcceptTag])

  const handleReject = useCallback(async (tagId: string) => {
    try {
      await onRejectTag(tagId)
    } catch (error) {
      console.error(`Failed to reject entity tag ${tagId}.`, error)
      // Keep the current row state in place when the workspace mutation fails.
    }
  }, [onRejectTag])

  const handleAcceptAllValidated = useCallback(async () => {
    const validatedPendingTagIds = state.tags
      .filter((tag) => tag.decision === 'pending' && tag.db_status === 'validated')
      .map((tag) => tag.tag_id)

    try {
      await onAcceptAllValidated(validatedPendingTagIds)
    } catch (error) {
      console.error('Failed to accept all validated entity tags.', error)
      // Leave the current table state intact and let the page surface the error.
    }
  }, [onAcceptAllValidated, state.tags])

  const handleSave = useCallback(async (tagId: string, updates: Partial<EntityTag>) => {
    try {
      if (state.manualTag && tagId === state.manualTag.tag_id) {
        const createdTagId = await onCreateManualTag({
          ...state.manualTag,
          ...updates,
        })
        state.cancelEditing()
        onSelectTag(createdTagId)
        return
      }

      await onSaveTag(tagId, updates)
      state.cancelEditing()
    } catch (error) {
      console.error(`Failed to save entity tag ${tagId}.`, error)
      // Keep edit mode open when the save fails.
    }
  }, [onCreateManualTag, onSaveTag, onSelectTag, state])

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <EntityTagToolbar
        totalCount={state.tags.length}
        pendingCount={state.pendingCount}
        validatedPendingCount={state.validatedPendingCount}
        onAcceptAllValidated={handleAcceptAllValidated}
        onAddEntity={state.addManualTag}
      />

      <TableContainer sx={{ flex: 1, overflow: 'auto' }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              {HEADER_CELLS.map((label) => (
                <TableCell
                  key={label}
                  sx={{ fontSize: '0.7rem', fontWeight: 600, py: 0.75, px: 1 }}
                >
                  {label}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {state.tags.map((tag) =>
              state.editingTagId === tag.tag_id ? (
                <InlineEditRow
                  key={tag.tag_id}
                  tag={tag}
                  onSave={handleSave}
                  onCancel={state.cancelEditing}
                />
              ) : (
                <EntityTagRow
                  key={tag.tag_id}
                  tag={tag}
                  isSelected={state.selectedTagId === tag.tag_id}
                  onSelect={handleSelect}
                  onAccept={(tagId) => void handleAccept(tagId)}
                  onReject={(tagId) => void handleReject(tagId)}
                  onEdit={state.startEditing}
                />
              ),
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Box sx={{ flex: '0 0 auto', minHeight: 120, borderTop: 1, borderColor: 'divider' }}>
        <EvidencePreviewPane tag={state.selectedTag} onShowInPdf={handleShowInPdf} />
      </Box>
    </Box>
  )
}

import { useCallback, useState } from 'react'
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import type { CurationEvidenceRecord } from '@/features/curation/types'
import { useEntityTagState } from './useEntityTagState'
import EntityTagToolbar from './EntityTagToolbar'
import EntityTagRow from './EntityTagRow'
import InlineEditRow from './InlineEditRow'
import EvidencePreviewPane from './EvidencePreviewPane'
import type { EntityTag } from './types'

interface EntityTagTableProps {
  tags: EntityTag[]
  candidateEvidenceByTagId?: Record<string, CurationEvidenceRecord[]>
  selectedTagId: string | null
  onSelectTag: (tagId: string) => void
  onAcceptTag: (tagId: string) => Promise<void> | void
  onRejectTag: (tagId: string) => Promise<void> | void
  onDeleteTag: (tagId: string) => Promise<void> | void
  onAcceptAllValidated: (tagIds: string[]) => Promise<void> | void
  onSaveTag: (tagId: string, updates: Partial<EntityTag>) => Promise<void> | void
  onCreateManualTag: (tag: EntityTag) => Promise<string> | string
}

const HEADER_CELLS = ['Entity', 'Type', 'Species', 'Topic', 'DB Status', 'Source', 'Decision']

export default function EntityTagTable({
  tags,
  candidateEvidenceByTagId = {},
  selectedTagId,
  onSelectTag,
  onAcceptTag,
  onRejectTag,
  onDeleteTag,
  onAcceptAllValidated,
  onSaveTag,
  onCreateManualTag,
}: EntityTagTableProps) {
  const state = useEntityTagState(tags, selectedTagId)
  const [deleteTargetTag, setDeleteTargetTag] = useState<EntityTag | null>(null)
  const [deletePending, setDeletePending] = useState(false)

  const handleSelect = useCallback((tagId: string) => {
    state.selectTag()
    onSelectTag(tagId)
  }, [onSelectTag, state])

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

  const handleRequestDelete = useCallback((tagId: string) => {
    const targetTag = state.tags.find((tag) => tag.tag_id === tagId) ?? null
    setDeleteTargetTag(targetTag)
  }, [state.tags])

  const handleCloseDeleteDialog = useCallback(() => {
    if (deletePending) {
      return
    }

    setDeleteTargetTag(null)
  }, [deletePending])

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteTargetTag) {
      return
    }

    setDeletePending(true)
    try {
      await onDeleteTag(deleteTargetTag.tag_id)
    } catch (error) {
      console.error(`Failed to delete entity tag ${deleteTargetTag.tag_id}.`, error)
      // Keep the current row state intact and let the page surface the delete error.
    } finally {
      setDeletePending(false)
      setDeleteTargetTag(null)
    }
  }, [deleteTargetTag, onDeleteTag])

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
                  onDelete={handleRequestDelete}
                />
              ),
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Box sx={{ flex: '0 0 auto', minHeight: 120, borderTop: 1, borderColor: 'divider' }}>
        <EvidencePreviewPane
          tag={state.selectedTag}
          evidenceRecords={
            state.selectedTag
              ? (candidateEvidenceByTagId[state.selectedTag.tag_id] ?? [])
              : []
          }
        />
      </Box>

      <Dialog
        open={deleteTargetTag !== null}
        onClose={deletePending ? undefined : handleCloseDeleteDialog}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Delete curation row?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mt: 0.5 }}>
            {deleteTargetTag
              ? `Delete "${deleteTargetTag.entity_name}" from this curation session?`
              : 'Delete this curation row from the current session?'}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
            This permanently removes the candidate, draft, evidence anchors, and validation state
            for this row.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseDeleteDialog} disabled={deletePending}>
            Cancel
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={() => void handleConfirmDelete()}
            disabled={deletePending}
          >
            Delete row
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

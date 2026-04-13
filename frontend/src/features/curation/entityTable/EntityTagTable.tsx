import { useCallback, useEffect, useMemo } from 'react'
import { Box, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material'
import { dispatchPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import type { CurationEvidenceRecord } from '@/features/curation/types'
import { useEntityTagState } from './useEntityTagState'
import { buildEntityTagNavigationCommand } from './entityTagNavigation'
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
  onAcceptAllValidated: (tagIds: string[]) => Promise<void> | void
  onSaveTag: (tagId: string, updates: Partial<EntityTag>) => Promise<void> | void
  onCreateManualTag: (tag: EntityTag) => Promise<string> | string
}

const HEADER_CELLS = ['Entity', 'Type', 'Species', 'Topic', 'DB Status', 'Source', 'Decision']

function primaryEvidenceRecord(
  evidenceRecords: CurationEvidenceRecord[] | undefined,
): CurationEvidenceRecord | null {
  if (!Array.isArray(evidenceRecords) || evidenceRecords.length === 0) {
    return null
  }

  return evidenceRecords.find((record) => record.is_primary) ?? evidenceRecords[0] ?? null
}

function buildNavigationSignature(command: ReturnType<typeof buildEntityTagNavigationCommand>): string | null {
  if (!command) {
    return null
  }

  return JSON.stringify({
    anchorId: command.anchorId,
    locatorQuality: command.anchor.locator_quality,
    searchText: command.searchText,
    pageNumber: command.pageNumber,
    sectionTitle: command.sectionTitle,
    subsectionTitle: command.anchor.subsection_title ?? null,
    chunkIds: command.anchor.chunk_ids ?? [],
  })
}

export default function EntityTagTable({
  tags,
  candidateEvidenceByTagId = {},
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
  const selectedPrimaryEvidence = selectedTag
    ? primaryEvidenceRecord(candidateEvidenceByTagId[selectedTag.tag_id])
    : null
  const selectedNavigationCommand = useMemo(
    () => {
      if (!selectedTagIdValue || !selectedTag) {
        return null
      }

      return buildEntityTagNavigationCommand(
        selectedTag,
        selectedPrimaryEvidence,
      )
    },
    [
      selectedPrimaryEvidence?.anchor_id,
      selectedPrimaryEvidence?.anchor.locator_quality,
      selectedPrimaryEvidence?.anchor.page_number,
      selectedPrimaryEvidence?.anchor.section_title,
      selectedPrimaryEvidence?.anchor.subsection_title,
      selectedPrimaryEvidence?.anchor.sentence_text,
      selectedPrimaryEvidence?.anchor.snippet_text,
      selectedPrimaryEvidence?.anchor.normalized_text,
      selectedPrimaryEvidence?.anchor.viewer_search_text,
      selectedPrimaryEvidence?.anchor.chunk_ids?.join('|'),
      selectedTag?.evidence?.page_number,
      selectedTag?.evidence?.section_title,
      selectedTag?.evidence?.sentence_text,
      selectedTag?.evidence?.chunk_ids?.join('|'),
      selectedTag?.tag_id,
      selectedTagIdValue,
    ],
  )
  const selectedNavigationSignature = useMemo(
    () => buildNavigationSignature(selectedNavigationCommand),
    [selectedNavigationCommand],
  )

  useEffect(() => {
    if (!selectedNavigationCommand) {
      return
    }

    dispatchPDFViewerNavigateEvidence(selectedNavigationCommand)
  }, [
    selectedNavigationCommand,
    selectedNavigationSignature,
  ])

  const handleSelect = useCallback((tagId: string) => {
    state.selectTag()
    onSelectTag(tagId)
  }, [onSelectTag, state])

  const handleShowInPdf = useCallback(
    (tag: EntityTag, evidence?: CurationEvidenceRecord | null) => {
      const command = buildEntityTagNavigationCommand(
        tag,
        evidence ?? primaryEvidenceRecord(candidateEvidenceByTagId[tag.tag_id]),
      )
      if (command) dispatchPDFViewerNavigateEvidence(command)
    },
    [candidateEvidenceByTagId],
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
        <EvidencePreviewPane
          tag={state.selectedTag}
          evidenceRecords={
            state.selectedTag
              ? (candidateEvidenceByTagId[state.selectedTag.tag_id] ?? [])
              : []
          }
          onShowInPdf={handleShowInPdf}
        />
      </Box>
    </Box>
  )
}

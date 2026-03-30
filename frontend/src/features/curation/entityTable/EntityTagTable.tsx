import { useCallback } from 'react'
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
}

const HEADER_CELLS = ['Entity', 'Type', 'Species', 'Topic', 'DB Status', 'Src', 'Decision']

export default function EntityTagTable({ tags: initialTags }: EntityTagTableProps) {
  const state = useEntityTagState(initialTags)

  const handleSelect = useCallback(
    (tagId: string) => {
      state.selectTag(tagId)
      const tag = state.tags.find((t) => t.tag_id === tagId)
      if (tag) {
        const command = buildEntityTagNavigationCommand(tag)
        if (command) dispatchPDFViewerNavigateEvidence(command)
      }
    },
    [state],
  )

  const handleShowInPdf = useCallback(
    (tag: EntityTag) => {
      const command = buildEntityTagNavigationCommand(tag)
      if (command) dispatchPDFViewerNavigateEvidence(command)
    },
    [],
  )

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <EntityTagToolbar
        totalCount={state.tags.length}
        pendingCount={state.pendingCount}
        onAcceptAllValidated={state.acceptAllValidated}
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
                  onSave={state.saveEdit}
                  onCancel={state.cancelEditing}
                />
              ) : (
                <EntityTagRow
                  key={tag.tag_id}
                  tag={tag}
                  isSelected={state.selectedTagId === tag.tag_id}
                  onSelect={handleSelect}
                  onAccept={state.acceptTag}
                  onReject={state.rejectTag}
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

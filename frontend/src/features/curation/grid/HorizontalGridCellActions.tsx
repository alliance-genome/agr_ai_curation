import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import FindInPageOutlinedIcon from '@mui/icons-material/FindInPageOutlined'
import {
  IconButton,
  Stack,
  Tooltip,
} from '@mui/material'

import {
  buildNavigationCommandFromEnvelopeEvidenceProjection,
  type EvidenceNavigationCommand,
} from '@/features/curation/evidence'
import type {
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
} from '@/features/curation/types'
import type { HorizontalGridFieldCell } from './horizontalGridModel'

export interface HorizontalGridCellActionsProps {
  cell: HorizontalGridFieldCell
  field: CurationDraftField | null
  isSaving: boolean
  onEdit: (field: CurationDraftField) => void
  onEvidence: (
    projection: DomainEnvelopeEvidenceAnchorProjection,
    command: EvidenceNavigationCommand,
    anchorEl: HTMLElement,
  ) => void
  onSelect: () => void
}

export default function HorizontalGridCellActions({
  cell,
  field,
  isSaving,
  onEdit,
  onEvidence,
  onSelect,
}: HorizontalGridCellActionsProps) {
  if (!field || !cell.hasField) {
    return null
  }

  const mutationDisabled = field.read_only || isSaving

  return (
    <Stack spacing={0.35}>
      <Stack
        direction="row"
        spacing={0.25}
        sx={(theme) => ({
          alignSelf: 'flex-start',
          p: '2px',
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: '6px',
          backgroundColor: theme.palette.mode === 'light' ? 'rgba(247, 249, 248, 0.94)' : theme.palette.grey[800],
          '& .MuiIconButton-root': {
            color: theme.palette.mode === 'light' ? '#60757a' : theme.palette.text.secondary,
            transition: 'background 140ms ease, box-shadow 140ms ease, color 140ms ease',
          },
          '& .MuiIconButton-root:hover, & .MuiIconButton-root:focus-visible': {
            backgroundColor: theme.palette.background.paper,
            boxShadow: '0 1px 3px rgba(30, 51, 59, 0.13)',
            color: theme.palette.mode === 'light' ? '#176c66' : theme.palette.primary.light,
          },
        })}
      >
        {cell.evidence.map((projection, index) => {
          const command = buildNavigationCommandFromEnvelopeEvidenceProjection(projection)
          const evidenceNumber = index + 1

          return (
            <Tooltip
              key={projection.anchor_id}
              title={command ? `Show evidence ${evidenceNumber}` : 'PDF navigation unavailable'}
            >
              <span>
                <IconButton
                  aria-label={command
                    ? `Show evidence ${evidenceNumber} for ${field.label}`
                    : `Evidence ${evidenceNumber} for ${field.label} has no navigable PDF location`}
                  disabled={!command}
                  onClick={command
                    ? (event) => {
                        onSelect()
                        onEvidence(projection, command, event.currentTarget)
                      }
                    : undefined}
                  size="small"
                  sx={{ borderRadius: '4px', height: 23, width: 23 }}
                >
                  <FindInPageOutlinedIcon sx={{ fontSize: 14 }} />
                </IconButton>
              </span>
            </Tooltip>
          )
        })}
        {!field.read_only ? (
          <>
            {/* Validation is intentionally read-only in this curator preview. Re-enabling
                execution requires a separately reviewed product ticket. */}
            <Tooltip title="Edit field">
              <span>
                <IconButton
                  aria-label={`Edit ${field.label}`}
                  disabled={mutationDisabled}
                  onClick={() => {
                    onSelect()
                    onEdit(field)
                  }}
                  size="small"
                  sx={{ borderRadius: '4px', height: 23, width: 23 }}
                >
                  <EditOutlinedIcon sx={{ fontSize: 14 }} />
                </IconButton>
              </span>
            </Tooltip>
          </>
        ) : null}
      </Stack>
    </Stack>
  )
}

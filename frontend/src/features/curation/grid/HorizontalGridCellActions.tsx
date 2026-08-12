import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined'
import FindInPageOutlinedIcon from '@mui/icons-material/FindInPageOutlined'
import {
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
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
  error: string | null
  field: CurationDraftField | null
  isSaving: boolean
  isValidating: boolean
  onEdit: (field: CurationDraftField) => void
  onEvidence: (
    projection: DomainEnvelopeEvidenceAnchorProjection,
    command: EvidenceNavigationCommand,
  ) => void
  onSelect: () => void
  onValidate: (field: CurationDraftField) => void
}

export default function HorizontalGridCellActions({
  cell,
  error,
  field,
  isSaving,
  isValidating,
  onEdit,
  onEvidence,
  onSelect,
  onValidate,
}: HorizontalGridCellActionsProps) {
  if (!field || !cell.hasField) {
    return null
  }

  const mutationDisabled = field.read_only || isSaving || isValidating

  return (
    <Stack spacing={0.35}>
      <Stack direction="row" spacing={0.25}>
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
                    ? () => {
                        onSelect()
                        onEvidence(projection, command)
                      }
                    : undefined}
                  size="small"
                >
                  <FindInPageOutlinedIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          )
        })}
        {!field.read_only ? (
          <>
            <Tooltip title="Validate field">
              <span>
                <IconButton
                  aria-label={`Validate ${field.label}`}
                  disabled={mutationDisabled}
                  onClick={() => {
                    onSelect()
                    onValidate(field)
                  }}
                  size="small"
                >
                  {isValidating ? (
                    <CircularProgress aria-label={`Validating ${field.label}`} size={18} />
                  ) : (
                    <FactCheckOutlinedIcon fontSize="small" />
                  )}
                </IconButton>
              </span>
            </Tooltip>
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
                >
                  <EditOutlinedIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </>
        ) : null}
      </Stack>
      {error ? (
        <Typography color="error.main" role="alert" variant="caption">
          {error}
        </Typography>
      ) : null}
    </Stack>
  )
}

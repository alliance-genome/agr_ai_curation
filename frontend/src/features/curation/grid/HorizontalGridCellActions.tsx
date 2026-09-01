import CheckOutlinedIcon from '@mui/icons-material/CheckOutlined'
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
import type { FieldStateKind } from '@/features/curation/editor/fieldState'
import type {
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
} from '@/features/curation/types'
import { formatHorizontalGridValue } from './horizontalGridFormatting'
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
  onToggleValidationPreview: (field: CurationDraftField) => void
  previewState: FieldStateKind | null
  recordLabel: string
}

export default function HorizontalGridCellActions({
  cell,
  field,
  isSaving,
  onEdit,
  onEvidence,
  onSelect,
  onToggleValidationPreview,
  previewState,
  recordLabel,
}: HorizontalGridCellActionsProps) {
  if (!field || !cell.hasField) {
    return null
  }

  const mutationDisabled = field.read_only || isSaving
  const hasActions = cell.evidence.length > 0 || previewState !== null || !field.read_only
  const fieldValue = formatHorizontalGridValue(field.value) ?? 'Not available'
  const actionContext = `${field.label}: ${fieldValue} in ${recordLabel}`

  if (!hasActions) {
    return null
  }

  return (
    <Stack
      aria-label={`Actions for ${actionContext}`}
      direction="row"
      role="group"
      spacing={0.25}
      sx={(theme) => ({
        alignSelf: 'flex-start',
        p: '2px',
        border: `1px solid ${theme.palette.mode === 'light' ? '#dce5e3' : theme.palette.divider}`,
        borderRadius: '6px',
        backgroundColor: theme.palette.mode === 'light' ? 'rgba(247, 249, 248, 0.94)' : theme.palette.grey[800],
        '& .MuiIconButton-root:not([data-validation-preview="true"])': {
          color: theme.palette.mode === 'light' ? '#60757a' : theme.palette.text.secondary,
          transition: 'background 140ms ease, box-shadow 140ms ease, color 140ms ease',
        },
        '& .MuiIconButton-root:not([data-validation-preview="true"]):hover, & .MuiIconButton-root:not([data-validation-preview="true"]):focus-visible': {
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
                    ? `Show evidence ${evidenceNumber} for ${actionContext}`
                    : `Evidence ${evidenceNumber} for ${actionContext} has no navigable PDF location`}
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
        {previewState !== null ? (
          <Tooltip
            title={previewState === 'resolved'
              ? 'Curator validated — preview only; click to mark as needing review'
              : 'Not validated — preview only; click to mark as curator validated'}
          >
            <IconButton
              aria-label={previewState === 'resolved'
                ? `Mark as not validated ${actionContext}`
                : `Validate ${actionContext}`}
              aria-pressed={previewState === 'resolved'}
              data-validation-preview="true"
              data-testid={`horizontal-grid-validation-preview-${field.field_key}`}
              onClick={() => {
                onSelect()
                onToggleValidationPreview(field)
              }}
              size="small"
              sx={(theme) => ({
                borderRadius: '4px',
                height: 23,
                width: 23,
                backgroundColor: previewState === 'resolved'
                  ? (theme.palette.mode === 'dark' ? 'rgba(11, 125, 114, 0.28)' : '#dff1ed')
                  : (theme.palette.mode === 'dark' ? 'rgba(200, 136, 45, 0.24)' : '#fff6df'),
                color: previewState === 'resolved'
                  ? (theme.palette.mode === 'dark' ? theme.palette.success.light : '#176c66')
                  : (theme.palette.mode === 'dark' ? theme.palette.warning.light : '#8a5b0d'),
                boxShadow: previewState === 'resolved'
                  ? `inset 0 0 0 1px ${theme.palette.mode === 'dark' ? 'rgba(131, 189, 181, 0.58)' : '#a6d1ca'}`
                  : `inset 0 0 0 1px ${theme.palette.mode === 'dark' ? 'rgba(228, 196, 125, 0.52)' : '#e4c47d'}`,
                '&:hover, &:focus-visible': {
                  backgroundColor: previewState === 'resolved'
                    ? (theme.palette.mode === 'dark' ? 'rgba(11, 125, 114, 0.38)' : '#d1ebe6')
                    : (theme.palette.mode === 'dark' ? 'rgba(200, 136, 45, 0.34)' : '#ffedc1'),
                  color: previewState === 'resolved'
                    ? (theme.palette.mode === 'dark' ? theme.palette.success.light : '#0f5e59')
                    : (theme.palette.mode === 'dark' ? theme.palette.warning.light : '#704509'),
                },
              })}
            >
              <CheckOutlinedIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        ) : null}
        {!field.read_only ? (
          <>
            {/* Validation execution remains intentionally unmounted. The check action above
                changes client-local preview state only and never calls a validator service. */}
            <Tooltip title="Edit field">
              <span>
                <IconButton
                  aria-label={`Edit ${actionContext}`}
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
  )
}

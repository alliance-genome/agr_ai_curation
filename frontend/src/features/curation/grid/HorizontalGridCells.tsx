import FindInPageOutlinedIcon from '@mui/icons-material/FindInPageOutlined'
import { Box, ButtonBase, IconButton, Stack, Tooltip, Typography } from '@mui/material'

import {
  buildNavigationCommandFromEnvelopeEvidenceProjection,
  type EvidenceNavigationCommand,
} from '@/features/curation/evidence'
import type {
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
} from '@/features/curation/types'
import type { FieldStateKind } from '@/features/curation/editor/fieldState'
import type {
  HorizontalGridContextCell,
  HorizontalGridFieldCell,
} from './horizontalGridModel'
import { formatHorizontalGridValue } from './horizontalGridFormatting'

export function contextEvidenceFieldPath(
  projection: DomainEnvelopeEvidenceAnchorProjection,
): string | null {
  const fieldPath = projection.field_path?.trim()
  return fieldPath ? fieldPath : null
}

export function contextEvidenceLabel(
  projection: DomainEnvelopeEvidenceAnchorProjection,
): string {
  const fieldPath = contextEvidenceFieldPath(projection)
  return fieldPath ? `Field evidence (${fieldPath})` : 'Object evidence'
}

export function HorizontalGridContextCellContent({
  active,
  cell,
  onEvidence,
  onSelect,
}: {
  active: boolean
  cell: HorizontalGridContextCell
  onEvidence: (
    projection: DomainEnvelopeEvidenceAnchorProjection,
    command: EvidenceNavigationCommand,
    anchorEl: HTMLElement,
  ) => void
  onSelect: () => void
}) {
  return (
    <Stack
      spacing={0.25}
      sx={{ height: '100%', minWidth: 0, p: '6px 8px 30px 9px', position: 'relative' }}
    >
      <ButtonBase
        aria-label={`Select ${cell.value.identityLabel}`}
        aria-pressed={active}
        data-testid={`horizontal-grid-context-${cell.value.candidateId}`}
        onClick={onSelect}
        sx={{
          alignItems: 'stretch',
          borderRadius: 1,
          display: 'flex',
          justifyContent: 'flex-start',
          minWidth: 0,
          textAlign: 'left',
          width: '100%',
        }}
      >
        <Stack spacing={0.25} minWidth={0} width="100%">
          <Typography fontWeight={750} noWrap variant="body2">
            {cell.value.identityLabel}
          </Typography>
          {cell.value.secondaryLabel ? (
            <Typography color="text.secondary" noWrap variant="caption">
              {cell.value.secondaryLabel}
            </Typography>
          ) : null}
        </Stack>
      </ButtonBase>
      {cell.evidence.length > 0 ? (
        <Stack
          direction="row"
          spacing={0.25}
          sx={(theme) => ({
            bottom: 4,
            left: 7,
            position: 'absolute',
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
            const evidenceLabel = contextEvidenceLabel(projection)
            const fieldPath = contextEvidenceFieldPath(projection)
            const evidenceActionLabel = fieldPath
              ? `field evidence (${fieldPath})`
              : 'object evidence'

            return (
              <Tooltip
                key={projection.anchor_id}
                title={command
                  ? `Show ${evidenceActionLabel} ${evidenceNumber}`
                  : 'PDF navigation unavailable'}
              >
                <span>
                  <IconButton
                    aria-label={command
                      ? `Show ${evidenceActionLabel} ${evidenceNumber} for ${cell.value.identityLabel}`
                      : `${evidenceLabel} ${evidenceNumber} for ${cell.value.identityLabel} has no navigable PDF location`}
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
        </Stack>
      ) : null}
    </Stack>
  )
}

export function HorizontalGridFieldCellContent({
  active,
  cell,
  field,
  onSelect,
  state,
}: {
  active: boolean
  cell: HorizontalGridFieldCell
  field: CurationDraftField | null
  onSelect: () => void
  state: FieldStateKind | null
}) {
  if (!field || !cell.hasField) {
    return (
      <Typography color="text.disabled" fontStyle="italic" variant="body2">
        Not available
      </Typography>
    )
  }

  const value = formatHorizontalGridValue(field.value)
  const validationMessages = cell.validation.summaries.flatMap((summary) => summary.messages)
  const stateLabel = state === 'resolved'
    ? 'Curator validated'
    : state === 'needs-review'
      ? 'Needs review'
      : 'Not validated'

  return (
    <Stack
      minWidth={0}
      spacing={0.35}
      width="100%"
    >
      <ButtonBase
        aria-label={`Select ${field.label} for ${cell.fieldPath}. ${stateLabel}.`}
        aria-pressed={active}
        data-field-key={field.field_key}
        data-testid={`horizontal-grid-field-${field.field_key}`}
        onClick={onSelect}
        sx={{
          alignItems: 'flex-start',
          borderRadius: 1,
          display: 'flex',
          justifyContent: 'flex-start',
          minWidth: 0,
          textAlign: 'left',
          width: '100%',
        }}
      >
        <Stack direction="row" minWidth={0} width="100%">
          <Typography
            aria-label={value === null ? 'Empty value' : undefined}
            data-slot="field-value"
            title={value ?? undefined}
            sx={{
              display: '-webkit-box',
              overflow: 'hidden',
              overflowWrap: 'anywhere',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 1,
              fontSize: 12,
              fontWeight: 670,
              lineHeight: 1.22,
              whiteSpace: 'pre-wrap',
            }}
          >
            {value ?? '—'}
          </Typography>
        </Stack>
      </ButtonBase>
      {validationMessages.length > 0 ? (
        <Tooltip
          arrow
          title={(
            <Stack spacing={0.5}>
              {validationMessages.map((message, index) => (
                <Typography key={`${index}:${message}`} variant="caption">{message}</Typography>
              ))}
            </Stack>
          )}
        >
          <Box
            aria-label={validationMessages.join(' ')}
            component="span"
            data-slot="field-message"
            role="img"
            sx={{ alignSelf: 'flex-start', display: 'inline-flex' }}
            tabIndex={0}
          >
            <Box aria-hidden="true" component="span" data-slot="field-message-icon">!</Box>
            <Box component="span" data-slot="field-message-text">{validationMessages.join(' ')}</Box>
          </Box>
        </Tooltip>
      ) : null}
    </Stack>
  )
}

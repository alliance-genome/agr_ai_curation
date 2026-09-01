import FindInPageOutlinedIcon from '@mui/icons-material/FindInPageOutlined'
import { Box, ButtonBase, Chip, IconButton, Stack, Tooltip, Typography } from '@mui/material'

import { FieldStateIndicator } from '@/features/curation/editor'
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
    <Stack spacing={0.25}>
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
                title={command
                  ? `Show object evidence ${evidenceNumber}`
                  : 'PDF navigation unavailable'}
              >
                <span>
                  <IconButton
                    aria-label={command
                      ? `Show object evidence ${evidenceNumber} for ${cell.value.identityLabel}`
                      : `Object evidence ${evidenceNumber} for ${cell.value.identityLabel} has no navigable PDF location`}
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

  return (
    <Stack
      minWidth={0}
      spacing={0.35}
      width="100%"
    >
      <ButtonBase
        aria-label={`Select ${field.label} for ${cell.fieldPath}`}
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
        <Stack direction="row" minWidth={0} spacing={0.75} width="100%">
          {state ? <FieldStateIndicator fieldKey={field.field_key} state={state} /> : null}
          <Typography
            aria-label={value === null ? 'Empty value' : undefined}
            title={value ?? undefined}
            sx={{
              display: '-webkit-box',
              overflow: 'hidden',
              overflowWrap: 'anywhere',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 2,
              whiteSpace: 'pre-wrap',
            }}
            variant="body2"
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
            aria-label={`${validationMessages.length} validation ${validationMessages.length === 1 ? 'detail' : 'details'} for ${field.label}`}
            component="span"
            sx={{ alignSelf: 'flex-start', borderRadius: 1, display: 'inline-flex' }}
            tabIndex={0}
          >
            <Chip
              label={`${validationMessages.length} ${validationMessages.length === 1 ? 'finding' : 'findings'}`}
              size="small"
              sx={{
                borderColor: state === 'needs-review' ? 'warning.main' : 'divider',
                color: 'text.primary',
                height: 20,
                '& .MuiChip-label': { fontSize: '0.64rem', px: 0.75 },
              }}
              variant="outlined"
            />
          </Box>
        </Tooltip>
      ) : null}
    </Stack>
  )
}

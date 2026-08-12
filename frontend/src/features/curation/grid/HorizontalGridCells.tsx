import FindInPageOutlinedIcon from '@mui/icons-material/FindInPageOutlined'
import { ButtonBase, IconButton, Stack, Tooltip, Typography } from '@mui/material'

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
        <Stack direction="row" spacing={0.25}>
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
        <Stack minWidth={0} spacing={0.2} sx={{ pt: 0.35 }}>
          <Typography
            aria-label={value === null ? 'Empty value' : undefined}
            sx={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}
            variant="body2"
          >
            {value ?? '—'}
          </Typography>
          {validationMessages.map((message, index) => (
            <Typography color="warning.main" key={`${index}:${message}`} variant="caption">
              {message}
            </Typography>
          ))}
        </Stack>
      </Stack>
    </ButtonBase>
  )
}

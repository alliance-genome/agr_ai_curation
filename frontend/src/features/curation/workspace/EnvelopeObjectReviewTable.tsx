import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import HighlightOffIcon from '@mui/icons-material/HighlightOff'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  type ChipProps,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type {
  CurationCandidateStatus,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeReviewRow,
  DomainEnvelopeReviewRowSummaryField,
  DomainEnvelopeValidationStatus,
} from '@/features/curation/types'
import { DOMAIN_ENVELOPE_VALIDATION_STATUSES } from '@/features/curation/types'
import type { WorkspaceEnvelopeObjectReviewRow } from './envelopeObjectReviewRows'

interface EnvelopeObjectReviewTableProps {
  errorMessage?: string | null
  isLoading: boolean
  onAcceptRow: (candidateId: string) => Promise<void> | void
  onRejectRow: (candidateId: string) => Promise<void> | void
  onRetry: () => void
  onSelectRow: (candidateId: string) => void
  rows: WorkspaceEnvelopeObjectReviewRow[]
  selectedCandidateId: string | null
}

const VALIDATION_STATUS_COLOR: Record<DomainEnvelopeValidationStatus, ChipProps['color']> = {
  blocked: 'error',
  planned: 'info',
  resolved: 'success',
  under_development: 'warning',
  unresolved: 'warning',
  waived: 'default',
}

const VALIDATION_STATUS_VALUES = new Set<string>(DOMAIN_ENVELOPE_VALIDATION_STATUSES)

function formatStatusLabel(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

export function formatProjectedSummaryValue(value: unknown): string {
  if (value === null || value === undefined) {
    return 'Empty'
  }

  if (typeof value === 'string') {
    return value || 'Empty'
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  if (Array.isArray(value)) {
    const compactItems = value
      .map((item) => formatProjectedSummaryValue(item))
      .filter((item) => item !== 'Empty')

    return compactItems.length > 0 ? compactItems.join(', ') : 'Empty'
  }

  const serialized = JSON.stringify(value)
  if (typeof serialized !== 'string') {
    throw new TypeError(`Unable to serialize projected summary field value of type ${typeof value}.`)
  }

  return serialized
}

function truncateValue(value: string, maxLength = 96): string {
  if (value.length <= maxLength) {
    return value
  }

  return `${value.slice(0, maxLength - 1)}...`
}

function decisionColor(status: CurationCandidateStatus): ChipProps['color'] {
  if (status === 'accepted') {
    return 'success'
  }
  if (status === 'rejected') {
    return 'default'
  }

  return 'warning'
}

function isDomainEnvelopeValidationStatus(value: unknown): value is DomainEnvelopeValidationStatus {
  return typeof value === 'string' && VALIDATION_STATUS_VALUES.has(value)
}

function formatUnknownValidationState(value: unknown): string {
  if (typeof value !== 'string') {
    return String(value)
  }

  return value.trim() || 'empty value'
}

function validationChipPresentation(row: DomainEnvelopeReviewRow): {
  color: ChipProps['color']
  label: string
} {
  if (!isDomainEnvelopeValidationStatus(row.validation_state)) {
    return {
      color: 'error',
      label: `Unknown validation state: ${formatUnknownValidationState(row.validation_state)}`,
    }
  }

  return {
    color: VALIDATION_STATUS_COLOR[row.validation_state],
    label: formatStatusLabel(row.validation_state),
  }
}

function renderSummaryFields(fields: DomainEnvelopeReviewRowSummaryField[]) {
  if (fields.length === 0) {
    return (
      <Typography color="text.secondary" variant="caption">
        No projected summary fields
      </Typography>
    )
  }

  const visibleFields = fields.slice(0, 4)
  const hiddenCount = fields.length - visibleFields.length

  return (
    <Stack spacing={0.35}>
      {visibleFields.map((field) => (
        <Typography
          key={field.field_path}
          variant="caption"
          sx={{ display: 'block', lineHeight: 1.35 }}
        >
          <Box component="span" sx={{ color: 'text.secondary' }}>
            {field.label}
          </Box>
          {': '}
          {truncateValue(formatProjectedSummaryValue(field.value))}
        </Typography>
      ))}
      {hiddenCount > 0 ? (
        <Typography color="text.secondary" variant="caption">
          {hiddenCount} more projected fields
        </Typography>
      ) : null}
    </Stack>
  )
}

function validationSummaryLabel(row: WorkspaceEnvelopeObjectReviewRow): string {
  if (row.validationSummaries.length === 0) {
    return 'No projected findings'
  }

  const openFindingCount = row.validationSummaries.reduce(
    (total, summary) => total + summary.open_finding_count,
    0,
  )
  const findingCount = row.validationSummaries.reduce(
    (total, summary) => total + summary.finding_count,
    0,
  )

  return `${openFindingCount} open / ${findingCount} findings`
}

function evidenceLabel(anchors: DomainEnvelopeEvidenceAnchorProjection[]): string {
  if (anchors.length === 0) {
    return 'No projected evidence'
  }

  return `${anchors.length} projected evidence anchor${anchors.length === 1 ? '' : 's'}`
}

function reviewRowDisplayLabel(reviewRow: DomainEnvelopeReviewRow): string | null {
  const displayLabel = reviewRow.display_label?.trim()
  return displayLabel && displayLabel.length > 0 ? displayLabel : null
}

function selectedRowLabel(row: WorkspaceEnvelopeObjectReviewRow): string {
  if (!row.reviewRow) {
    return `object ${row.projectionRef.object_id} with missing review row`
  }

  return reviewRowDisplayLabel(row.reviewRow) ?? 'review row with missing display label'
}

function evidenceAnchorText(anchor: DomainEnvelopeEvidenceAnchorProjection): string | null {
  for (const text of [
    anchor.quote,
    anchor.anchor.snippet_text,
    anchor.anchor.sentence_text,
  ]) {
    if (typeof text !== 'string') {
      continue
    }

    const trimmedText = text.trim()
    if (trimmedText.length > 0) {
      return trimmedText
    }
  }

  return null
}

function EnvelopeObjectRow({
  isSelected,
  onAcceptRow,
  onRejectRow,
  onSelectRow,
  row,
}: {
  isSelected: boolean
  onAcceptRow: (candidateId: string) => Promise<void> | void
  onRejectRow: (candidateId: string) => Promise<void> | void
  onSelectRow: (candidateId: string) => void
  row: WorkspaceEnvelopeObjectReviewRow
}) {
  const theme = useTheme()
  const reviewRow = row.reviewRow
  const displayLabel = reviewRow ? reviewRowDisplayLabel(reviewRow) : null
  const rowLabel = selectedRowLabel(row)
  const decision = row.candidate.status
  const validationChip = reviewRow ? validationChipPresentation(reviewRow) : null

  return (
    <TableRow
      hover
      onClick={() => onSelectRow(row.candidate.candidate_id)}
      selected={isSelected}
      sx={{
        cursor: 'pointer',
        ...(isSelected && {
          borderLeft: `3px solid ${theme.palette.primary.main}`,
        }),
        ...(decision === 'accepted' && {
          backgroundColor: alpha(theme.palette.success.main, 0.06),
        }),
        ...(decision === 'rejected' && {
          opacity: 0.58,
        }),
      }}
    >
      <TableCell sx={{ minWidth: 250, py: 0.85 }}>
        <Stack spacing={0.4}>
          {reviewRow ? (
            displayLabel ? (
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {displayLabel}
              </Typography>
            ) : (
              <Typography color="warning.main" variant="body2" sx={{ fontWeight: 600 }}>
                Display label missing
              </Typography>
            )
          ) : (
            <Typography color="warning.main" variant="body2" sx={{ fontWeight: 600 }}>
              Review row missing
            </Typography>
          )}
          {reviewRow?.secondary_label ? (
            <Typography color="text.secondary" variant="caption">
              {reviewRow.secondary_label}
            </Typography>
          ) : null}
          <Typography color="text.secondary" variant="caption">
            Object {row.projectionRef.object_id}
          </Typography>
          <Typography color="text.secondary" variant="caption">
            Envelope {row.projectionRef.envelope_id} r{row.projectionRef.envelope_revision}
          </Typography>
        </Stack>
      </TableCell>

      <TableCell sx={{ minWidth: 210, py: 0.85 }}>
        {reviewRow ? (
          <Stack spacing={0.45}>
            <Typography variant="body2">{reviewRow.object_type}</Typography>
            <Typography color="text.secondary" variant="caption">
              {reviewRow.object_role || 'No object role'}
            </Typography>
            <Typography color="text.secondary" variant="caption">
              {reviewRow.domain_pack_version
                ? `${reviewRow.domain_pack_id}@${reviewRow.domain_pack_version}`
                : reviewRow.domain_pack_id}
            </Typography>
          </Stack>
        ) : (
          <Alert severity="warning" sx={{ py: 0 }}>
            Review row missing for this envelope object.
          </Alert>
        )}
      </TableCell>

      <TableCell sx={{ minWidth: 170, py: 0.85 }}>
        <Stack spacing={0.5} alignItems="flex-start">
          <Chip
            color={decisionColor(decision)}
            label={formatStatusLabel(decision)}
            size="small"
            variant="outlined"
          />
          {reviewRow ? (
            <>
              <Chip
                color="default"
                label={formatStatusLabel(reviewRow.status)}
                size="small"
                variant="outlined"
              />
              {validationChip ? (
                <Chip
                  color={validationChip.color}
                  label={validationChip.label}
                  size="small"
                  variant="outlined"
                />
              ) : null}
            </>
          ) : null}
        </Stack>
      </TableCell>

      <TableCell sx={{ minWidth: 260, py: 0.85 }}>
        {reviewRow ? (
          <Stack spacing={0.35}>
            <Typography variant="caption">
              <Box component="span" sx={{ color: 'text.secondary' }}>
                Type
              </Box>
              {`: ${reviewRow.projection_type}`}
            </Typography>
            <Typography variant="caption">
              <Box component="span" sx={{ color: 'text.secondary' }}>
                Key
              </Box>
              {`: ${reviewRow.projection_key}`}
            </Typography>
            {reviewRow.schema_provider ? (
              <Typography variant="caption">
                <Box component="span" sx={{ color: 'text.secondary' }}>
                  Schema
                </Box>
                {`: ${reviewRow.schema_provider}`}
              </Typography>
            ) : null}
          </Stack>
        ) : (
          <Typography color="text.secondary" variant="caption">
            Projection metadata unavailable
          </Typography>
        )}
      </TableCell>

      <TableCell sx={{ minWidth: 300, py: 0.85 }}>
        {reviewRow ? renderSummaryFields(reviewRow.summary_fields) : (
          <Typography color="text.secondary" variant="caption">
            Summary fields unavailable
          </Typography>
        )}
      </TableCell>

      <TableCell sx={{ minWidth: 210, py: 0.85 }}>
        <Stack spacing={0.35}>
          <Typography variant="caption">{validationSummaryLabel(row)}</Typography>
          <Typography color="text.secondary" variant="caption">
            {evidenceLabel(row.evidenceAnchors)}
          </Typography>
        </Stack>
      </TableCell>

      <TableCell
        onClick={(event) => event.stopPropagation()}
        sx={{ minWidth: 190, py: 0.85 }}
      >
        {decision === 'pending' ? (
          <Stack direction="row" spacing={0.75}>
            <Tooltip title={`Accept ${rowLabel}`}>
              <Button
                color="success"
                onClick={() => void onAcceptRow(row.candidate.candidate_id)}
                size="small"
                startIcon={<CheckCircleOutlineIcon sx={{ fontSize: 16 }} />}
                variant="outlined"
              >
                Accept
              </Button>
            </Tooltip>
            <Tooltip title={`Reject ${rowLabel}`}>
              <Button
                color="error"
                onClick={() => void onRejectRow(row.candidate.candidate_id)}
                size="small"
                startIcon={<HighlightOffIcon sx={{ fontSize: 16 }} />}
                variant="outlined"
              >
                Reject
              </Button>
            </Tooltip>
          </Stack>
        ) : (
          <Typography
            variant="caption"
            sx={{
              color: decision === 'accepted' ? 'success.main' : 'text.secondary',
              fontWeight: 600,
            }}
          >
            {formatStatusLabel(decision)}
          </Typography>
        )}
      </TableCell>
    </TableRow>
  )
}

function EvidenceProjectionPanel({
  row,
}: {
  row: WorkspaceEnvelopeObjectReviewRow | null
}) {
  if (!row) {
    return (
      <Box sx={{ p: 1.5 }}>
        <Typography color="text.secondary" variant="body2">
          No object row selected.
        </Typography>
      </Box>
    )
  }

  return (
    <Box sx={{ p: 1.5 }}>
      <Stack spacing={1}>
        <Stack spacing={0.25}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {selectedRowLabel(row)}
          </Typography>
          <Typography color="text.secondary" variant="caption">
            {row.projectionRef.envelope_id} / {row.projectionRef.object_id} / r{row.projectionRef.envelope_revision}
          </Typography>
        </Stack>

        {row.evidenceAnchors.length === 0 ? (
          <Typography color="text.secondary" variant="body2">
            No projected evidence anchors.
          </Typography>
        ) : (
          <Stack spacing={0.75}>
            {row.evidenceAnchors.slice(0, 5).map((anchor) => {
              const projectedText = evidenceAnchorText(anchor)

              return (
                <Box key={anchor.anchor_id}>
                  {projectedText ? (
                    <Typography variant="body2">
                      {projectedText}
                    </Typography>
                  ) : (
                    <Typography color="text.secondary" variant="body2" sx={{ fontStyle: 'italic' }}>
                      No evidence text projected.
                    </Typography>
                  )}
                  <Typography color="text.secondary" variant="caption">
                    {[
                      anchor.field_path,
                      anchor.page_label || (anchor.page_number ? `page ${anchor.page_number}` : null),
                      anchor.section_title,
                    ].filter(Boolean).join(' / ') || anchor.anchor_id}
                  </Typography>
                </Box>
              )
            })}
          </Stack>
        )}
      </Stack>
    </Box>
  )
}

export default function EnvelopeObjectReviewTable({
  errorMessage,
  isLoading,
  onAcceptRow,
  onRejectRow,
  onRetry,
  onSelectRow,
  rows,
  selectedCandidateId,
}: EnvelopeObjectReviewTableProps) {
  const selectedRow = rows.find((row) => row.candidate.candidate_id === selectedCandidateId)
    ?? rows[0]
    ?? null

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        spacing={1}
        alignItems={{ xs: 'stretch', md: 'center' }}
        justifyContent="space-between"
        sx={{ borderBottom: 1, borderColor: 'divider', px: 1.5, py: 1 }}
      >
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            Envelope objects
          </Typography>
          <Chip label={`${rows.length} rows`} size="small" variant="outlined" />
          {isLoading ? (
            <Chip
              icon={<CircularProgress size={12} />}
              label="Loading projections"
              size="small"
              variant="outlined"
            />
          ) : null}
        </Stack>
        {errorMessage ? (
          <Button onClick={onRetry} size="small" variant="outlined">
            Retry
          </Button>
        ) : null}
      </Stack>

      {errorMessage ? (
        <Box sx={{ p: 1.5 }}>
          <Alert severity="error">{errorMessage}</Alert>
        </Box>
      ) : null}

      <TableContainer sx={{ flex: 1, overflow: 'auto' }}>
        <Table size="small" stickyHeader sx={{ minWidth: 1600 }}>
          <TableHead>
            <TableRow>
              {[
                'Object',
                'Domain',
                'Status',
                'Projection',
                'Projected fields',
                'Validation',
                'Decision',
              ].map((label) => (
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
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} sx={{ py: 5 }}>
                  <Stack spacing={1} alignItems="center">
                    {isLoading ? <CircularProgress size={22} /> : null}
                    <Typography color="text.secondary" variant="body2">
                      {isLoading
                        ? 'Loading envelope object rows...'
                        : 'No envelope object rows are available for this workspace.'}
                    </Typography>
                  </Stack>
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => (
                <EnvelopeObjectRow
                  isSelected={row.candidate.candidate_id === selectedCandidateId}
                  key={`${row.projectionRef.envelope_id}:${row.projectionRef.object_id}:${row.candidate.candidate_id}`}
                  onAcceptRow={onAcceptRow}
                  onRejectRow={onRejectRow}
                  onSelectRow={onSelectRow}
                  row={row}
                />
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Box sx={{ flex: '0 0 auto', minHeight: 120, borderTop: 1, borderColor: 'divider' }}>
        <EvidenceProjectionPanel row={selectedRow} />
      </Box>
    </Box>
  )
}

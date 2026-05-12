import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import HighlightOffIcon from '@mui/icons-material/HighlightOff'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  Tooltip,
  Typography,
  type ChipProps,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import { EvidenceNavigationQuoteCard } from '@/features/curation/evidence'
import { buildQuoteCentricEvidenceNavigationCommand } from '@/features/curation/evidence/navigationCommandBuilder'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence/types'
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
const TECHNICAL_SUMMARY_FIELD_PATTERNS = [
  /^association_kind$/,
  /^evidence_record_ids(?:\[|$)/,
  /^metadata_refs(?:\[|$)/,
]
const TECHNICAL_SUMMARY_LABEL_PATTERNS = [
  /^association kind$/i,
  /evidence record id/i,
]

function formatStatusLabel(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

function formatReadableIdentifier(value: string): string {
  const readable = value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  if (!readable) {
    return value
  }

  return `${readable.charAt(0).toUpperCase()}${readable.slice(1)}`
}

function formatObjectType(value?: string | null): string {
  return value ? formatReadableIdentifier(value) : 'Curation object'
}

function objectTypeTokens(value?: string | null): Set<string> {
  if (!value) {
    return new Set()
  }

  return new Set(
    formatReadableIdentifier(value)
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean),
  )
}

function conciseObjectId(objectId: string, objectType?: string | null): string {
  const rawTokens = objectId
    .split(/[._-]+/)
    .map((token) => token.trim())
    .filter(Boolean)
  const typeTokens = objectTypeTokens(objectType)
  const genericLeadingTokens = new Set([
    'annotation',
    'association',
    'curatable',
    'evidence',
    'object',
    'pending',
    'reference',
  ])
  let firstMeaningfulIndex = 0

  while (
    firstMeaningfulIndex < rawTokens.length - 1 &&
    (
      typeTokens.has(rawTokens[firstMeaningfulIndex].toLowerCase()) ||
      genericLeadingTokens.has(rawTokens[firstMeaningfulIndex].toLowerCase())
    )
  ) {
    firstMeaningfulIndex += 1
  }

  return formatReadableIdentifier(rawTokens.slice(firstMeaningfulIndex).join(' ') || objectId)
}

function isTechnicalDisplayLabel(value: string): boolean {
  const trimmedValue = value.trim()
  return /^[a-z0-9_.:-]+$/.test(trimmedValue) && /[_:.]/.test(trimmedValue)
}

function isEmptyProjectedValue(value: unknown): boolean {
  if (value === null || value === undefined) {
    return true
  }

  if (typeof value === 'string') {
    return value.trim().length === 0
  }

  if (Array.isArray(value)) {
    return value.length === 0
  }

  return false
}

function isTechnicalSummaryField(field: DomainEnvelopeReviewRowSummaryField): boolean {
  return TECHNICAL_SUMMARY_FIELD_PATTERNS.some((pattern) => pattern.test(field.field_path)) ||
    TECHNICAL_SUMMARY_LABEL_PATTERNS.some((pattern) => pattern.test(field.label))
}

function curatorSummaryFields(row: DomainEnvelopeReviewRow): DomainEnvelopeReviewRowSummaryField[] {
  return row.summary_fields.filter((field) => !isTechnicalSummaryField(field))
}

function rowNeedsCuratorReview(row: DomainEnvelopeReviewRow): boolean {
  return curatorSummaryFields(row).some((field) => isEmptyProjectedValue(field.value))
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

function validationChipPresentation(row: DomainEnvelopeReviewRow): {
  color: ChipProps['color']
  label: string
} {
  if (rowNeedsCuratorReview(row)) {
    return {
      color: 'warning',
      label: 'Needs review',
    }
  }

  if (row.validation_state === 'clear') {
    return {
      color: 'success',
      label: 'No issues found',
    }
  }

  if (!row.validation_state) {
    return {
      color: 'default',
      label: 'Not checked',
    }
  }

  if (!isDomainEnvelopeValidationStatus(row.validation_state)) {
    return {
      color: 'default',
      label: formatStatusLabel(String(row.validation_state)),
    }
  }

  return {
    color: VALIDATION_STATUS_COLOR[row.validation_state],
    label: formatStatusLabel(row.validation_state),
  }
}

function renderSummaryFields(row: DomainEnvelopeReviewRow) {
  const fields = curatorSummaryFields(row)

  if (fields.length === 0) {
    return (
      <Typography color="text.secondary" variant="caption">
        No curator-facing fields
      </Typography>
    )
  }

  const visibleFields = fields.slice(0, 3)
  const hiddenCount = fields.length - visibleFields.length

  return (
    <Stack spacing={0.45}>
      {visibleFields.map((field) => (
        <Box
          key={field.field_path}
          sx={{
            alignItems: 'baseline',
            display: 'grid',
            gap: 0.75,
            gridTemplateColumns: 'minmax(104px, 0.42fr) minmax(0, 1fr)',
          }}
        >
          <Typography color="text.secondary" variant="caption">
            {field.label}
          </Typography>
          <Typography
            color={isEmptyProjectedValue(field.value) ? 'warning.main' : 'text.primary'}
            variant="caption"
            sx={{ fontWeight: isEmptyProjectedValue(field.value) ? 700 : 500 }}
          >
            {truncateValue(formatProjectedSummaryValue(field.value), 72)}
          </Typography>
        </Box>
      ))}
      {hiddenCount > 0 ? (
        <Typography color="text.secondary" variant="caption">
          {hiddenCount} more fields in the editor
        </Typography>
      ) : null}
    </Stack>
  )
}

function validationSummaryLabel(row: WorkspaceEnvelopeObjectReviewRow): string {
  if (row.validationSummaries.length === 0) {
    return 'No validation findings'
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
    return 'No evidence'
  }

  return `${anchors.length} evidence quote${anchors.length === 1 ? '' : 's'}`
}

function reviewRowDisplayLabel(reviewRow: DomainEnvelopeReviewRow): string | null {
  const displayLabel = reviewRow.display_label?.trim()
  return displayLabel && displayLabel.length > 0 ? displayLabel : null
}

function reviewRowTitle(row: WorkspaceEnvelopeObjectReviewRow): string {
  const reviewRow = row.reviewRow
  const displayLabel = reviewRow ? reviewRowDisplayLabel(reviewRow) : null

  if (displayLabel && !isTechnicalDisplayLabel(displayLabel)) {
    return displayLabel
  }

  return conciseObjectId(row.projectionRef.object_id, reviewRow?.object_type)
}

function selectedRowLabel(row: WorkspaceEnvelopeObjectReviewRow): string {
  if (!row.reviewRow) {
    return reviewRowTitle(row)
  }

  return reviewRowTitle(row)
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

function evidenceProjectionCommand(
  projection: DomainEnvelopeEvidenceAnchorProjection,
): EvidenceNavigationCommand | null {
  const quote = evidenceAnchorText(projection)
  const pageNumber = projection.page_number ?? projection.anchor.page_number ?? null
  const sectionTitle = projection.section_title ?? projection.anchor.section_title ?? null
  const subsectionTitle = projection.subsection_title ?? projection.anchor.subsection_title ?? null
  const anchor = {
    ...projection.anchor,
    page_number: pageNumber,
    section_title: sectionTitle,
    subsection_title: subsectionTitle,
    chunk_ids: projection.chunk_ids.length > 0 ? projection.chunk_ids : projection.anchor.chunk_ids,
  }

  if (quote) {
    return buildQuoteCentricEvidenceNavigationCommand({
      anchorId: projection.anchor_id,
      anchor,
      quote,
      pageNumber,
      sectionTitle,
      mode: 'select',
    })
  }

  if (
    anchor.viewer_search_text ||
    pageNumber !== null ||
    sectionTitle !== null ||
    anchor.locator_quality === 'document_only'
  ) {
    return {
      anchorId: projection.anchor_id,
      anchor,
      searchText: anchor.viewer_search_text ?? null,
      pageNumber,
      sectionTitle,
      mode: 'select',
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
  const rowLabel = selectedRowLabel(row)
  const decision = row.candidate.status
  const validationChip = reviewRow ? validationChipPresentation(reviewRow) : null

  return (
    <Box
      aria-current={isSelected ? 'true' : undefined}
      data-testid={`envelope-object-review-row-${row.candidate.candidate_id}`}
      onClick={() => onSelectRow(row.candidate.candidate_id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelectRow(row.candidate.candidate_id)
        }
      }}
      role="button"
      sx={{
        border: `1px solid ${isSelected ? alpha(theme.palette.primary.main, 0.72) : alpha(theme.palette.divider, 0.82)}`,
        borderRadius: 1,
        backgroundColor: isSelected
          ? alpha(theme.palette.primary.main, 0.11)
          : alpha(theme.palette.background.paper, 0.72),
        boxShadow: isSelected
          ? `inset 3px 0 0 ${theme.palette.primary.main}`
          : `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.03)}`,
        cursor: 'pointer',
        display: 'grid',
        gap: 1,
        gridTemplateColumns: { xs: '1fr', lg: 'minmax(170px, 0.72fr) minmax(220px, 1fr) auto' },
        p: 1,
        transition: 'border-color 160ms ease, background-color 160ms ease, transform 160ms ease',
        '&:hover': {
          borderColor: alpha(theme.palette.primary.main, 0.56),
          backgroundColor: alpha(theme.palette.primary.main, isSelected ? 0.13 : 0.055),
        },
        '&:focus-visible': {
          outline: `2px solid ${theme.palette.primary.main}`,
          outlineOffset: 2,
        },
        '&:active': {
          transform: 'translateY(1px)',
        },
        ...(decision === 'accepted' && {
          backgroundColor: alpha(theme.palette.success.main, 0.06),
        }),
        ...(decision === 'rejected' && {
          opacity: 0.58,
        }),
      }}
      tabIndex={0}
    >
      <Stack spacing={0.45} sx={{ minWidth: 0 }}>
        <Typography
          variant="body2"
          sx={{
            fontWeight: 700,
            lineHeight: 1.25,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {rowLabel}
        </Typography>
        {reviewRow ? (
          <>
            <Typography color="text.secondary" variant="caption">
              {formatObjectType(reviewRow.object_type)}
            </Typography>
            {reviewRow.object_role ? (
              <Typography color="text.secondary" variant="caption">
                {formatReadableIdentifier(reviewRow.object_role)}
              </Typography>
            ) : null}
          </>
        ) : (
          <Typography color="warning.main" variant="caption">
            Review row unavailable
          </Typography>
        )}
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          <Chip
            color={decisionColor(decision)}
            label={formatStatusLabel(decision)}
            size="small"
            variant="outlined"
          />
          {reviewRow?.secondary_label ? (
            <Chip label={reviewRow.secondary_label} size="small" variant="outlined" />
          ) : null}
        </Stack>
      </Stack>

      <Stack spacing={0.75} sx={{ minWidth: 0 }}>
        {reviewRow ? (
          renderSummaryFields(reviewRow)
        ) : (
          <Typography color="text.secondary" variant="caption">
            Summary unavailable
          </Typography>
        )}
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {validationChip ? (
            <Chip
              color={validationChip.color}
              label={validationChip.label}
              size="small"
              variant="outlined"
            />
          ) : null}
          <Chip label={validationSummaryLabel(row)} size="small" variant="outlined" />
          <Chip label={evidenceLabel(row.evidenceAnchors)} size="small" variant="outlined" />
        </Stack>
      </Stack>

      <Box
        onClick={(event) => event.stopPropagation()}
        sx={{
          alignItems: { xs: 'flex-start', lg: 'center' },
          display: 'flex',
          justifyContent: { xs: 'flex-start', lg: 'flex-end' },
        }}
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
      </Box>
    </Box>
  )
}

function EvidenceProjectionPanel({
  row,
}: {
  row: WorkspaceEnvelopeObjectReviewRow | null
}) {
  const theme = useTheme()

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
      <Stack spacing={1.25}>
        <Stack spacing={0.25}>
          <Typography color="text.secondary" variant="caption">
            Evidence for selected object
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 700 }}>
            {selectedRowLabel(row)}
          </Typography>
        </Stack>

        {row.evidenceAnchors.length === 0 ? (
          <Typography color="text.secondary" variant="body2">
            No evidence quote is linked to this object.
          </Typography>
        ) : (
          <Stack spacing={0.75}>
            {row.evidenceAnchors.slice(0, 5).map((anchor) => {
              const projectedText = evidenceAnchorText(anchor)
              const command = evidenceProjectionCommand(anchor)
              const footerText = [
                anchor.page_label || (anchor.page_number ? `page ${anchor.page_number}` : null),
                anchor.section_title,
              ].filter(Boolean).join(' / ')

              if (projectedText && command) {
                return (
                  <EvidenceNavigationQuoteCard
                    accentColor={theme.palette.primary.main}
                    appearance="workspace"
                    ariaLabel={`Highlight evidence on PDF: ${projectedText}`}
                    command={command}
                    debugContext={{
                      source: 'curation-envelope-object-review',
                      anchorId: anchor.anchor_id,
                      objectId: anchor.object_id,
                      fieldPath: anchor.field_path ?? null,
                    }}
                    footerText={footerText || 'Click to highlight this passage in the PDF'}
                    key={anchor.anchor_id}
                    quote={projectedText}
                  />
                )
              }

              return (
                <Box
                  key={anchor.anchor_id}
                  sx={{
                    border: `1px solid ${theme.palette.divider}`,
                    borderRadius: 1,
                    p: 1,
                  }}
                >
                  <Typography color="text.secondary" variant="body2" sx={{ fontStyle: 'italic' }}>
                    No evidence text is available for this anchor.
                  </Typography>
                  {command ? (
                    <Typography color="text.secondary" variant="caption">
                      A page or section locator is available, but there is no quote text.
                    </Typography>
                  ) : null}
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
          <Typography variant="body2" sx={{ fontWeight: 700 }}>
            Objects to review
          </Typography>
          <Chip label={`${rows.length} object${rows.length === 1 ? '' : 's'}`} size="small" variant="outlined" />
          {isLoading ? (
            <Chip
              icon={<CircularProgress size={12} />}
              label="Loading"
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

      <Stack
        divider={<Divider flexItem />}
        spacing={1}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          p: 1,
        }}
      >
        {rows.length === 0 ? (
          <Stack spacing={1} alignItems="center" sx={{ py: 5 }}>
            {isLoading ? <CircularProgress size={22} /> : null}
            <Typography color="text.secondary" variant="body2">
              {isLoading
                ? 'Loading curation objects...'
                : 'No curation objects are available for this workspace.'}
            </Typography>
          </Stack>
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
      </Stack>

      <Box sx={{ flex: '0 0 auto', minHeight: 190, borderTop: 1, borderColor: 'divider' }}>
        <EvidenceProjectionPanel row={selectedRow} />
      </Box>
    </Box>
  )
}

import { useMemo, useState } from 'react'

import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded'
import FilterListRoundedIcon from '@mui/icons-material/FilterListRounded'
import KeyboardArrowRightRoundedIcon from '@mui/icons-material/KeyboardArrowRightRounded'
import HighlightOffIcon from '@mui/icons-material/HighlightOff'
import RadioButtonUncheckedRoundedIcon from '@mui/icons-material/RadioButtonUncheckedRounded'
import SearchRoundedIcon from '@mui/icons-material/SearchRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  InputAdornment,
  Stack,
  TextField,
  Tooltip,
  Typography,
  type ChipProps,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import { EvidenceNavigationQuoteCard } from '@/features/curation/evidence'
import { buildQuoteCentricEvidenceNavigationCommand } from '@/features/curation/evidence/navigationCommandBuilder'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence/types'
import {
  unavailableCapabilityMessage,
  unavailableValidatorCapabilities,
} from '@/features/curation/unavailableValidatorCapabilities'
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
  const unavailableCapabilities = unavailableValidatorCapabilities(
    row.metadata.unavailable_validator_capabilities,
  )

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
            sx={{ fontWeight: isEmptyProjectedValue(field.value) ? 600 : 500 }}
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
      {unavailableCapabilities.length > 0 ? (
        <Tooltip
          arrow
          placement="top"
          title={unavailableCapabilities.map(unavailableCapabilityMessage).join('\n')}
        >
          <Typography color="text.secondary" variant="caption">
            {unavailableCapabilities.length === 1
              ? '1 validator capability under development'
              : `${unavailableCapabilities.length} validator capabilities under development`}
          </Typography>
        </Tooltip>
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

function rowSearchText(row: WorkspaceEnvelopeObjectReviewRow): string {
  const reviewRow = row.reviewRow
  const summaryText = reviewRow?.summary_fields
    .map((field) => `${field.label} ${formatProjectedSummaryValue(field.value)}`)
    .join(' ') ?? ''

  return [
    selectedRowLabel(row),
    reviewRow?.display_label,
    reviewRow?.secondary_label,
    reviewRow?.object_type,
    reviewRow?.object_role,
    row.candidate.status,
    summaryText,
  ]
    .filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
    .join(' ')
    .toLowerCase()
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
        border: `1px solid ${isSelected ? alpha(theme.palette.primary.main, 0.82) : alpha(theme.palette.primary.light, 0.14)}`,
        borderRadius: 1,
        backgroundColor: isSelected
          ? alpha(theme.palette.primary.main, 0.12)
          : alpha('#081a2b', 0.74),
        boxShadow: isSelected
          ? `inset 3px 0 0 ${theme.palette.primary.main}, 0 14px 28px ${alpha(theme.palette.common.black, 0.16)}`
          : `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.035)}`,
        cursor: 'pointer',
        display: 'grid',
        gap: 1.25,
        gridTemplateColumns: { xs: '1fr', sm: 'auto minmax(0, 1fr) auto' },
        p: 1.25,
        transition: 'border-color 160ms ease, background-color 160ms ease, transform 160ms ease',
        '&:hover': {
          borderColor: alpha(theme.palette.primary.main, 0.58),
          backgroundColor: alpha(theme.palette.primary.main, isSelected ? 0.14 : 0.07),
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
      <Box
        sx={{
          alignItems: 'center',
          color: isSelected ? theme.palette.primary.main : alpha(theme.palette.common.white, 0.36),
          display: { xs: 'none', sm: 'flex' },
          pt: 0.25,
        }}
      >
        {isSelected ? (
          <CheckCircleRoundedIcon fontSize="small" />
        ) : (
          <RadioButtonUncheckedRoundedIcon fontSize="small" />
        )}
      </Box>

      <Stack spacing={0.9} sx={{ minWidth: 0 }}>
        <Stack spacing={0.25} sx={{ minWidth: 0 }}>
          <Typography
            variant="body2"
            sx={{
              color: alpha(theme.palette.common.white, 0.94),
              fontWeight: 600,
              fontSize: '0.95rem',
              letterSpacing: -0.1,
              lineHeight: 1.3,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {rowLabel}
          </Typography>
          {reviewRow ? (
            <Typography color="text.secondary" variant="caption" sx={{ fontWeight: 500 }}>
              {[
                formatObjectType(reviewRow.object_type),
                reviewRow.object_role ? formatReadableIdentifier(reviewRow.object_role) : null,
              ].filter(Boolean).join(' · ')}
            </Typography>
          ) : (
            <Typography color="warning.main" variant="caption">
              Review row unavailable
            </Typography>
          )}
        </Stack>
        {reviewRow ? (
          renderSummaryFields(reviewRow)
        ) : (
          <Typography color="text.secondary" variant="caption">
            Summary unavailable
          </Typography>
        )}
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {reviewRow?.secondary_label ? (
            <Chip
              label={reviewRow.secondary_label}
              size="small"
              variant="outlined"
              sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 500, px: 0.75 } }}
            />
          ) : null}
          {validationChip ? (
            <Chip
              color={validationChip.color}
              label={validationChip.label}
              size="small"
              variant="outlined"
              icon={(
                <Box
                  component="span"
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    backgroundColor: 'currentColor',
                    ml: 0.65,
                    mr: -0.2,
                  }}
                />
              )}
              sx={{
                borderRadius: 1,
                height: 22,
                '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 600, px: 0.75 },
                '& .MuiChip-icon': { color: 'inherit' },
              }}
            />
          ) : null}
          <Chip
            label={validationSummaryLabel(row)}
            size="small"
            variant="outlined"
            sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 500, px: 0.75 } }}
          />
          <Chip
            label={evidenceLabel(row.evidenceAnchors)}
            size="small"
            variant="outlined"
            sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 500, px: 0.75 } }}
          />
        </Stack>
      </Stack>

      <Box
        onClick={(event) => event.stopPropagation()}
        sx={{
          alignItems: { xs: 'flex-start', sm: 'flex-end' },
          alignSelf: 'stretch',
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
          justifyContent: 'space-between',
        }}
      >
        <Stack direction="row" spacing={0.5} alignItems="center" sx={{ pt: 0.25 }}>
          <Chip
            color={decisionColor(decision)}
            label={formatStatusLabel(decision)}
            size="small"
            variant={decision === 'pending' ? 'outlined' : 'filled'}
            sx={{
              borderRadius: 999,
              height: 22,
              '& .MuiChip-label': {
                fontSize: '0.68rem',
                fontWeight: 600,
                letterSpacing: 0.2,
                px: 0.9,
              },
            }}
          />
          <KeyboardArrowRightRoundedIcon sx={{ color: 'text.secondary', fontSize: 18, opacity: 0.55 }} />
        </Stack>
        {decision === 'pending' ? (
          <Stack direction="row" spacing={0.75}>
            <Tooltip title={`Accept ${rowLabel}`}>
              <Button
                color="success"
                onClick={() => void onAcceptRow(row.candidate.candidate_id)}
                size="small"
                startIcon={<CheckCircleOutlineIcon sx={{ fontSize: 16 }} />}
                variant="outlined"
                sx={{
                  borderRadius: 1,
                  fontSize: '0.74rem',
                  fontWeight: 500,
                  letterSpacing: 0,
                  minHeight: 28,
                  px: 1.25,
                  textTransform: 'none',
                }}
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
                sx={{
                  borderRadius: 1,
                  fontSize: '0.74rem',
                  fontWeight: 500,
                  letterSpacing: 0,
                  minHeight: 28,
                  px: 1.25,
                  textTransform: 'none',
                }}
              >
                Reject
              </Button>
            </Tooltip>
          </Stack>
        ) : null}
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
        <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
          <Typography variant="body2" sx={{ color: alpha(theme.palette.common.white, 0.94), fontWeight: 600, letterSpacing: -0.1 }}>
            Evidence & context
          </Typography>
          <Chip
            label={evidenceLabel(row.evidenceAnchors)}
            size="small"
            variant="outlined"
            sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 500, px: 0.75 } }}
          />
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
  const theme = useTheme()
  const [searchQuery, setSearchQuery] = useState('')
  const [pendingOnly, setPendingOnly] = useState(false)
  const displayedRows = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase()

    return rows.filter((row) => {
      if (pendingOnly && row.candidate.status !== 'pending') {
        return false
      }

      if (!normalizedQuery) {
        return true
      }

      return rowSearchText(row).includes(normalizedQuery)
    })
  }, [pendingOnly, rows, searchQuery])
  const selectedRow = rows.find((row) => row.candidate.candidate_id === selectedCandidateId)
    ?? rows[0]
    ?? null
  const objectCountLabel = `${displayedRows.length} of ${rows.length} object${rows.length === 1 ? '' : 's'}`

  return (
    <Box
      sx={{
        background:
          `linear-gradient(180deg, ${alpha(theme.palette.primary.main, 0.04)}, transparent 42%)`,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <Stack
        spacing={1.25}
        justifyContent="space-between"
        sx={{
          borderBottom: `1px solid ${alpha(theme.palette.primary.light, 0.16)}`,
          px: 1.5,
          py: 1.25,
        }}
      >
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          justifyContent="space-between"
          sx={{ minWidth: 0 }}
        >
          <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
            <Typography variant="body2" sx={{ color: alpha(theme.palette.common.white, 0.94), fontWeight: 600, letterSpacing: -0.1 }}>
              Objects to review
            </Typography>
            <Chip
              label={objectCountLabel}
              size="small"
              variant="outlined"
              sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 500, px: 0.75 } }}
            />
            {isLoading ? (
              <Chip
                icon={<CircularProgress size={12} />}
                label="Loading"
                size="small"
                variant="outlined"
                sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 500, px: 0.75 } }}
              />
            ) : null}
          </Stack>
          {errorMessage ? (
            <Button onClick={onRetry} size="small" variant="outlined" sx={{ borderRadius: 1, fontWeight: 500, textTransform: 'none' }}>
              Retry
            </Button>
          ) : null}
        </Stack>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
          <TextField
            fullWidth
            inputProps={{ 'aria-label': 'Search curation objects' }}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search objects..."
            size="small"
            value={searchQuery}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchRoundedIcon sx={{ color: 'text.secondary', fontSize: 18 }} />
                </InputAdornment>
              ),
            }}
            sx={{
              '& .MuiOutlinedInput-root': {
                backgroundColor: alpha('#020915', 0.52),
                borderRadius: 1,
                '& fieldset': {
                  borderColor: alpha(theme.palette.common.white, 0.12),
                },
                '&:hover fieldset': {
                  borderColor: alpha(theme.palette.primary.light, 0.36),
                },
                '&.Mui-focused fieldset': {
                  borderColor: theme.palette.primary.main,
                },
              },
              '& .MuiInputBase-input': {
                fontSize: '0.82rem',
              },
            }}
          />
          <Button
            aria-pressed={pendingOnly}
            onClick={() => setPendingOnly((currentValue) => !currentValue)}
            size="small"
            startIcon={<FilterListRoundedIcon sx={{ fontSize: 18 }} />}
            variant={pendingOnly ? 'contained' : 'outlined'}
            sx={{
              borderRadius: 1,
              flex: { xs: '1 1 auto', sm: '0 0 auto' },
              fontWeight: 500,
              letterSpacing: 0,
              minHeight: 40,
              px: 1.5,
              textTransform: 'none',
              whiteSpace: 'nowrap',
            }}
          >
            Filter
          </Button>
        </Stack>
      </Stack>

      {errorMessage ? (
        <Box sx={{ p: 1.5 }}>
          <Alert severity="error">{errorMessage}</Alert>
        </Box>
      ) : null}

      <Stack
        spacing={1}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          p: 1.25,
        }}
      >
        {displayedRows.length === 0 ? (
          <Stack spacing={1} alignItems="center" sx={{ py: 5 }}>
            {isLoading ? <CircularProgress size={22} /> : null}
            <Typography color="text.secondary" variant="body2">
              {isLoading
                ? 'Loading curation objects...'
                : rows.length === 0
                  ? 'No curation objects are available for this workspace.'
                  : 'No curation objects match the current filters.'}
            </Typography>
          </Stack>
        ) : (
          displayedRows.map((row) => (
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

      <Box
        sx={{
          borderTop: `1px solid ${alpha(theme.palette.primary.light, 0.16)}`,
          flex: '0 0 auto',
          minHeight: 190,
        }}
      >
        <EvidenceProjectionPanel row={selectedRow} />
      </Box>
    </Box>
  )
}

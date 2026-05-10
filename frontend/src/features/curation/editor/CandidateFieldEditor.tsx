import { useMemo } from 'react'

import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  Divider,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import type { ChipProps } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import {
  buildEvidenceLocationLabel,
  dispatchEvidenceNavigationCommand,
} from '@/features/curation/evidence'
import type {
  CurationActionLogEntry,
  CurationCandidate,
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeValidationStatus,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'
import {
  useCurationWorkspaceAutosave,
  useCurationWorkspaceContext,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import { resolveEnvelopeFieldPath } from '@/features/curation/workspace/workspaceState'
import FieldRow from './FieldRow'

interface FieldSection {
  key: string
  label: string
  order: number
  fields: CurationDraftField[]
}

interface StatusPresentation {
  label: string
  color: ChipProps['color']
  severity: 'error' | 'warning' | 'info' | 'success'
}

const STATUS_RANK: Record<DomainEnvelopeValidationStatus, number> = {
  resolved: 0,
  waived: 0,
  planned: 1,
  under_development: 2,
  unresolved: 3,
  blocked: 4,
}

const STATUS_PRESENTATION: Record<DomainEnvelopeValidationStatus, StatusPresentation> = {
  unresolved: {
    label: 'Unresolved',
    color: 'warning',
    severity: 'warning',
  },
  planned: {
    label: 'Planned',
    color: 'info',
    severity: 'info',
  },
  blocked: {
    label: 'Blocked',
    color: 'error',
    severity: 'error',
  },
  under_development: {
    label: 'Under development',
    color: 'secondary',
    severity: 'info',
  },
  resolved: {
    label: 'Validated',
    color: 'success',
    severity: 'success',
  },
  waived: {
    label: 'Opt out',
    color: 'default',
    severity: 'info',
  },
}

const METADATA_KEYS_TO_SKIP = new Set([
  'options',
  'placeholder',
  'source_field_path',
  'widget',
])

function humanizeKey(value?: string | null): string {
  if (!value) {
    return ''
  }

  return value
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase())
}

function groupLabel(field: CurationDraftField): string {
  const label = field.group_label?.trim()
  if (label) {
    return label
  }

  return humanizeKey(field.group_key) || 'Ungrouped fields'
}

function buildSections(fields: CurationDraftField[]): FieldSection[] {
  const sections = new Map<string, FieldSection>()

  for (const field of fields) {
    const key = field.group_key ?? '__ungrouped__'
    const existingSection = sections.get(key)

    if (!existingSection) {
      sections.set(key, {
        key,
        label: groupLabel(field),
        order: field.order,
        fields: [field],
      })
      continue
    }

    existingSection.fields.push(field)
    existingSection.order = Math.min(existingSection.order, field.order)
  }

  return [...sections.values()].sort(
    (left, right) =>
      left.order - right.order ||
      left.label.localeCompare(right.label) ||
      left.key.localeCompare(right.key),
  )
}

function fieldPathCandidates(field: CurationDraftField): Set<string> {
  return new Set([field.field_key, resolveEnvelopeFieldPath(field)])
}

function isProjectionForCandidate(
  candidate: CurationCandidate,
  objectId?: string | null,
): boolean {
  return !objectId || objectId === candidate.projection_ref?.object_id
}

function validationSummariesForField(
  candidate: CurationCandidate,
  field: CurationDraftField,
): DomainEnvelopeValidationSummaryProjection[] {
  const paths = fieldPathCandidates(field)

  return (candidate.validation_summary_projections ?? []).filter((summary) =>
    Boolean(summary.field_path) &&
    paths.has(summary.field_path ?? '') &&
    isProjectionForCandidate(candidate, summary.object_id))
}

function objectValidationSummaries(
  candidate: CurationCandidate | null,
): DomainEnvelopeValidationSummaryProjection[] {
  if (!candidate) {
    return []
  }

  return (candidate.validation_summary_projections ?? []).filter((summary) =>
    !summary.field_path && isProjectionForCandidate(candidate, summary.object_id))
}

function evidenceProjectionsForField(
  candidate: CurationCandidate,
  field: CurationDraftField,
): DomainEnvelopeEvidenceAnchorProjection[] {
  const paths = fieldPathCandidates(field)

  return (candidate.evidence_anchor_projections ?? []).filter((projection) =>
    Boolean(projection.field_path) &&
    paths.has(projection.field_path ?? '') &&
    projection.object_id === candidate.projection_ref?.object_id)
}

function strongestStatus(
  summaries: DomainEnvelopeValidationSummaryProjection[],
): DomainEnvelopeValidationStatus | null {
  let selectedStatus: DomainEnvelopeValidationStatus | null = null

  for (const summary of summaries) {
    if (
      selectedStatus === null ||
      STATUS_RANK[summary.status] > STATUS_RANK[selectedStatus]
    ) {
      selectedStatus = summary.status
    }
  }

  return selectedStatus
}

function uniqueMessages(summaries: DomainEnvelopeValidationSummaryProjection[]): string[] {
  const messages: string[] = []

  for (const summary of summaries) {
    for (const message of summary.messages) {
      if (message.trim() && !messages.includes(message)) {
        messages.push(message)
      }
    }
    for (const finding of summary.findings) {
      if (finding.message.trim() && !messages.includes(finding.message)) {
        messages.push(finding.message)
      }
    }
  }

  return messages
}

function FieldValidationSlot({
  field,
  summaries,
}: {
  field: CurationDraftField
  summaries: DomainEnvelopeValidationSummaryProjection[]
}) {
  const status = strongestStatus(summaries)
  const presentation = status ? STATUS_PRESENTATION[status] : null
  const messages = uniqueMessages(summaries)

  if (!presentation && !field.stale_validation) {
    return null
  }

  return (
    <Stack
      data-testid={`field-validation-state-${field.field_key}`}
      spacing={0.4}
      sx={{ maxWidth: 280 }}
    >
      {presentation ? (
        <Chip
          color={presentation.color}
          label={presentation.label}
          size="small"
          variant={presentation.color === 'default' ? 'outlined' : 'filled'}
        />
      ) : null}
      {field.stale_validation ? (
        <Typography color="text.secondary" variant="caption">
          Stale after edit
        </Typography>
      ) : null}
      {messages[0] ? (
        <Tooltip
          arrow
          placement="top"
          title={messages.join('\n')}
        >
          <Typography
            color="text.secondary"
            sx={{
              display: '-webkit-box',
              overflow: 'hidden',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 2,
            }}
            variant="caption"
          >
            {messages[0]}
            {messages.length > 1 ? ` +${messages.length - 1}` : ''}
          </Typography>
        </Tooltip>
      ) : null}
    </Stack>
  )
}

function evidenceQuote(projection: DomainEnvelopeEvidenceAnchorProjection): string {
  return projection.quote
    ?? projection.anchor.sentence_text
    ?? projection.anchor.snippet_text
    ?? projection.anchor.normalized_text
    ?? '[missing evidence text]'
}

function FieldEvidenceSlot({
  projections,
}: {
  projections: DomainEnvelopeEvidenceAnchorProjection[]
}) {
  const theme = useTheme()

  if (projections.length === 0) {
    return null
  }

  return (
    <>
      {projections.map((projection, index) => {
        const quote = evidenceQuote(projection)
        const pageNumber = projection.page_number ?? projection.anchor.page_number ?? null
        const sectionTitle = projection.section_title ?? projection.anchor.section_title ?? null
        const subsectionTitle =
          projection.subsection_title ?? projection.anchor.subsection_title ?? null
        const label = buildEvidenceLocationLabel({
          pageNumber,
          sectionTitle,
          subsectionTitle,
        })

        return (
          <Tooltip
            arrow
            key={projection.anchor_id}
            placement="top"
            title={quote}
          >
            <ButtonBase
              aria-label={`Highlight field evidence ${index + 1}: ${quote}`}
              data-testid={`field-evidence-projection-${projection.anchor_id}`}
              onClick={() =>
                dispatchEvidenceNavigationCommand(
                  {
                    anchorId: projection.anchor_id,
                    anchor: projection.anchor,
                    searchText:
                      projection.anchor.viewer_search_text
                      ?? projection.quote
                      ?? projection.anchor.sentence_text
                      ?? projection.anchor.snippet_text
                      ?? null,
                    pageNumber,
                    sectionTitle,
                    mode: 'select',
                  },
                  {
                    source: 'curation-field-editor',
                    fieldPath: projection.field_path,
                    objectId: projection.object_id,
                  },
                )}
              sx={{
                px: 0.9,
                py: 0.35,
                borderRadius: 999,
                border: `1px solid ${alpha(theme.palette.divider, 0.82)}`,
                color: theme.palette.text.secondary,
                fontSize: theme.typography.caption.fontSize,
                fontWeight: 700,
                minHeight: 24,
                '&:hover': {
                  borderColor: alpha(theme.palette.primary.main, 0.72),
                  backgroundColor: alpha(theme.palette.primary.main, 0.1),
                  color: theme.palette.primary.light,
                },
              }}
            >
              {label}
            </ButtonBase>
          </Tooltip>
        )
      })}
    </>
  )
}

function formatUnknown(value: unknown): string {
  if (value === null || value === undefined) {
    return 'empty'
  }

  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value)
  }

  try {
    return JSON.stringify(value)
  } catch {
    return '[unserializable value]'
  }
}

function fieldPatchHistory(
  actionLog: CurationActionLogEntry[],
  candidate: CurationCandidate,
  field: CurationDraftField,
): CurationActionLogEntry[] {
  const projectionRef = candidate.projection_ref
  if (!projectionRef) {
    return []
  }

  const paths = fieldPathCandidates(field)

  return actionLog
    .filter((entry) => {
      if (entry.action_type !== 'envelope_field_patched') {
        return false
      }

      const metadata = entry.metadata
      return metadata.envelope_id === projectionRef.envelope_id &&
        metadata.object_id === projectionRef.object_id &&
        typeof metadata.field_path === 'string' &&
        paths.has(metadata.field_path)
    })
    .sort((left, right) => right.occurred_at.localeCompare(left.occurred_at))
}

function metadataPairs(field: CurationDraftField): string[] {
  const pairs: string[] = []

  for (const [key, value] of Object.entries(field.metadata)) {
    if (METADATA_KEYS_TO_SKIP.has(key)) {
      continue
    }

    if (
      value === null ||
      value === undefined ||
      typeof value === 'string' ||
      typeof value === 'number' ||
      typeof value === 'boolean'
    ) {
      pairs.push(`${humanizeKey(key)}: ${formatUnknown(value)}`)
    }

    if (pairs.length >= 3) {
      break
    }
  }

  return pairs
}

function FieldSupportDetails({
  candidate,
  field,
  history,
}: {
  candidate: CurationCandidate
  field: CurationDraftField
  history: CurationActionLogEntry[]
}) {
  const fieldPath = resolveEnvelopeFieldPath(field)
  const latestHistory = history[0]
  const metadata = latestHistory?.metadata
  const metadataText = metadata
    ? `Last repair: ${formatUnknown(metadata.before)} -> ${formatUnknown(metadata.after)}`
    : null
  const details = [
    candidate.projection_ref ? `Path: ${fieldPath}` : null,
    ...metadataPairs(field),
    metadataText,
  ].filter((detail): detail is string => Boolean(detail))

  if (details.length === 0) {
    return null
  }

  return (
    <Typography
      color="text.secondary"
      data-testid={`field-support-details-${field.field_key}`}
      sx={{ pl: { md: '132px' } }}
      variant="caption"
    >
      {details.join(' · ')}
    </Typography>
  )
}

function ObjectValidationAlerts({
  summaries,
}: {
  summaries: DomainEnvelopeValidationSummaryProjection[]
}) {
  if (summaries.length === 0) {
    return null
  }

  return (
    <Stack spacing={1}>
      {summaries.map((summary) => {
        const presentation = STATUS_PRESENTATION[summary.status]
        const message = uniqueMessages([summary])[0] ?? presentation.label

        return (
          <Alert
            data-testid={`object-validation-state-${summary.status}`}
            key={summary.summary_id}
            severity={presentation.severity}
            variant="outlined"
          >
            <Typography component="span" sx={{ fontWeight: 700 }} variant="body2">
              {presentation.label}
            </Typography>
            <Typography component="span" variant="body2">
              {`: ${message}`}
            </Typography>
          </Alert>
        )
      })}
    </Stack>
  )
}

export default function CandidateFieldEditor() {
  const { activeCandidate, workspace } = useCurationWorkspaceContext()
  const autosave = useCurationWorkspaceAutosave()
  const sections = useMemo(
    () => buildSections(activeCandidate?.draft.fields ?? []),
    [activeCandidate?.draft.fields],
  )
  const objectSummaries = useMemo(
    () => objectValidationSummaries(activeCandidate),
    [activeCandidate],
  )

  if (!activeCandidate) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography color="text.secondary" variant="body2">
          Select a curation row to edit its fields.
        </Typography>
      </Box>
    )
  }

  return (
    <Stack
      data-testid="candidate-field-editor"
      spacing={1.75}
      sx={{
        height: '100%',
        minHeight: 0,
        overflow: 'auto',
        p: 2,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Typography sx={{ flex: '1 1 auto', minWidth: 180 }} variant="subtitle1">
          {activeCandidate.display_label ?? activeCandidate.draft.title ?? 'Selected curation row'}
        </Typography>
        {activeCandidate.projection_ref ? (
          <Chip
            data-testid="field-editor-envelope-revision"
            label={`r${activeCandidate.projection_ref.envelope_revision}`}
            size="small"
            variant="outlined"
          />
        ) : null}
        {autosave.isSaving ? (
          <Chip color="info" label="Saving" size="small" />
        ) : null}
      </Stack>

      <ObjectValidationAlerts summaries={objectSummaries} />

      {sections.map((section) => (
        <Box key={section.key}>
          <Typography color="text.secondary" sx={{ letterSpacing: 0 }} variant="overline">
            {section.label.toUpperCase()}
          </Typography>
          <Stack divider={<Divider flexItem />} spacing={0.25}>
            {section.fields.map((field) => {
              const history = fieldPatchHistory(workspace.action_log, activeCandidate, field)

              return (
                <Stack key={field.field_key} spacing={0.6} sx={{ py: 1 }}>
                  <FieldRow
                    evidenceSlot={(
                      <FieldEvidenceSlot
                        projections={evidenceProjectionsForField(activeCandidate, field)}
                      />
                    )}
                    field={field}
                    onChange={(value) => {
                      autosave.queueFieldChange({
                        field_key: field.field_key,
                        value,
                      })
                    }}
                    revertSlot={field.dirty ? (
                      <Button
                        onClick={() =>
                          autosave.queueFieldChange({
                            field_key: field.field_key,
                            revert_to_seed: true,
                          })}
                        size="small"
                        type="button"
                        variant="text"
                      >
                        Revert
                      </Button>
                    ) : null}
                    validationSlot={(
                      <FieldValidationSlot
                        field={field}
                        summaries={validationSummariesForField(activeCandidate, field)}
                      />
                    )}
                    value={field.value}
                  />
                  <FieldSupportDetails
                    candidate={activeCandidate}
                    field={field}
                    history={history}
                  />
                </Stack>
              )
            })}
          </Stack>
        </Box>
      ))}
    </Stack>
  )
}

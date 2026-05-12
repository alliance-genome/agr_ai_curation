import { useCallback, useEffect, useMemo, useState } from 'react'

import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import HighlightOffIcon from '@mui/icons-material/HighlightOff'
import SaveOutlinedIcon from '@mui/icons-material/SaveOutlined'
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
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
  CurationCandidateDraftUpdateResponse,
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeValidationStatus,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'
import { autosaveCurationCandidateDraft } from '@/features/curation/services/curationWorkspaceService'
import {
  useCurationWorkspaceAutosave,
  useCurationWorkspaceContext,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import {
  mergeSavedDraftIntoWorkspace,
  resolveEnvelopeFieldPath,
} from '@/features/curation/workspace/workspaceState'
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

interface CandidateFieldEditorProps {
  onAcceptCandidate?: (candidateId: string) => Promise<void> | void
  onRejectCandidate?: (candidateId: string) => Promise<void> | void
}

const NOTES_MAX_LENGTH = 500

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
  'projection_key',
  'projection_type',
  'provider_refs',
  'repair',
  'semantic_source',
  'source_field_path',
  'source_of_truth',
  'widget',
])
const TECHNICAL_FIELD_PATH_PATTERNS = [
  /^association_kind$/,
  /^evidence_record_ids(?:\[|$)/,
  /^metadata_refs(?:\[|$)/,
]
const TECHNICAL_FIELD_LABEL_PATTERNS = [
  /^association kind$/i,
  /evidence record id/i,
]

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

function isTechnicalDisplayLabel(value: string): boolean {
  const trimmedValue = value.trim()
  return /^[a-z0-9_.:-]+$/.test(trimmedValue) && /[_:.]/.test(trimmedValue)
}

function humanizeObjectIdentifier(value: string): string {
  const suffixParts = value.split(/(?:association|annotation|object|evidence|reference)[_-]+/i)
  const meaningfulValue = suffixParts.at(-1)?.trim() || value
  return humanizeKey(meaningfulValue)
}

function candidateDisplayTitle(candidate: CurationCandidate): string {
  const explicitTitle = candidate.display_label?.trim()
    || candidate.draft.title?.trim()
    || ''

  if (explicitTitle && !isTechnicalDisplayLabel(explicitTitle)) {
    return explicitTitle
  }

  if (candidate.projection_ref?.object_id) {
    return humanizeObjectIdentifier(candidate.projection_ref.object_id)
  }

  return explicitTitle ? humanizeKey(explicitTitle) : 'Selected curation object'
}

function groupLabel(field: CurationDraftField): string {
  const label = field.group_label?.trim()
  if (label) {
    return label
  }

  return humanizeKey(field.group_key) || 'Fields to review'
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

function isTechnicalCurationField(field: CurationDraftField): boolean {
  const fieldPath = resolveEnvelopeFieldPath(field)

  return TECHNICAL_FIELD_PATH_PATTERNS.some((pattern) => pattern.test(fieldPath)) ||
    TECHNICAL_FIELD_LABEL_PATTERNS.some((pattern) => pattern.test(field.label))
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
  show,
}: {
  candidate: CurationCandidate
  field: CurationDraftField
  history: CurationActionLogEntry[]
  show: boolean
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
    <Box
      data-testid={`field-support-details-${field.field_key}`}
      sx={{
        color: 'text.secondary',
        display: show ? 'block' : 'none',
        mt: 0.25,
      }}
    >
      <Typography
        color="text.secondary"
        sx={{
          display: 'block',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          fontSize: '0.66rem',
          letterSpacing: 0,
          lineHeight: 1.35,
          opacity: 0.72,
          wordBreak: 'break-word',
        }}
        variant="caption"
      >
        {details.join(' · ')}
      </Typography>
    </Box>
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

export default function CandidateFieldEditor({
  onAcceptCandidate,
  onRejectCandidate,
}: CandidateFieldEditorProps = {}) {
  const { activeCandidate, setWorkspace, workspace } = useCurationWorkspaceContext()
  const autosave = useCurationWorkspaceAutosave()
  const [draftNotes, setDraftNotes] = useState('')
  const [draftSaveError, setDraftSaveError] = useState<string | null>(null)
  const [isSavingDraft, setIsSavingDraft] = useState(false)
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false)
  const curatorFields = useMemo(
    () => (activeCandidate?.draft.fields ?? []).filter((field) => !isTechnicalCurationField(field)),
    [activeCandidate?.draft.fields],
  )
  const technicalFields = useMemo(
    () => (activeCandidate?.draft.fields ?? []).filter(isTechnicalCurationField),
    [activeCandidate?.draft.fields],
  )
  const sections = useMemo(
    () => buildSections(curatorFields),
    [curatorFields],
  )
  const technicalSections = useMemo(
    () => buildSections(technicalFields),
    [technicalFields],
  )
  const objectSummaries = useMemo(
    () => objectValidationSummaries(activeCandidate),
    [activeCandidate],
  )
  const persistedDraftNotes = activeCandidate?.draft.notes ?? ''
  const notesDirty = draftNotes !== persistedDraftNotes
  const activeDecisionDisabled =
    !activeCandidate ||
    activeCandidate.status !== 'pending' ||
    isSavingDraft ||
    autosave.isSaving

  useEffect(() => {
    setDraftNotes(activeCandidate?.draft.notes ?? '')
    setDraftSaveError(null)
  }, [activeCandidate?.candidate_id, activeCandidate?.draft.notes])

  const mergeDraftResponse = useCallback(
    (response: CurationCandidateDraftUpdateResponse) => {
      setWorkspace((currentWorkspace) => mergeSavedDraftIntoWorkspace(currentWorkspace, response))
    },
    [setWorkspace],
  )

  const saveCurrentDraft = useCallback(async (): Promise<boolean> => {
    if (!activeCandidate) {
      return false
    }

    setDraftSaveError(null)
    setIsSavingDraft(true)

    try {
      const pendingSaved = await autosave.flush()
      if (!pendingSaved) {
        throw new Error('Unable to save pending field changes.')
      }

      if (notesDirty) {
        const response = await autosaveCurationCandidateDraft({
          session_id: workspace.session.session_id,
          candidate_id: activeCandidate.candidate_id,
          draft_id: activeCandidate.draft.draft_id,
          expected_version: activeCandidate.draft.version,
          notes: draftNotes.trim().length > 0 ? draftNotes : null,
          autosave: false,
        })
        mergeDraftResponse(response)
      }

      return true
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to save this draft.'
      setDraftSaveError(message)
      return false
    } finally {
      setIsSavingDraft(false)
    }
  }, [
    activeCandidate,
    autosave,
    draftNotes,
    mergeDraftResponse,
    notesDirty,
    workspace.session.session_id,
  ])

  const handleAcceptActiveCandidate = useCallback(async () => {
    if (!activeCandidate || !onAcceptCandidate) {
      return
    }

    const saved = await saveCurrentDraft()
    if (saved) {
      await onAcceptCandidate(activeCandidate.candidate_id)
    }
  }, [activeCandidate, onAcceptCandidate, saveCurrentDraft])

  const handleRejectActiveCandidate = useCallback(async () => {
    if (!activeCandidate || !onRejectCandidate) {
      return
    }

    const saved = await saveCurrentDraft()
    if (saved) {
      await onRejectCandidate(activeCandidate.candidate_id)
    }
  }, [activeCandidate, onRejectCandidate, saveCurrentDraft])

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
      spacing={0.75}
      sx={{
        height: '100%',
        minHeight: 0,
        overflow: 'auto',
        px: 1.5,
        py: 1.25,
        background:
          (theme) => `linear-gradient(180deg, ${alpha(theme.palette.primary.main, 0.04)}, transparent 44%)`,
      }}
    >
      <Stack spacing={0.25}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography
            sx={{
              color: (theme) => alpha(theme.palette.common.white, 0.94),
              flex: '1 1 auto',
              fontWeight: 600,
              letterSpacing: -0.1,
              minWidth: 180,
            }}
            variant="subtitle1"
          >
            Editable fields
          </Typography>
          {activeCandidate.projection_ref ? (
            <Chip
              data-testid="field-editor-envelope-revision"
              label={`r${activeCandidate.projection_ref.envelope_revision}`}
              size="small"
              variant="outlined"
              sx={{ borderRadius: 1, height: 22, '& .MuiChip-label': { fontSize: '0.68rem', fontWeight: 500, px: 0.75 } }}
            />
          ) : null}
          {autosave.isSaving || isSavingDraft ? (
            <Chip color="info" label="Saving" size="small" sx={{ borderRadius: 1, height: 22 }} />
          ) : null}
        </Stack>
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
          <Typography
            color="text.secondary"
            sx={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={candidateDisplayTitle(activeCandidate)}
            variant="body2"
          >
            {candidateDisplayTitle(activeCandidate)}
          </Typography>
          <FormControlLabel
            control={(
              <Switch
                checked={showTechnicalDetails}
                onChange={(event) => setShowTechnicalDetails(event.target.checked)}
                size="small"
                inputProps={{ 'aria-label': 'Show technical details' }}
              />
            )}
            label="Show technical details"
            sx={{
              flexShrink: 0,
              ml: 0,
              mr: 0,
              '& .MuiFormControlLabel-label': {
                color: 'text.secondary',
                fontSize: '0.72rem',
                fontWeight: 500,
              },
            }}
          />
        </Stack>
      </Stack>

      {draftSaveError ? (
        <Alert severity="error" variant="outlined">
          {draftSaveError}
        </Alert>
      ) : null}

      <ObjectValidationAlerts summaries={objectSummaries} />

      {sections.length === 0 ? (
        <Alert severity="info" variant="outlined">
          No curator-facing fields are available for this object.
        </Alert>
      ) : null}

      {sections.map((section) => (
        <Box key={section.key}>
          <Stack direction="row" spacing={1.25} alignItems="center" sx={{ mb: 0.5 }}>
            <Typography
              sx={{
                color: (theme) => alpha(theme.palette.common.white, 0.82),
                flexShrink: 0,
                fontSize: '0.78rem',
                fontWeight: 600,
              }}
              variant="body2"
            >
              {section.label}
            </Typography>
            <Box
              sx={(theme) => ({
                backgroundColor: alpha(theme.palette.common.white, 0.07),
                flex: 1,
                height: 1,
              })}
            />
          </Stack>
          <Stack spacing={0.5}>
            {section.fields.map((field) => {
              const history = fieldPatchHistory(workspace.action_log, activeCandidate, field)

              return (
                <Box key={field.field_key}>
                  <FieldRow
                    evidenceSlot={(
                      <FieldEvidenceSlot
                        projections={evidenceProjectionsForField(activeCandidate, field)}
                      />
                    )}
                    field={field}
                    labelSubtitleSlot={(
                      <FieldSupportDetails
                        candidate={activeCandidate}
                        field={field}
                        history={history}
                        show={showTechnicalDetails}
                      />
                    )}
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
                </Box>
              )
            })}
          </Stack>
        </Box>
      ))}

      {technicalSections.length > 0 ? (
        <Box
          component="details"
          sx={{
            border: (theme) => `1px solid ${alpha(theme.palette.common.white, 0.1)}`,
            borderRadius: 1,
            backgroundColor: (theme) => alpha(theme.palette.common.black, 0.12),
            p: 1.25,
            '& summary': {
              cursor: 'pointer',
              fontSize: '0.8rem',
              fontWeight: 700,
              outline: 0,
            },
            '& summary:focus-visible': {
              borderRadius: 0.5,
              boxShadow: (theme) => `0 0 0 2px ${theme.palette.primary.main}`,
            },
          }}
        >
          <Box component="summary">
            Technical fields
          </Box>
          <Typography color="text.secondary" sx={{ display: 'block', mt: 0.75 }} variant="caption">
            Internal routing and evidence identifiers are kept here for debugging.
          </Typography>
          <Stack spacing={0.5} sx={{ mt: 1 }}>
            {technicalSections.flatMap((section) => section.fields).map((field) => {
              const history = fieldPatchHistory(workspace.action_log, activeCandidate, field)

              return (
                <Box key={field.field_key}>
                  <FieldRow
                    labelSubtitleSlot={(
                      <FieldSupportDetails
                        candidate={activeCandidate}
                        field={field}
                        history={history}
                        show={showTechnicalDetails}
                      />
                    )}
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
                </Box>
              )
            })}
          </Stack>
        </Box>
      ) : null}

      <Box>
        <Typography
          sx={{
            color: (theme) => alpha(theme.palette.common.white, 0.82),
            fontSize: '0.78rem',
            fontWeight: 600,
            mb: 0.5,
          }}
          variant="body2"
        >
          Notes <Typography component="span" color="text.secondary" variant="caption">(optional)</Typography>
        </Typography>
        <TextField
          fullWidth
          inputProps={{
            'aria-label': 'Curator notes',
            maxLength: NOTES_MAX_LENGTH,
          }}
          minRows={3}
          multiline
          onChange={(event) => setDraftNotes(event.target.value.slice(0, NOTES_MAX_LENGTH))}
          placeholder="Add a note for this object..."
          size="small"
          value={draftNotes}
          sx={{
            '& .MuiOutlinedInput-root': {
              backgroundColor: 'rgba(2, 9, 21, 0.5)',
              borderRadius: 1,
              color: 'rgba(255, 255, 255, 0.9)',
              '& fieldset': {
                borderColor: 'rgba(255, 255, 255, 0.12)',
              },
              '&:hover fieldset': {
                borderColor: 'rgba(100, 181, 246, 0.38)',
              },
              '&.Mui-focused fieldset': {
                borderColor: '#2196f3',
              },
            },
            '& .MuiInputBase-input': {
              fontSize: '0.82rem',
            },
          }}
        />
        <Typography
          color="text.secondary"
          sx={{
            display: 'block',
            fontVariantNumeric: 'tabular-nums',
            mt: 0.35,
            textAlign: 'right',
          }}
          variant="caption"
        >
          {draftNotes.length} / {NOTES_MAX_LENGTH}
        </Typography>
      </Box>

      <Box
        sx={(theme) => ({
          background:
            `linear-gradient(180deg, ${alpha('#071524', 0.72)}, #071524)`,
          borderTop: `1px solid ${alpha(theme.palette.common.white, 0.1)}`,
          bottom: 0,
          mt: 'auto',
          mx: -1.5,
          pb: 0,
          position: 'sticky',
          px: 1.5,
          pt: 1,
          zIndex: 1,
        })}
      >
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} justifyContent="flex-end">
          <Button
            disabled={isSavingDraft || autosave.isSaving}
            onClick={() => void saveCurrentDraft()}
            size="medium"
            startIcon={<SaveOutlinedIcon />}
            variant="outlined"
            sx={{ borderRadius: 1, fontWeight: 500, letterSpacing: 0, minHeight: 36, px: 1.75, textTransform: 'none' }}
          >
            Save draft
          </Button>
          {onRejectCandidate ? (
            <Button
              color="error"
              disabled={activeDecisionDisabled}
              onClick={() => void handleRejectActiveCandidate()}
              size="medium"
              startIcon={<HighlightOffIcon />}
              variant="outlined"
              sx={{ borderRadius: 1, fontWeight: 500, letterSpacing: 0, minHeight: 36, px: 2, textTransform: 'none' }}
            >
              Reject
            </Button>
          ) : null}
          {onAcceptCandidate ? (
            <Button
              color="success"
              disabled={activeDecisionDisabled}
              onClick={() => void handleAcceptActiveCandidate()}
              size="medium"
              startIcon={<CheckCircleOutlineIcon />}
              variant="outlined"
              sx={{ borderRadius: 1, fontWeight: 500, letterSpacing: 0, minHeight: 36, px: 2, textTransform: 'none' }}
            >
              Accept
            </Button>
          ) : null}
        </Stack>
      </Box>
    </Stack>
  )
}

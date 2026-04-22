import {
  startTransition,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import {
  EVIDENCE_LOCATOR_QUALITIES,
  EVIDENCE_SUPPORTS_DECISIONS,
} from '@/features/curation/contracts'
import { createManualCurationCandidate } from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationCandidate,
  CurationDraft,
  CurationDraftField,
  CurationEvidenceRecord,
  CurationReviewSession,
  CurationWorkspace,
  EvidenceLocatorQuality,
  EvidenceSupportsDecision,
} from '@/features/curation/types'
import { useCurationWorkspaceContext } from '@/features/curation/workspace/CurationWorkspaceContext'
import {
  appendWorkspaceActionLog,
  appendWorkspaceCandidate,
  removeWorkspaceCandidate,
  replaceWorkspaceCandidateById,
  replaceWorkspaceSession,
  updateWorkspaceActiveCandidate,
} from '@/features/curation/workspace/workspaceState'
import { normalizeOptionalText } from '@/lib/normalizeOptionalText'

import FieldRow from './FieldRow'
import { useEditorState } from './useEditorState'

interface ManualAnnotationDialogProps {
  open: boolean
  onClose: () => void
}

interface FieldSection {
  key: string
  label: string
  order: number
  fields: CurationDraftField[]
}

interface ManualTemplateOption {
  key: string
  adapterKey: string
  label: string
  description: string
  source: 'candidate' | 'empty'
  fields: CurationDraftField[]
}

interface ManualEvidenceDraft {
  id: string
  fieldKey: string
  snippetText: string
  pageNumber: string
  sectionTitle: string
  locatorQuality: EvidenceLocatorQuality
  supportsDecision: EvidenceSupportsDecision
  isPrimary: boolean
}

const EMPTY_FIELDS: CurationDraftField[] = []
const DEFAULT_LOCATOR_QUALITY: EvidenceLocatorQuality = 'exact_quote'
const DEFAULT_SUPPORTS_DECISION: EvidenceSupportsDecision = 'supports'

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

function humanizeGroupLabel(groupKey?: string | null): string {
  return humanizeKey(groupKey) || 'Details'
}

function buildSections(fields: CurationDraftField[]): FieldSection[] {
  const sections = new Map<string, FieldSection>()

  for (const field of fields) {
    const key = field.group_key ?? '__ungrouped__'
    const existingSection = sections.get(key)

    if (!existingSection) {
      sections.set(key, {
        key,
        label: field.group_label?.trim() || humanizeGroupLabel(field.group_key),
        order: field.order,
        fields: [field],
      })
      continue
    }

    existingSection.fields.push(field)
    existingSection.order = Math.min(existingSection.order, field.order)
    if (!existingSection.label && field.group_label?.trim()) {
      existingSection.label = field.group_label.trim()
    }
  }

  return [...sections.values()].sort(
    (left, right) =>
      left.order - right.order
      || left.label.localeCompare(right.label)
      || left.key.localeCompare(right.key),
  )
}

function cloneTemplateFields(fields: CurationDraftField[]): CurationDraftField[] {
  return fields.map((field) => ({
    ...field,
    value: null,
    seed_value: null,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: { ...field.metadata },
  }))
}

function parseTemplateFieldOrder(value: unknown, fallbackOrder: number): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? value
    : fallbackOrder
}

function parseTemplateFieldMetadata(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {}
  }

  return { ...(value as Record<string, unknown>) }
}

function createTemplateOptionKey(adapterKey: string): string {
  return adapterKey
}

function buildTemplateLabel(
  session: CurationReviewSession,
  adapterKey: string,
): string {
  return adapterKey === session.adapter.adapter_key
    ? (session.adapter.display_label?.trim() || humanizeKey(adapterKey))
    : humanizeKey(adapterKey)
}

function buildTemplateOptions(
  workspace: CurationWorkspace,
): ManualTemplateOption[] {
  const options = new Map<string, ManualTemplateOption>()
  const sortedCandidates = [...workspace.candidates].sort(
    (left, right) => left.order - right.order,
  )

  for (const candidate of sortedCandidates) {
    const key = createTemplateOptionKey(candidate.adapter_key)
    if (options.has(key)) {
      continue
    }

    const fields = cloneTemplateFields(candidate.draft.fields)
    options.set(key, {
      key,
      adapterKey: candidate.adapter_key,
      label: buildTemplateLabel(
        workspace.session,
        candidate.adapter_key,
      ),
      description:
        `${fields.length} field${fields.length === 1 ? '' : 's'} from the shared editor template`,
      source: 'candidate',
      fields,
    })
  }

  const sessionKey = createTemplateOptionKey(workspace.session.adapter.adapter_key)
  if (!options.has(sessionKey)) {
    options.set(sessionKey, {
      key: sessionKey,
      adapterKey: workspace.session.adapter.adapter_key,
      label: buildTemplateLabel(
        workspace.session,
        workspace.session.adapter.adapter_key,
      ),
      description: 'No shared draft-field template is available yet for this adapter.',
      source: 'empty',
      fields: EMPTY_FIELDS,
    })
  }

  return [...options.values()].sort((left, right) => left.label.localeCompare(right.label))
}

function stringifyFieldValue(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null
  }

  if (
    typeof value === 'string'
    || typeof value === 'number'
    || typeof value === 'boolean'
  ) {
    const normalized = String(value).trim()
    return normalized.length > 0 ? normalized : null
  }

  return null
}

function parseOptionalInteger(value: string): number | null {
  const normalized = value.trim()
  if (!normalized) {
    return null
  }

  const parsed = Number.parseInt(normalized, 10)
  return Number.isFinite(parsed) ? parsed : null
}

function createLocalId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }

  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`
}

function createEmptyEvidenceDraft(fieldKey: string): ManualEvidenceDraft {
  return {
    id: createLocalId('manual-evidence'),
    fieldKey,
    snippetText: '',
    pageNumber: '',
    sectionTitle: '',
    locatorQuality: DEFAULT_LOCATOR_QUALITY,
    supportsDecision: DEFAULT_SUPPORTS_DECISION,
    isPrimary: false,
  }
}

function findField(
  fields: CurationDraftField[],
  fieldKey: string,
): CurationDraftField | null {
  return fields.find((field) => field.field_key === fieldKey) ?? null
}

function buildEvidenceRecords(
  rows: ManualEvidenceDraft[],
  fields: CurationDraftField[],
  candidateId: string,
  timestamp: string,
): CurationEvidenceRecord[] {
  return rows.map((row) => {
    const field = findField(fields, row.fieldKey)

    return {
      anchor_id: createLocalId('manual-anchor'),
      candidate_id: candidateId,
      source: 'manual',
      field_keys: row.fieldKey ? [row.fieldKey] : [],
      field_group_keys: field?.group_key ? [field.group_key] : [],
      is_primary: row.isPrimary,
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: row.locatorQuality,
        supports_decision: row.supportsDecision,
        snippet_text: normalizeOptionalText(row.snippetText),
        sentence_text: normalizeOptionalText(row.snippetText),
        normalized_text: null,
        viewer_search_text:
          normalizeOptionalText(row.snippetText),
        viewer_highlightable: true,
        page_number: parseOptionalInteger(row.pageNumber),
        page_label: null,
        section_title: normalizeOptionalText(row.sectionTitle),
        subsection_title: null,
        figure_reference: null,
        table_reference: null,
        chunk_ids: [],
      },
      created_at: timestamp,
      updated_at: timestamp,
      warnings: [],
    }
  })
}

function applyEvidenceToFields(
  fields: CurationDraftField[],
  evidenceRecords: CurationEvidenceRecord[],
): CurationDraftField[] {
  return fields.map((field) => ({
    ...field,
    seed_value: field.value ?? null,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: evidenceRecords
      .filter((record) => record.field_keys.includes(field.field_key))
      .map((record) => record.anchor_id),
    validation_result: null,
  }))
}

function inferDisplayLabel(
  displayLabel: string,
  fields: CurationDraftField[],
  fallbackIndex: number,
): string {
  const explicitLabel = normalizeOptionalText(displayLabel)
  if (explicitLabel) {
    return explicitLabel
  }

  for (const field of fields) {
    const candidateLabel = stringifyFieldValue(field.value)
    if (candidateLabel) {
      return candidateLabel
    }
  }

  return `Manual candidate ${fallbackIndex}`
}

function validateEvidenceRows(rows: ManualEvidenceDraft[]): string | null {
  const incompleteRow = rows.find(
    (row) => !row.fieldKey || !normalizeOptionalText(row.snippetText),
  )

  if (incompleteRow) {
    return 'Complete or remove each evidence row before creating the manual annotation.'
  }

  return null
}

function buildOptimisticSession(
  session: CurationReviewSession,
  candidateId: string,
  timestamp: string,
): CurationReviewSession {
  return {
    ...session,
    session_version: session.session_version + 1,
    current_candidate_id: candidateId,
    last_worked_at: timestamp,
    progress: {
      ...session.progress,
      total_candidates: session.progress.total_candidates + 1,
      pending_candidates: session.progress.pending_candidates + 1,
      manual_candidates: session.progress.manual_candidates + 1,
    },
  }
}

function buildOptimisticCandidate(args: {
  session: CurationReviewSession
  candidateId: string
  draftId: string
  adapterKey: string
  displayLabel: string
  fields: CurationDraftField[]
  evidenceRecords: CurationEvidenceRecord[]
  timestamp: string
  order: number
}): CurationCandidate {
  const draft: CurationDraft = {
    draft_id: args.draftId,
    candidate_id: args.candidateId,
    adapter_key: args.adapterKey,
    version: 1,
    title: args.displayLabel,
    summary: null,
    fields: args.fields,
    notes: null,
    created_at: args.timestamp,
    updated_at: args.timestamp,
    last_saved_at: args.timestamp,
    metadata: {},
  }

  return {
    candidate_id: args.candidateId,
    session_id: args.session.session_id,
    source: 'manual',
    status: 'pending',
    order: args.order,
    adapter_key: args.adapterKey,
    display_label: args.displayLabel,
    secondary_label: null,
    conversation_summary: null,
    extraction_result_id: null,
    draft,
    evidence_anchors: args.evidenceRecords,
    validation: null,
    evidence_summary: null,
    created_at: args.timestamp,
    updated_at: args.timestamp,
    metadata: {},
  }
}

export default function ManualAnnotationDialog({
  open,
  onClose,
}: ManualAnnotationDialogProps) {
  const {
    activeCandidateId,
    session,
    setActiveCandidate,
    setWorkspace,
    workspace,
  } = useCurationWorkspaceContext()
  const templateOptions = useMemo(() => buildTemplateOptions(workspace), [workspace])
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>('')
  const [displayLabel, setDisplayLabel] = useState('')
  const [evidenceRows, setEvidenceRows] = useState<ManualEvidenceDraft[]>([])
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const wasOpenRef = useRef(false)

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      setSelectedTemplateKey(templateOptions[0]?.key ?? '')
      setDisplayLabel('')
      setEvidenceRows([])
      setError(null)
      setSubmitting(false)
    }

    if (!open && wasOpenRef.current) {
      setSelectedTemplateKey('')
      setDisplayLabel('')
      setEvidenceRows([])
      setError(null)
      setSubmitting(false)
    }

    wasOpenRef.current = open
  }, [open, templateOptions])

  const selectedTemplate = useMemo(
    () => templateOptions.find((option) => option.key === selectedTemplateKey) ?? null,
    [selectedTemplateKey, templateOptions],
  )
  const editorState = useEditorState({
    candidateId: open ? selectedTemplateKey : null,
    fields: selectedTemplate?.fields ?? EMPTY_FIELDS,
  })
  const sections = useMemo(() => buildSections(editorState.fields), [editorState.fields])
  const evidenceCountByFieldKey = useMemo(
    () =>
      evidenceRows.reduce<Record<string, number>>((counts, row) => {
        if (!row.fieldKey) {
          return counts
        }

        return {
          ...counts,
          [row.fieldKey]: (counts[row.fieldKey] ?? 0) + 1,
        }
      }, {}),
    [evidenceRows],
  )

  const fieldOptions = useMemo(
    () => editorState.fields.map((field) => ({
      fieldKey: field.field_key,
      label: field.label,
    })),
    [editorState.fields],
  )

  const handleClose = () => {
    if (submitting) {
      return
    }

    onClose()
  }

  const addEvidenceRow = (fieldKey?: string) => {
    const fallbackFieldKey = fieldKey ?? fieldOptions[0]?.fieldKey ?? ''
    setEvidenceRows((currentRows) => [
      ...currentRows,
      createEmptyEvidenceDraft(fallbackFieldKey),
    ])
  }

  const updateEvidenceRow = (
    rowId: string,
    updates: Partial<ManualEvidenceDraft>,
  ) => {
    setEvidenceRows((currentRows) =>
      currentRows.map((row) =>
        row.id === rowId
          ? { ...row, ...updates }
          : row,
      ),
    )
  }

  const removeEvidenceRow = (rowId: string) => {
    setEvidenceRows((currentRows) => currentRows.filter((row) => row.id !== rowId))
  }

  const hasTemplateFields = editorState.fields.length > 0

  const handleCreate = async () => {
    if (!selectedTemplate || !hasTemplateFields) {
      setError('Select an adapter template with shared editor fields before creating a candidate.')
      return
    }

    const evidenceError = validateEvidenceRows(evidenceRows)
    if (evidenceError) {
      setError(evidenceError)
      return
    }

    setSubmitting(true)
    setError(null)

    const previousSession = workspace.session
    const previousActiveCandidateId = activeCandidateId
    const timestamp = new Date().toISOString()
    const optimisticCandidateId = createLocalId('manual-candidate')
    const optimisticDraftId = createLocalId('manual-draft')
    const evidenceRecords = buildEvidenceRecords(
      evidenceRows,
      editorState.fields,
      optimisticCandidateId,
      timestamp,
    )
    const draftFields = applyEvidenceToFields(editorState.fields, evidenceRecords)
    const nextOrder = workspace.candidates.reduce(
      (highestOrder, candidate) => Math.max(highestOrder, candidate.order),
      -1,
    ) + 1
    const resolvedDisplayLabel = inferDisplayLabel(
      displayLabel,
      draftFields,
      nextOrder + 1,
    )

    const optimisticCandidate = buildOptimisticCandidate({
      session,
      candidateId: optimisticCandidateId,
      draftId: optimisticDraftId,
      adapterKey: selectedTemplate.adapterKey,
      displayLabel: resolvedDisplayLabel,
      fields: draftFields,
      evidenceRecords,
      timestamp,
      order: nextOrder,
    })

    startTransition(() => {
      setWorkspace((currentWorkspace) =>
        updateWorkspaceActiveCandidate(
          appendWorkspaceCandidate(
            replaceWorkspaceSession(
              currentWorkspace,
              buildOptimisticSession(
                currentWorkspace.session,
                optimisticCandidateId,
                timestamp,
              ),
            ),
            optimisticCandidate,
          ),
          optimisticCandidateId,
        ),
      )
      setActiveCandidate(optimisticCandidateId)
    })

    try {
      const response = await createManualCurationCandidate({
        session_id: session.session_id,
        adapter_key: selectedTemplate.adapterKey,
        source: 'manual',
        display_label: resolvedDisplayLabel,
        draft: optimisticCandidate.draft,
        evidence_anchors: evidenceRecords,
      })

      startTransition(() => {
        setWorkspace((currentWorkspace) =>
          updateWorkspaceActiveCandidate(
            appendWorkspaceActionLog(
              replaceWorkspaceCandidateById(
                replaceWorkspaceSession(currentWorkspace, response.session),
                optimisticCandidateId,
                response.candidate,
              ),
              response.action_log_entry,
            ),
            response.candidate.candidate_id,
          ),
        )
        setActiveCandidate(response.candidate.candidate_id)
      })

      onClose()
      return
    } catch (createError) {
      const message = createError instanceof Error
        ? createError.message
        : 'Unable to create the manual annotation.'

      startTransition(() => {
        setWorkspace((currentWorkspace) =>
          updateWorkspaceActiveCandidate(
            removeWorkspaceCandidate(
              replaceWorkspaceSession(currentWorkspace, previousSession),
              optimisticCandidateId,
            ),
            previousActiveCandidateId ?? null,
          ),
        )
        setActiveCandidate(previousActiveCandidateId ?? null)
      })

      setError(message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      fullWidth
      maxWidth="md"
      PaperProps={{
        sx: {
          minHeight: 420,
        },
      }}
    >
      <DialogTitle>Add Manual Annotation</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5}>
          {error ? <Alert severity="error">{error}</Alert> : null}

          <Typography color="text.secondary" variant="body2">
            Create a candidate that was missed by extraction, using the shared draft-field
            primitives for the selected adapter template.
          </Typography>

          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
            <TextField
              data-testid="manual-annotation-template-select"
              fullWidth
              label="Adapter template"
              onChange={(event) => {
                setSelectedTemplateKey(event.target.value)
                setDisplayLabel('')
                setEvidenceRows([])
                setError(null)
              }}
              select
              size="small"
              value={selectedTemplateKey}
            >
              {templateOptions.map((option) => (
                <MenuItem key={option.key} value={option.key}>
                  {option.label}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              data-testid="manual-annotation-display-label"
              fullWidth
              helperText="Optional queue label. Leave blank to derive one from the first populated field."
              label="Annotation label"
              onChange={(event) => setDisplayLabel(event.target.value)}
              size="small"
              value={displayLabel}
            />
          </Stack>

          {selectedTemplate ? (
            <Typography color="text.secondary" variant="caption">
              {selectedTemplate.description}
            </Typography>
          ) : null}

          {!hasTemplateFields ? (
            <Alert severity="warning">
              No shared draft-field template is available for this adapter yet.
            </Alert>
          ) : (
            <Stack spacing={2.25}>
              {sections.map((section) => (
                <Box
                  key={section.key}
                  sx={(theme) => ({
                    borderRadius: 1.5,
                    border: `1px solid ${alpha(theme.palette.divider, 0.8)}`,
                    backgroundColor: alpha(theme.palette.background.paper, 0.44),
                    overflow: 'hidden',
                  })}
                >
                  <Box
                    sx={(theme) => ({
                      px: 1.5,
                      py: 1.25,
                      borderBottom: `1px solid ${alpha(theme.palette.divider, 0.8)}`,
                      backgroundColor: alpha(theme.palette.background.default, 0.34),
                    })}
                  >
                    <Typography color="text.secondary" letterSpacing="0.08em" variant="overline">
                      {section.label.toUpperCase()}
                    </Typography>
                  </Box>

                  <Stack divider={<Divider flexItem />} spacing={0}>
                    {section.fields.map((field) => (
                      <Box key={field.field_key} sx={{ px: 1.5, py: 1.25 }}>
                        <FieldRow
                          evidenceSlot={(
                            <Button
                              onClick={() => addEvidenceRow(field.field_key)}
                              size="small"
                              type="button"
                              variant="text"
                            >
                              Link evidence
                              {evidenceCountByFieldKey[field.field_key]
                                ? ` (${evidenceCountByFieldKey[field.field_key]})`
                                : ''}
                            </Button>
                          )}
                          field={field}
                          onChange={(value) => {
                            editorState.setFieldValue(field.field_key, value)
                            setError(null)
                          }}
                          value={editorState.getField(field.field_key)?.value}
                        />
                      </Box>
                    ))}
                  </Stack>
                </Box>
              ))}
            </Stack>
          )}

          <Stack spacing={1.5}>
            <Stack alignItems="center" direction="row" justifyContent="space-between" spacing={1}>
              <Typography variant="subtitle2">Evidence links</Typography>
              <Button
                data-testid="manual-annotation-add-evidence"
                disabled={!hasTemplateFields}
                onClick={() => addEvidenceRow()}
                size="small"
                type="button"
                variant="outlined"
              >
                Add evidence
              </Button>
            </Stack>

            {evidenceRows.length === 0 ? (
              <Typography color="text.secondary" variant="body2">
                Add evidence rows only when you want the new candidate linked to document support
                immediately.
              </Typography>
            ) : (
              <Stack spacing={1.5}>
                {evidenceRows.map((row, index) => (
                  <Box
                    data-testid={`manual-annotation-evidence-row-${row.id}`}
                    key={row.id}
                    sx={(theme) => ({
                      p: 1.5,
                      borderRadius: 1.5,
                      border: `1px solid ${alpha(theme.palette.divider, 0.8)}`,
                    })}
                  >
                    <Stack spacing={1.25}>
                      <Stack
                        alignItems={{ xs: 'stretch', sm: 'center' }}
                        direction={{ xs: 'column', sm: 'row' }}
                        justifyContent="space-between"
                        spacing={1}
                      >
                        <Typography variant="body2">
                          Evidence row {index + 1}
                        </Typography>
                        <Button
                          color="inherit"
                          onClick={() => removeEvidenceRow(row.id)}
                          size="small"
                          type="button"
                        >
                          Remove
                        </Button>
                      </Stack>

                      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.25}>
                        <TextField
                          fullWidth
                          label="Field"
                          onChange={(event) => updateEvidenceRow(row.id, { fieldKey: event.target.value })}
                          select
                          size="small"
                          value={row.fieldKey}
                        >
                          {fieldOptions.map((option) => (
                            <MenuItem key={option.fieldKey} value={option.fieldKey}>
                              {option.label}
                            </MenuItem>
                          ))}
                        </TextField>

                        <TextField
                          fullWidth
                          label="Page"
                          onChange={(event) => updateEvidenceRow(row.id, { pageNumber: event.target.value })}
                          size="small"
                          type="number"
                          value={row.pageNumber}
                        />
                      </Stack>

                      <TextField
                        fullWidth
                        label="Snippet text"
                        minRows={2}
                        multiline
                        onChange={(event) => updateEvidenceRow(row.id, { snippetText: event.target.value })}
                        size="small"
                        value={row.snippetText}
                      />

                      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.25}>
                        <TextField
                          fullWidth
                          label="Section"
                          onChange={(event) => updateEvidenceRow(row.id, { sectionTitle: event.target.value })}
                          size="small"
                          value={row.sectionTitle}
                        />

                        <TextField
                          fullWidth
                          label="Locator quality"
                          onChange={(event) =>
                            updateEvidenceRow(row.id, {
                              locatorQuality: event.target.value as EvidenceLocatorQuality,
                            })}
                          select
                          size="small"
                          value={row.locatorQuality}
                        >
                          {EVIDENCE_LOCATOR_QUALITIES.map((quality) => (
                            <MenuItem key={quality} value={quality}>
                              {humanizeKey(quality)}
                            </MenuItem>
                          ))}
                        </TextField>

                        <TextField
                          fullWidth
                          label="Decision support"
                          onChange={(event) =>
                            updateEvidenceRow(row.id, {
                              supportsDecision: event.target.value as EvidenceSupportsDecision,
                            })}
                          select
                          size="small"
                          value={row.supportsDecision}
                        >
                          {EVIDENCE_SUPPORTS_DECISIONS.map((decision) => (
                            <MenuItem key={decision} value={decision}>
                              {humanizeKey(decision)}
                            </MenuItem>
                          ))}
                        </TextField>
                      </Stack>

                      <FormControlLabel
                        control={(
                          <Checkbox
                            checked={row.isPrimary}
                            onChange={(event) =>
                              updateEvidenceRow(row.id, { isPrimary: event.target.checked })}
                          />
                        )}
                        label="Primary evidence for this field"
                      />
                    </Stack>
                  </Box>
                ))}
              </Stack>
            )}
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button disabled={submitting} onClick={handleClose}>
          Cancel
        </Button>
        <Button
          data-testid="manual-annotation-create-button"
          disabled={submitting || !hasTemplateFields}
          onClick={() => void handleCreate()}
          variant="contained"
        >
          {submitting ? 'Creating...' : 'Create annotation'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

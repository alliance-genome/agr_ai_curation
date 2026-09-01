import {
  getCurationAdapterEditorPack,
  type CurationAdapterFieldLayoutEntry,
} from '@/features/curation/adapters'
import { fieldState, type FieldStateKind } from '@/features/curation/editor/fieldState'
import { resolveRenderAs } from '@/features/curation/editor/fieldRenderers'
import type {
  CurationCandidate,
  CurationCandidateSource,
  CurationCandidateStatus,
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeProjectionRef,
  DomainEnvelopeReviewRow,
  DomainEnvelopeReviewRowSummaryField,
  DomainEnvelopeValidationStatus,
  DomainEnvelopeValidationSummaryProjection,
  FieldValidationResult,
} from '@/features/curation/types'
import type { WorkspaceEnvelopeObjectReviewRow } from '@/features/curation/workspace/envelopeObjectReviewRows'
import { objectSelectorLabel } from '@/features/curation/workspace/objectSelector'
import { resolveEnvelopeFieldPath } from '@/features/curation/workspace/workspaceState'
import { formatHorizontalGridValue } from './horizontalGridFormatting'

export const HORIZONTAL_GRID_CONTEXT_COLUMN_KEY = 'context'

export interface HorizontalGridColumn {
  key: string
  kind: 'context' | 'field'
  fieldPath: string | null
  label: string
  order: number
  required: boolean
  readOnly: boolean
  groupKey: string | null
  groupLabel: string | null
}

export interface HorizontalGridValidationProjection {
  summaries: DomainEnvelopeValidationSummaryProjection[]
  statuses: DomainEnvelopeValidationStatus[]
  summaryCount: number
  findingCount: number
  openFindingCount: number
}

export interface HorizontalGridRowContext {
  candidateId: string
  objectId: string | null
  envelopeId: string | null
  envelopeRevision: number | null
  objectType: string | null
  objectRole: string | null
  identityLabel: string
  secondaryLabel: string | null
  candidateStatus: CurationCandidateStatus
  candidateSource: CurationCandidateSource
  candidateMetadata: Record<string, unknown>
  summaryFields: DomainEnvelopeReviewRowSummaryField[] | null
  reviewRowMetadata: Record<string, unknown> | null
}

export interface HorizontalGridContextCell {
  columnKey: typeof HORIZONTAL_GRID_CONTEXT_COLUMN_KEY
  value: HorizontalGridRowContext
  evidence: DomainEnvelopeEvidenceAnchorProjection[]
  validation: HorizontalGridValidationProjection
}

export interface HorizontalGridFieldCell {
  columnKey: string
  fieldKey: string | null
  fieldPath: string
  hasField: boolean
  value: unknown
  required: boolean | null
  readOnly: boolean | null
  staleValidation: boolean | null
  state: FieldStateKind | null
  fieldValidation: FieldValidationResult | null
  evidence: DomainEnvelopeEvidenceAnchorProjection[]
  validation: HorizontalGridValidationProjection
  extractorComparison: HorizontalGridExtractorComparison | null
  valueSource: 'canonical' | 'extractor'
}

export interface HorizontalGridExtractorComparison {
  fieldKey: string
  fieldPath: string
  label: string
  value: unknown
  outcome: 'confirmed' | 'different' | 'overridden' | 'unresolved'
}

export interface HorizontalGridRow {
  candidateId: string
  contextCell: HorizontalGridContextCell
  cells: HorizontalGridFieldCell[]
  evidence: DomainEnvelopeEvidenceAnchorProjection[]
  validation: HorizontalGridValidationProjection
  unmappedEvidence: DomainEnvelopeEvidenceAnchorProjection[]
  unmappedValidation: HorizontalGridValidationProjection
}

export interface HorizontalGridModel {
  columns: HorizontalGridColumn[]
  rows: HorizontalGridRow[]
}

export interface BuildHorizontalGridModelInput {
  candidates: readonly CurationCandidate[]
  envelopeReviewRows: readonly WorkspaceEnvelopeObjectReviewRow[]
}

interface HorizontalGridSourceRow {
  candidate: CurationCandidate
  projectionRef: DomainEnvelopeProjectionRef | null
  reviewRow: DomainEnvelopeReviewRow | null
  evidenceAnchors: DomainEnvelopeEvidenceAnchorProjection[]
  validationSummaries: DomainEnvelopeValidationSummaryProjection[]
}

interface FieldOccurrence {
  candidate: CurationCandidate
  field: CurationDraftField
  fieldPath: string
  label: string
  order: number
  groupKey: string | null
  groupLabel: string | null
}

function divergenceTargetPath(field: CurationDraftField): string | null {
  if (resolveRenderAs(field) !== 'divergence') {
    return null
  }

  const fieldPath = resolveEnvelopeFieldPath(field)
  const segments = fieldPath.split('.')
  const fieldName = segments.at(-1)
  if (!fieldName?.startsWith('proposed_')) {
    return null
  }

  segments[segments.length - 1] = fieldName.slice('proposed_'.length)
  return segments.join('.')
}

function divergenceFieldForCanonicalPath(
  candidate: CurationCandidate,
  canonicalPath: string,
): CurationDraftField | null {
  return candidate.draft.fields.find(
    (field) => divergenceTargetPath(field) === canonicalPath,
  ) ?? null
}

function extractorComparison(
  candidate: CurationCandidate,
  canonicalField: CurationDraftField,
  canonicalPath: string,
): HorizontalGridExtractorComparison | null {
  const extractorField = divergenceFieldForCanonicalPath(candidate, canonicalPath)
  const extractorValue = extractorField?.value
  const formattedExtractorValue = formatHorizontalGridValue(extractorValue)
  if (!extractorField || formattedExtractorValue === null) {
    return null
  }

  const formattedCanonicalValue = formatHorizontalGridValue(canonicalField.value)
  return {
    fieldKey: extractorField.field_key,
    fieldPath: resolveEnvelopeFieldPath(extractorField),
    label: extractorField.label,
    value: extractorValue,
    outcome: formattedCanonicalValue === null
      ? 'unresolved'
      : formattedCanonicalValue === formattedExtractorValue
        ? 'confirmed'
        : 'different',
  }
}

function isProjectedAsCanonicalComparison(
  candidate: CurationCandidate,
  field: CurationDraftField,
): boolean {
  const canonicalPath = divergenceTargetPath(field)
  return canonicalPath !== null && candidate.draft.fields.some(
    (candidateField) => resolveEnvelopeFieldPath(candidateField) === canonicalPath,
  )
}

function isCuratorDecisionField(
  candidate: CurationCandidate,
  field: CurationDraftField,
): boolean {
  if (candidate.adapter_key !== 'gene') {
    return true
  }

  // The gene envelope deliberately separates the curator's export/sign-off
  // surface (Gene identity) from evidence locators, provider hints, resolution
  // notes, and confidence. Those supporting values remain in the envelope and
  // evidence projections; they are not peer decisions and must not acquire a
  // validation checkbox merely because the draft transports them to the UI.
  return field.group_key === 'identity'
}

function fieldColumnKey(fieldPath: string): string {
  return `field:${encodeURIComponent(fieldPath)}`
}

function compareStrings(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0
}

function compareNullableStrings(left: string | null, right: string | null): number {
  if (left === right) {
    return 0
  }
  if (left === null) {
    return -1
  }
  if (right === null) {
    return 1
  }
  return compareStrings(left, right)
}

function adapterLayoutForField(
  candidate: CurationCandidate,
  fieldPath: string,
): CurationAdapterFieldLayoutEntry | null {
  const editorPack = getCurationAdapterEditorPack(candidate.adapter_key)
  return editorPack?.fieldLayout.find((entry) => entry.fieldPath === fieldPath) ?? null
}

function fieldOccurrence(
  candidate: CurationCandidate,
  field: CurationDraftField,
): FieldOccurrence {
  const fieldPath = resolveEnvelopeFieldPath(field)
  const adapterLayout = adapterLayoutForField(candidate, fieldPath)

  return {
    candidate,
    field,
    fieldPath,
    label: adapterLayout?.label ?? field.label,
    order: adapterLayout?.order ?? field.order,
    groupKey: adapterLayout?.groupKey ?? field.group_key ?? null,
    groupLabel: adapterLayout?.groupLabel ?? field.group_label ?? null,
  }
}

function compareOccurrences(left: FieldOccurrence, right: FieldOccurrence): number {
  return (
    left.order - right.order
    || compareStrings(left.label, right.label)
    || left.candidate.order - right.candidate.order
    || compareStrings(left.candidate.candidate_id, right.candidate.candidate_id)
    || compareStrings(left.field.field_key, right.field.field_key)
  )
}

function buildFieldColumns(
  rows: readonly HorizontalGridSourceRow[],
): HorizontalGridColumn[] {
  const occurrencesByPath = new Map<string, FieldOccurrence[]>()

  for (const row of rows) {
    for (const field of row.candidate.draft.fields) {
      // Divergence fields preserve what the extractor proposed before validation.
      // When the candidate also has the canonical target, the proposal belongs in
      // that target's Details comparison—not in a peer grid column that could be
      // mistaken for a second authoritative or curator-editable value.
      if (isProjectedAsCanonicalComparison(row.candidate, field)) {
        continue
      }
      if (!isCuratorDecisionField(row.candidate, field)) {
        continue
      }
      const occurrence = fieldOccurrence(row.candidate, field)
      const occurrences = occurrencesByPath.get(occurrence.fieldPath) ?? []
      occurrences.push(occurrence)
      occurrencesByPath.set(occurrence.fieldPath, occurrences)
    }
  }

  return [...occurrencesByPath.entries()]
    .map(([fieldPath, occurrences]) => {
      const orderedOccurrences = [...occurrences].sort(compareOccurrences)
      const representative = orderedOccurrences[0]
      if (!representative) {
        throw new Error(`Horizontal grid field '${fieldPath}' has no field metadata`)
      }

      return {
        key: fieldColumnKey(fieldPath),
        kind: 'field' as const,
        fieldPath,
        label: representative.label,
        order: representative.order,
        required: occurrences.some(({ field }) => field.required),
        readOnly: occurrences.every(({ field }) => field.read_only),
        groupKey: representative.groupKey,
        groupLabel: representative.groupLabel,
      }
    })
    .sort((left, right) => left.order - right.order || compareStrings(left.fieldPath, right.fieldPath))
}

function compareEvidence(
  left: DomainEnvelopeEvidenceAnchorProjection,
  right: DomainEnvelopeEvidenceAnchorProjection,
): number {
  return (
    compareNullableStrings(normalizeFieldPath(left.field_path), normalizeFieldPath(right.field_path))
    || compareStrings(left.anchor_id, right.anchor_id)
  )
}

function compareValidationSummaries(
  left: DomainEnvelopeValidationSummaryProjection,
  right: DomainEnvelopeValidationSummaryProjection,
): number {
  return (
    compareNullableStrings(normalizeFieldPath(left.field_path), normalizeFieldPath(right.field_path))
    || compareStrings(left.summary_id, right.summary_id)
  )
}

function validationProjection(
  summaries: readonly DomainEnvelopeValidationSummaryProjection[],
): HorizontalGridValidationProjection {
  const orderedSummaries = [...summaries].sort(compareValidationSummaries)

  return {
    summaries: orderedSummaries,
    statuses: orderedSummaries.map((summary) => summary.status),
    summaryCount: orderedSummaries.length,
    findingCount: orderedSummaries.reduce((count, summary) => count + summary.finding_count, 0),
    openFindingCount: orderedSummaries.reduce(
      (count, summary) => count + summary.open_finding_count,
      0,
    ),
  }
}

function normalizeFieldPath(fieldPath: string | null | undefined): string | null {
  return fieldPath?.trim() || null
}

function fieldPathMatches(projectionPath: string | null | undefined, fieldPath: string): boolean {
  return normalizeFieldPath(projectionPath) === fieldPath
}

function isObjectLevelProjection(projectionPath: string | null | undefined): boolean {
  return normalizeFieldPath(projectionPath) === null
}

function secondaryLabel(row: HorizontalGridSourceRow): string | null {
  return (
    row.reviewRow?.secondary_label?.trim()
    || row.candidate.secondary_label?.trim()
    || null
  )
}

function compareRows(
  left: HorizontalGridSourceRow,
  right: HorizontalGridSourceRow,
): number {
  return (
    left.candidate.order - right.candidate.order
    || compareStrings(left.candidate.candidate_id, right.candidate.candidate_id)
    || compareNullableStrings(
      left.projectionRef?.object_id ?? null,
      right.projectionRef?.object_id ?? null,
    )
  )
}

function fieldsByCanonicalPath(candidate: CurationCandidate): Map<string, CurationDraftField> {
  const fieldsByPath = new Map<string, CurationDraftField>()

  for (const field of candidate.draft.fields) {
    const fieldPath = resolveEnvelopeFieldPath(field)
    const existingField = fieldsByPath.get(fieldPath)
    if (existingField) {
      throw new Error(
        `Candidate '${candidate.candidate_id}' has multiple draft fields for canonical path `
        + `'${fieldPath}': '${existingField.field_key}' and '${field.field_key}'`,
      )
    }
    fieldsByPath.set(fieldPath, field)
  }

  return fieldsByPath
}

function contextForRow(row: HorizontalGridSourceRow): HorizontalGridRowContext {
  const candidate = row.candidate

  return {
    candidateId: candidate.candidate_id,
    objectId: row.projectionRef?.object_id ?? null,
    envelopeId: row.projectionRef?.envelope_id ?? null,
    envelopeRevision: row.projectionRef?.envelope_revision ?? null,
    objectType: row.reviewRow?.object_type ?? null,
    objectRole: row.reviewRow?.object_role ?? null,
    identityLabel: objectSelectorLabel(row),
    secondaryLabel: secondaryLabel(row),
    candidateStatus: candidate.status,
    candidateSource: candidate.source,
    candidateMetadata: candidate.metadata,
    summaryFields: row.reviewRow ? [...row.reviewRow.summary_fields] : null,
    reviewRowMetadata: row.reviewRow?.metadata ?? null,
  }
}

function projectRow(
  row: HorizontalGridSourceRow,
  fieldColumns: readonly HorizontalGridColumn[],
): HorizontalGridRow {
  const fieldsByPath = fieldsByCanonicalPath(row.candidate)
  const projectedFieldsByPath = new Map(
    [...fieldsByPath.entries()].filter(([, field]) => (
      !isProjectedAsCanonicalComparison(row.candidate, field)
      && isCuratorDecisionField(row.candidate, field)
    )),
  )
  const evidence = [...row.evidenceAnchors].sort(compareEvidence)
  const validationSummaries = [...row.validationSummaries].sort(compareValidationSummaries)
  const columnFieldPaths = new Set(
    fieldColumns.flatMap((column) => column.fieldPath === null ? [] : [column.fieldPath]),
  )
  const context = contextForRow(row)
  const objectValidation = validationSummaries.filter((projection) =>
    isObjectLevelProjection(projection.field_path),
  )
  // Keep evidence reachable without pretending that it supports a different field.
  // Object-level projections and projections without an actionable field cell belong
  // on the row context control, while exact field matches stay on their field cells.
  const contextEvidence = evidence.filter((projection) => (
    isObjectLevelProjection(projection.field_path)
    || !fieldColumns.some((column) => (
      column.fieldPath !== null
      && projectedFieldsByPath.has(column.fieldPath)
      && fieldPathMatches(projection.field_path, column.fieldPath)
    ))
  ))

  const cells = fieldColumns.map((column): HorizontalGridFieldCell => {
    const fieldPath = column.fieldPath
    if (fieldPath === null) {
      throw new Error(`Horizontal grid field column '${column.key}' has no canonical path`)
    }

    const field = projectedFieldsByPath.get(fieldPath) ?? null
    const cellEvidence = evidence.filter((projection) =>
      fieldPathMatches(projection.field_path, fieldPath),
    )
    const cellValidation = validationSummaries.filter((projection) =>
      fieldPathMatches(projection.field_path, fieldPath),
    )

    const baseState = field ? fieldState(field, cellValidation) : null
    const projectedComparison = field ? extractorComparison(row.candidate, field, fieldPath) : null
    const validatorResolved = !field?.stale_validation
      && cellValidation.some((summary) => summary.status === 'resolved')
      && cellValidation.every((summary) => (
        summary.status === 'resolved' || summary.status === 'waived'
      ))
    // A populated canonical-shaped field is not proof that validation ran. Only a
    // resolved validation projection may describe the second stage as a validator
    // result; otherwise the extractor value remains explicitly unvalidated.
    const comparison = projectedComparison
      ? projectedComparison.outcome === 'unresolved'
        ? projectedComparison
        : validatorResolved
          ? projectedComparison
          : baseState === 'resolved'
            ? { ...projectedComparison, outcome: 'overridden' as const }
            : { ...projectedComparison, outcome: 'unresolved' as const }
      : null
    const valueSource = comparison?.outcome === 'unresolved' ? 'extractor' : 'canonical'
    const projectedState = field
      ? comparison?.outcome === 'different'
        ? 'needs-review'
        : comparison?.outcome === 'unresolved'
          ? baseState === 'needs-review' ? 'needs-review' : 'ai-unconfirmed'
          : baseState
      : null

    return {
      columnKey: column.key,
      fieldKey: field?.field_key ?? null,
      fieldPath,
      hasField: field !== null,
      value: valueSource === 'extractor' ? comparison?.value ?? null : field?.value ?? null,
      required: field?.required ?? null,
      readOnly: field?.read_only ?? null,
      staleValidation: field?.stale_validation ?? null,
      state: projectedState,
      fieldValidation: field?.validation_result ?? null,
      evidence: cellEvidence,
      validation: validationProjection(cellValidation),
      extractorComparison: comparison,
      valueSource,
    }
  })

  return {
    candidateId: row.candidate.candidate_id,
    contextCell: {
      columnKey: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
      value: context,
      evidence: contextEvidence,
      validation: validationProjection(objectValidation),
    },
    cells,
    evidence,
    validation: validationProjection(validationSummaries),
    unmappedEvidence: evidence.filter((projection) => {
      const fieldPath = normalizeFieldPath(projection.field_path)
      return fieldPath !== null && !columnFieldPaths.has(fieldPath)
    }),
    unmappedValidation: validationProjection(validationSummaries.filter((projection) => {
      const fieldPath = normalizeFieldPath(projection.field_path)
      return fieldPath !== null && !columnFieldPaths.has(fieldPath)
    })),
  }
}

function sameProjectionRef(
  left: DomainEnvelopeProjectionRef,
  right: DomainEnvelopeProjectionRef,
): boolean {
  return (
    left.envelope_id === right.envelope_id
    && left.object_id === right.object_id
    && left.envelope_revision === right.envelope_revision
  )
}

function sourceRows({
  candidates,
  envelopeReviewRows,
}: BuildHorizontalGridModelInput): HorizontalGridSourceRow[] {
  const candidateIds = new Set(candidates.map((candidate) => candidate.candidate_id))
  const reviewRowsByCandidateId = new Map<string, WorkspaceEnvelopeObjectReviewRow>()

  for (const reviewRow of envelopeReviewRows) {
    const candidateId = reviewRow.candidate.candidate_id
    if (!candidateIds.has(candidateId)) {
      throw new Error(
        `Envelope review row references candidate '${candidateId}' outside the current candidates`,
      )
    }
    if (reviewRowsByCandidateId.has(candidateId)) {
      throw new Error(`Candidate '${candidateId}' has multiple envelope review rows`)
    }
    reviewRowsByCandidateId.set(candidateId, reviewRow)
  }

  return candidates.map((candidate) => {
    const envelopeReviewRow = reviewRowsByCandidateId.get(candidate.candidate_id) ?? null
    if (candidate.projection_ref) {
      if (!envelopeReviewRow) {
        throw new Error(
          `Candidate '${candidate.candidate_id}' has an envelope projection but no envelope review row`,
        )
      }
      if (!sameProjectionRef(candidate.projection_ref, envelopeReviewRow.projectionRef)) {
        throw new Error(
          `Envelope review row projection does not match candidate '${candidate.candidate_id}'`,
        )
      }

      return {
        candidate,
        projectionRef: candidate.projection_ref,
        reviewRow: envelopeReviewRow.reviewRow,
        evidenceAnchors: envelopeReviewRow.evidenceAnchors,
        validationSummaries: envelopeReviewRow.validationSummaries,
      }
    }

    if (envelopeReviewRow) {
      throw new Error(
        `Envelope review row references candidate '${candidate.candidate_id}' without an envelope projection`,
      )
    }

    return {
      candidate,
      projectionRef: null,
      reviewRow: null,
      evidenceAnchors: candidate.evidence_anchor_projections ?? [],
      validationSummaries: candidate.validation_summary_projections ?? [],
    }
  })
}

export function buildHorizontalGridModel(
  input: BuildHorizontalGridModelInput,
): HorizontalGridModel {
  const orderedRows = sourceRows(input).sort(compareRows)
  const fieldColumns = buildFieldColumns(orderedRows)

  return {
    columns: [
      {
        key: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
        kind: 'context',
        fieldPath: null,
        label: 'Object',
        order: -1,
        required: false,
        readOnly: true,
        groupKey: null,
        groupLabel: null,
      },
      ...fieldColumns,
    ],
    rows: orderedRows.map((row) => projectRow(row, fieldColumns)),
  }
}

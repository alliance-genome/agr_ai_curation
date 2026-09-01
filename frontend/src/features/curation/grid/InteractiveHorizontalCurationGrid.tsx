import { useCallback, useMemo, useState } from 'react'

import { getCurationAdapterEditorPack } from '@/features/curation/adapters'
import {
  buildNavigationCommandFromEnvelopeEvidenceProjection,
  dispatchEvidenceNavigationCommand,
} from '@/features/curation/evidence'
import type { FieldStateKind } from '@/features/curation/editor/fieldState'
import type {
  CurationCandidate,
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
} from '@/features/curation/types'
import {
  useCurationWorkspaceAutosave,
  useCurationWorkspaceContext,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import {
  resolveEnvelopeFieldPath,
} from '@/features/curation/workspace/workspaceState'
import HorizontalCurationGrid, {
  type HorizontalCurationGridProps,
  type HorizontalGridContextRenderArgs,
  type HorizontalGridFieldRenderArgs,
} from './HorizontalCurationGrid'
import HorizontalGridCellActions from './HorizontalGridCellActions'
import {
  HorizontalGridContextCellContent,
  HorizontalGridFieldCellContent,
  contextEvidenceFieldPath,
  contextEvidenceLabel,
} from './HorizontalGridCells'
import HorizontalGridEvidencePopover, {
  type HorizontalGridEvidencePopoverTarget,
} from './HorizontalGridEvidencePopover'
import HorizontalGridFieldEditorDialog from './HorizontalGridFieldEditorDialog'
import HorizontalGridValidationPreviewRowActions from './HorizontalGridValidationPreviewRowActions'
import { formatHorizontalGridValue } from './horizontalGridFormatting'
import type { HorizontalGridModel } from './horizontalGridModel'
import {
  applyHorizontalGridValidationPreview,
  horizontalGridValidationPreviewKey,
  toggledHorizontalGridValidationPreviewState,
} from './horizontalGridValidationPreview'

interface EditingTarget {
  candidateId: string
  fieldKey: string
  fieldPath: string
}

interface ValidationPreviewState {
  model: HorizontalGridModel
  notice: string
  overrides: ReadonlyMap<string, FieldStateKind>
}

const EMPTY_VALIDATION_PREVIEW_OVERRIDES = new Map<string, FieldStateKind>()

export type InteractiveHorizontalCurationGridProps = Omit<
  HorizontalCurationGridProps,
  'renderCellActions' | 'renderContextCell' | 'renderFieldCell' | 'renderRowActions'
>

function candidateForRow(
  candidates: readonly CurationCandidate[],
  candidateId: string,
): CurationCandidate {
  const candidate = candidates.find((item) => item.candidate_id === candidateId)
  if (!candidate) {
    throw new Error(`Horizontal grid row references missing candidate '${candidateId}'`)
  }
  return candidate
}

function canonicalFieldForCell(
  candidates: readonly CurationCandidate[],
  { cell, row }: HorizontalGridFieldRenderArgs,
): { candidate: CurationCandidate; field: CurationDraftField | null } {
  const candidate = candidateForRow(candidates, row.candidateId)
  if (!cell.hasField) {
    return { candidate, field: null }
  }

  if (!cell.fieldKey) {
    throw new Error(`Horizontal grid field '${cell.fieldPath}' has no canonical field key`)
  }

  const field = candidate.draft.fields.find((item) => item.field_key === cell.fieldKey)
  if (!field) {
    throw new Error(
      `Horizontal grid field '${cell.fieldPath}' is missing candidate field '${cell.fieldKey}'`,
    )
  }

  const canonicalPath = resolveEnvelopeFieldPath(field)
  if (canonicalPath !== cell.fieldPath) {
    throw new Error(
      `Horizontal grid field '${cell.fieldPath}' does not match canonical path '${canonicalPath}'`,
    )
  }

  return { candidate, field }
}

export default function InteractiveHorizontalCurationGrid({
  model,
  ...gridProps
}: InteractiveHorizontalCurationGridProps) {
  const {
    activeCandidateId,
    candidates,
    setActiveCandidate,
  } = useCurationWorkspaceContext()
  const autosave = useCurationWorkspaceAutosave()
  const [editingTarget, setEditingTarget] = useState<EditingTarget | null>(null)
  const [evidenceTarget, setEvidenceTarget] = useState<HorizontalGridEvidencePopoverTarget | null>(null)
  const [validationPreview, setValidationPreview] = useState<ValidationPreviewState>(() => ({
    model,
    notice: '',
    overrides: EMPTY_VALIDATION_PREVIEW_OVERRIDES,
  }))
  const activeValidationPreviewOverrides = validationPreview.model === model
    ? validationPreview.overrides
    : EMPTY_VALIDATION_PREVIEW_OVERRIDES
  const validationPreviewNotice = validationPreview.model === model
    ? validationPreview.notice
    : ''
  const displayedModel = useMemo(
    () => applyHorizontalGridValidationPreview(model, activeValidationPreviewOverrides),
    [activeValidationPreviewOverrides, model],
  )

  const selectCandidate = useCallback((candidateId: string) => {
    if (activeCandidateId !== candidateId) {
      setActiveCandidate(candidateId)
    }
  }, [activeCandidateId, setActiveCandidate])

  const selectedEditor = useMemo(() => {
    if (!editingTarget) {
      return null
    }

    const candidate = candidateForRow(candidates, editingTarget.candidateId)
    const field = candidate.draft.fields.find((item) => item.field_key === editingTarget.fieldKey)
    if (!field) {
      throw new Error(
        `Horizontal grid editor cannot find candidate field '${editingTarget.fieldKey}'`,
      )
    }
    const canonicalPath = resolveEnvelopeFieldPath(field)
    if (canonicalPath !== editingTarget.fieldPath) {
      throw new Error(
        `Horizontal grid editor path '${editingTarget.fieldPath}' does not match '${canonicalPath}'`,
      )
    }

    return {
      candidate,
      editorPack: getCurationAdapterEditorPack(candidate.adapter_key),
      field,
    }
  }, [candidates, editingTarget])

  const renderFieldCell = useCallback((args: HorizontalGridFieldRenderArgs) => {
    const { field } = canonicalFieldForCell(candidates, args)
    return (
      <HorizontalGridFieldCellContent
        active={activeCandidateId === args.row.candidateId}
        cell={args.cell}
        field={field}
        onSelect={() => selectCandidate(args.row.candidateId)}
        state={args.cell.state}
      />
    )
  }, [activeCandidateId, candidates, selectCandidate])

  const renderCellActions = useCallback((args: HorizontalGridFieldRenderArgs) => {
    const { candidate, field } = canonicalFieldForCell(candidates, args)

    return (
      <HorizontalGridCellActions
        cell={args.cell}
        field={field}
        isSaving={autosave.isSaving}
        recordLabel={args.row.contextCell.value.identityLabel}
        onEdit={(editableField) => {
          setEvidenceTarget(null)
          setEditingTarget({
            candidateId: candidate.candidate_id,
            fieldKey: editableField.field_key,
            fieldPath: args.cell.fieldPath,
          })
        }}
        onDetails={(anchorEl) => {
          const navigateEvidence = (
            projection: DomainEnvelopeEvidenceAnchorProjection,
          ) => {
            const command = buildNavigationCommandFromEnvelopeEvidenceProjection(projection)
            if (!command) {
              return
            }
            dispatchEvidenceNavigationCommand(
              command,
              {
                source: 'horizontal-curation-grid',
                candidateId: candidate.candidate_id,
                objectId: projection.object_id,
                fieldKey: field?.field_key ?? null,
                fieldPath: args.cell.fieldPath,
              },
            )
          }
          setEvidenceTarget({
            anchorEl,
            canonicalFieldValue: field && args.cell.extractorComparison?.outcome !== 'unresolved'
              ? formatHorizontalGridValue(field.value)
              : null,
            extractorComparison: args.cell.extractorComparison,
            fieldLabel: field?.label ?? args.column.label,
            fieldValue: formatHorizontalGridValue(args.cell.value) ?? '—',
            onEvidence: navigateEvidence,
            projections: args.cell.evidence,
            sourceMention: args.row.contextCell.value.identityLabel,
            state: args.cell.state,
            validatorResolved: !args.cell.staleValidation
              && args.cell.validation.statuses.some((status) => status === 'resolved')
              && args.cell.validation.statuses.every((status) => (
                status === 'resolved' || status === 'waived'
              )),
            validationMessages: args.cell.validation.summaries.flatMap((summary) => summary.messages),
          })
          const projection = args.cell.evidence.find(
            (item) => buildNavigationCommandFromEnvelopeEvidenceProjection(item) !== null,
          )
          if (projection) {
            navigateEvidence(projection)
          }
        }}
        onSelect={() => selectCandidate(candidate.candidate_id)}
        onToggleValidationPreview={(previewField) => {
          const currentState = args.cell.state
          if (currentState === null) {
            return
          }

          const nextState = toggledHorizontalGridValidationPreviewState(currentState)
          setValidationPreview((current) => {
            const overrides = new Map(
              current.model === model
                ? current.overrides
                : EMPTY_VALIDATION_PREVIEW_OVERRIDES,
            )
            overrides.set(
              horizontalGridValidationPreviewKey(candidate.candidate_id, args.cell.fieldPath),
              nextState,
            )
            return {
              model,
              notice: `${previewField.label} marked ${
                nextState === 'resolved' ? 'curator validated' : 'as needing review'
              } for this preview only. No validation was run or saved.`,
              overrides,
            }
          })
        }}
        previewState={args.cell.state}
      />
    )
  }, [autosave.isSaving, candidates, model, selectCandidate])

  const renderContextCell = useCallback(({ cell, row }: HorizontalGridContextRenderArgs) => (
    <HorizontalGridContextCellContent
      active={activeCandidateId === row.candidateId}
      cell={cell}
      onEvidence={(projection, command, anchorEl) => {
        const navigateEvidence = (
          selectedProjection: DomainEnvelopeEvidenceAnchorProjection,
        ) => {
          const selectedCommand = selectedProjection.anchor_id === projection.anchor_id
            ? command
            : buildNavigationCommandFromEnvelopeEvidenceProjection(selectedProjection)
          if (!selectedCommand) {
            return
          }
          dispatchEvidenceNavigationCommand(
            selectedCommand,
            {
              source: 'horizontal-curation-grid-context',
              candidateId: row.candidateId,
              objectId: selectedProjection.object_id,
              fieldPath: contextEvidenceFieldPath(selectedProjection),
            },
          )
        }
        setEvidenceTarget({
          anchorEl,
          canonicalFieldValue: null,
          extractorComparison: null,
          fieldLabel: contextEvidenceLabel(projection),
          fieldValue: cell.value.identityLabel,
          onEvidence: navigateEvidence,
          projections: [projection],
          sourceMention: null,
          state: null,
          validatorResolved: false,
          validationMessages: [],
        })
        navigateEvidence(projection)
      }}
      onSelect={() => selectCandidate(row.candidateId)}
    />
  ), [activeCandidateId, selectCandidate])

  const renderRowActions = useCallback((row: HorizontalGridModel['rows'][number]) => (
    <HorizontalGridValidationPreviewRowActions
      onValidate={() => {
        selectCandidate(row.candidateId)
        setValidationPreview((current) => {
          const overrides = new Map(
            current.model === model
              ? current.overrides
              : EMPTY_VALIDATION_PREVIEW_OVERRIDES,
          )
          for (const cell of row.cells) {
            if (cell.hasField && cell.state !== null) {
              overrides.set(
                horizontalGridValidationPreviewKey(row.candidateId, cell.fieldPath),
                'resolved',
              )
            }
          }
          return {
            model,
            notice: `${row.contextCell.value.identityLabel} marked curator validated for this preview only. No validation was run or saved.`,
            overrides,
          }
        })
      }}
      row={row}
    />
  ), [model, selectCandidate])

  return (
    <>
      <HorizontalCurationGrid
        {...gridProps}
        model={displayedModel}
        selectedCandidateId={activeCandidateId}
        renderCellActions={renderCellActions}
        renderContextCell={renderContextCell}
        renderFieldCell={renderFieldCell}
        renderRowActions={renderRowActions}
        rowActionsLabel="Validate"
        validationPreviewNotice={validationPreviewNotice}
      />
      <HorizontalGridEvidencePopover
        onClose={() => setEvidenceTarget(null)}
        target={evidenceTarget}
      />
      <HorizontalGridFieldEditorDialog
        autosaveWarning={autosave.warning}
        editorPack={selectedEditor?.editorPack ?? null}
        field={selectedEditor?.field ?? null}
        isSaving={autosave.isSaving}
        onSave={async (value) => {
          if (!selectedEditor || selectedEditor.field.read_only) {
            throw new Error('Horizontal grid editor change requires an editable field')
          }
          const { field } = selectedEditor
          autosave.queueFieldChange({ field_key: field.field_key, value })
          const saved = await autosave.flush()
          if (saved) {
            setEditingTarget(null)
          }
          return saved
        }}
        onClose={() => setEditingTarget(null)}
        onRevert={async () => {
          if (!selectedEditor || selectedEditor.field.read_only) {
            throw new Error('Horizontal grid editor revert requires an editable field')
          }
          const { field } = selectedEditor
          autosave.queueFieldChange({
            field_key: field.field_key,
            revert_to_seed: true,
          })
          const saved = await autosave.flush()
          if (saved) {
            setEditingTarget(null)
          }
          return saved
        }}
        open={editingTarget !== null}
      />
    </>
  )
}

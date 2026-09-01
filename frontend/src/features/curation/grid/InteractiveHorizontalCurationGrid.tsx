import { useCallback, useMemo, useState } from 'react'

import { getCurationAdapterEditorPack } from '@/features/curation/adapters'
import {
  dispatchEvidenceNavigationCommand,
} from '@/features/curation/evidence'
import type {
  CurationCandidate,
  CurationDraftField,
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
} from './HorizontalGridCells'
import HorizontalGridEvidencePopover, {
  type HorizontalGridEvidencePopoverTarget,
} from './HorizontalGridEvidencePopover'
import HorizontalGridFieldEditorDialog from './HorizontalGridFieldEditorDialog'
import { formatHorizontalGridValue } from './horizontalGridFormatting'

interface EditingTarget {
  candidateId: string
  fieldKey: string
  fieldPath: string
}

export type InteractiveHorizontalCurationGridProps = Omit<
  HorizontalCurationGridProps,
  'renderCellActions' | 'renderContextCell' | 'renderFieldCell'
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
        onEdit={(editableField) => {
          setEvidenceTarget(null)
          setEditingTarget({
            candidateId: candidate.candidate_id,
            fieldKey: editableField.field_key,
            fieldPath: args.cell.fieldPath,
          })
        }}
        onEvidence={(projection, command, anchorEl) => {
          setEvidenceTarget({
            anchorEl,
            fieldLabel: field?.label ?? args.column.label,
            fieldValue: field ? formatHorizontalGridValue(field.value) ?? '—' : '—',
            projection,
            state: args.cell.state,
            validationMessages: args.cell.validation.summaries.flatMap((summary) => summary.messages),
          })
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
        }}
        onSelect={() => selectCandidate(candidate.candidate_id)}
      />
    )
  }, [autosave.isSaving, candidates, selectCandidate])

  const renderContextCell = useCallback(({ cell, row }: HorizontalGridContextRenderArgs) => (
    <HorizontalGridContextCellContent
      active={activeCandidateId === row.candidateId}
      cell={cell}
      onEvidence={(projection, command, anchorEl) => {
        setEvidenceTarget({
          anchorEl,
          fieldLabel: 'Object evidence',
          fieldValue: cell.value.identityLabel,
          projection,
          state: null,
          validationMessages: [],
        })
        dispatchEvidenceNavigationCommand(
          command,
          {
            source: 'horizontal-curation-grid-context',
            candidateId: row.candidateId,
            objectId: projection.object_id,
            fieldPath: null,
          },
        )
      }}
      onSelect={() => selectCandidate(row.candidateId)}
    />
  ), [activeCandidateId, selectCandidate])

  return (
    <>
      <HorizontalCurationGrid
        {...gridProps}
        model={model}
        selectedCandidateId={activeCandidateId}
        renderCellActions={renderCellActions}
        renderContextCell={renderContextCell}
        renderFieldCell={renderFieldCell}
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

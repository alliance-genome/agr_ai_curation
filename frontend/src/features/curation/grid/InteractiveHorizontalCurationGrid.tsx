import { useCallback, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { getCurationAdapterEditorPack } from '@/features/curation/adapters'
import {
  buildNavigationCommandFromEnvelopeEvidenceProjection,
  dispatchEvidenceNavigationCommand,
} from '@/features/curation/evidence'
import { fieldState } from '@/features/curation/editor/fieldState'
import {
  validateCurationCandidate,
} from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationCandidate,
  CurationDraftField,
} from '@/features/curation/types'
import {
  useCurationWorkspaceAutosave,
  useCurationWorkspaceContext,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import {
  replaceWorkspaceCandidate,
  resolveEnvelopeFieldPath,
} from '@/features/curation/workspace/workspaceState'
import { curationWorkspaceEnvelopeReviewRowsQueryKey } from '@/features/curation/workspace/queryKeys'
import HorizontalCurationGrid, {
  type HorizontalCurationGridProps,
  type HorizontalGridFieldRenderArgs,
} from './HorizontalCurationGrid'
import HorizontalGridCellActions from './HorizontalGridCellActions'
import {
  HorizontalGridContextCellContent,
  HorizontalGridFieldCellContent,
} from './HorizontalGridCells'
import HorizontalGridFieldEditorDialog from './HorizontalGridFieldEditorDialog'

interface EditingTarget {
  candidateId: string
  fieldKey: string
  fieldPath: string
}

interface ValidationRequestState {
  error: string | null
  isLoading: boolean
}

export type InteractiveHorizontalCurationGridProps = Omit<
  HorizontalCurationGridProps,
  'renderCellActions' | 'renderContextCell' | 'renderFieldCell'
>

function validationStateKey(candidateId: string, fieldKey: string): string {
  return `${candidateId}:${fieldKey}`
}

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
  const queryClient = useQueryClient()
  const {
    activeCandidateId,
    candidates,
    setActiveCandidate,
    setWorkspace,
    workspace,
  } = useCurationWorkspaceContext()
  const autosave = useCurationWorkspaceAutosave()
  const [editingTarget, setEditingTarget] = useState<EditingTarget | null>(null)
  const [validationStates, setValidationStates] = useState<Record<string, ValidationRequestState>>({})

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

  const setValidationState = useCallback((
    key: string,
    state: ValidationRequestState,
  ) => {
    setValidationStates((current) => ({ ...current, [key]: state }))
  }, [])

  const validateField = useCallback(async (
    candidate: CurationCandidate,
    field: CurationDraftField,
  ) => {
    const key = validationStateKey(candidate.candidate_id, field.field_key)
    setValidationState(key, { error: null, isLoading: true })

    try {
      const pendingSaved = await autosave.flush()
      if (!pendingSaved) {
        throw new Error('Unable to save pending field changes before validation.')
      }

      const response = await validateCurationCandidate({
        session_id: workspace.session.session_id,
        candidate_id: candidate.candidate_id,
        field_keys: [field.field_key],
      })
      setWorkspace((currentWorkspace) =>
        replaceWorkspaceCandidate(currentWorkspace, response.candidate),
      )
      await queryClient.invalidateQueries({
        queryKey: curationWorkspaceEnvelopeReviewRowsQueryKey(workspace.session.session_id),
      })
      if (response.validation_snapshot.state === 'failed') {
        throw new Error(
          response.validation_snapshot.warnings[0]
            ?? 'The server could not validate this field.',
        )
      }
      setValidationState(key, { error: null, isLoading: false })
    } catch (error) {
      setValidationState(key, {
        error: error instanceof Error ? error.message : 'Unable to validate this field.',
        isLoading: false,
      })
    }
  }, [autosave, queryClient, setValidationState, setWorkspace, workspace.session.session_id])

  const renderFieldCell = useCallback((args: HorizontalGridFieldRenderArgs) => {
    const { field } = canonicalFieldForCell(candidates, args)
    return (
      <HorizontalGridFieldCellContent
        active={activeCandidateId === args.row.candidateId}
        cell={args.cell}
        field={field}
        onSelect={() => selectCandidate(args.row.candidateId)}
        state={field ? fieldState(field, args.cell.validation.summaries) : null}
      />
    )
  }, [activeCandidateId, candidates, selectCandidate])

  const renderCellActions = useCallback((args: HorizontalGridFieldRenderArgs) => {
    const { candidate, field } = canonicalFieldForCell(candidates, args)
    const requestState = field
      ? validationStates[validationStateKey(candidate.candidate_id, field.field_key)]
      : undefined

    return (
      <HorizontalGridCellActions
        cell={args.cell}
        error={requestState?.error ?? null}
        field={field}
        isSaving={autosave.isSaving}
        isValidating={requestState?.isLoading ?? false}
        onEdit={() => {
          if (!field || field.read_only) {
            return
          }
          setEditingTarget({
            candidateId: candidate.candidate_id,
            fieldKey: field.field_key,
            fieldPath: args.cell.fieldPath,
          })
        }}
        onEvidence={(evidenceIndex) => {
          const projection = args.cell.evidence[evidenceIndex]
          if (!projection) {
            return
          }
          dispatchEvidenceNavigationCommand(
            buildNavigationCommandFromEnvelopeEvidenceProjection(projection),
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
        onValidate={() => {
          if (field && !field.read_only) {
            void validateField(candidate, field)
          }
        }}
      />
    )
  }, [autosave.isSaving, candidates, selectCandidate, validateField, validationStates])

  return (
    <>
      <HorizontalCurationGrid
        {...gridProps}
        model={model}
        renderCellActions={renderCellActions}
        renderContextCell={({ cell, row }) => (
          <HorizontalGridContextCellContent
            active={activeCandidateId === row.candidateId}
            cell={cell}
            onEvidence={(evidenceIndex) => {
              const projection = cell.evidence[evidenceIndex]
              if (!projection) {
                return
              }
              dispatchEvidenceNavigationCommand(
                buildNavigationCommandFromEnvelopeEvidenceProjection(projection),
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
        )}
        renderFieldCell={renderFieldCell}
      />
      <HorizontalGridFieldEditorDialog
        autosaveWarning={autosave.warning}
        editorPack={selectedEditor?.editorPack ?? null}
        field={selectedEditor?.field ?? null}
        onChange={(value) => {
          const field = selectedEditor?.field
          if (!field || field.read_only) {
            return
          }
          autosave.queueFieldChange({ field_key: field.field_key, value })
        }}
        onClose={() => setEditingTarget(null)}
        onRevert={() => {
          const field = selectedEditor?.field
          if (!field || field.read_only) {
            return
          }
          autosave.queueFieldChange({
            field_key: field.field_key,
            revert_to_seed: true,
          })
        }}
        open={editingTarget !== null}
      />
    </>
  )
}

import type {
  CurationCandidate,
  CurationCandidateDraftUpdateResponse,
  CurationDraftField,
  CurationDraftFieldChange,
  CurationReviewSession,
  CurationWorkspace,
} from '@/features/curation/types'

function stableSerialize(value: unknown): string {
  if (value === null || value === undefined) {
    return String(value)
  }

  if (typeof value !== 'object') {
    return JSON.stringify(value)
  }

  if (Array.isArray(value)) {
    return `[${value.map(stableSerialize).join(',')}]`
  }

  const entries = Object.entries(value as Record<string, unknown>)
    .sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey))
    .map(([key, entryValue]) => `${JSON.stringify(key)}:${stableSerialize(entryValue)}`)

  return `{${entries.join(',')}}`
}

export function areDraftFieldValuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {
    return true
  }

  return stableSerialize(left) === stableSerialize(right)
}

function applyDraftFieldChange(
  field: CurationDraftField,
  change: CurationDraftFieldChange,
): CurationDraftField {
  const nextValue = change.revert_to_seed ? field.seed_value ?? null : change.value ?? null
  const nextDirty = !areDraftFieldValuesEqual(nextValue, field.seed_value ?? null)

  return {
    ...field,
    value: nextValue,
    dirty: nextDirty,
    stale_validation: nextDirty,
  }
}

export function findWorkspaceCandidate(
  workspace: CurationWorkspace,
  candidateId?: string | null,
): CurationCandidate | null {
  if (!candidateId) {
    return null
  }

  return workspace.candidates.find((candidate) => candidate.candidate_id === candidateId) ?? null
}

export function replaceWorkspaceSession(
  workspace: CurationWorkspace,
  session: CurationReviewSession,
): CurationWorkspace {
  return {
    ...workspace,
    session,
  }
}

export function replaceWorkspaceCandidate(
  workspace: CurationWorkspace,
  candidate: CurationCandidate,
): CurationWorkspace {
  return {
    ...workspace,
    candidates: workspace.candidates.map((currentCandidate) =>
      currentCandidate.candidate_id === candidate.candidate_id ? candidate : currentCandidate,
    ),
  }
}

export function updateWorkspaceActiveCandidate(
  workspace: CurationWorkspace,
  candidateId: string | null,
): CurationWorkspace {
  return {
    ...workspace,
    active_candidate_id: candidateId,
    session: {
      ...workspace.session,
      current_candidate_id: candidateId,
    },
  }
}

export function applyDraftFieldChangesToWorkspace(
  workspace: CurationWorkspace,
  candidateId: string,
  fieldChanges: CurationDraftFieldChange[],
): CurationWorkspace {
  const fieldChangesByKey = new Map(
    fieldChanges.map((fieldChange) => [fieldChange.field_key, fieldChange]),
  )

  return {
    ...workspace,
    candidates: workspace.candidates.map((candidate) => {
      if (candidate.candidate_id !== candidateId) {
        return candidate
      }

      return {
        ...candidate,
        draft: {
          ...candidate.draft,
          fields: candidate.draft.fields.map((field) => {
            const change = fieldChangesByKey.get(field.field_key)
            return change ? applyDraftFieldChange(field, change) : field
          }),
        },
      }
    }),
  }
}

export function mergeSavedDraftIntoWorkspace(
  workspace: CurationWorkspace,
  response: CurationCandidateDraftUpdateResponse,
): CurationWorkspace {
  const savedCandidate: CurationCandidate = {
    ...response.candidate,
    draft: response.draft,
    validation: response.validation_snapshot?.summary ?? response.candidate.validation,
  }

  return replaceWorkspaceCandidate(workspace, savedCandidate)
}

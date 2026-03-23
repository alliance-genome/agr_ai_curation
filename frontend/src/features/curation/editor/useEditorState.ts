import { useLayoutEffect, useMemo, useReducer } from 'react'

import type { CurationDraftField } from '../types'
import { areDraftFieldValuesEqual } from '../workspace/workspaceState'

interface EditorState {
  candidateId: string | null
  sourceFields: CurationDraftField[]
  fields: CurationDraftField[]
}

const EMPTY_FIELDS: CurationDraftField[] = []

type EditorAction =
  | {
      type: 'hydrate'
      candidateId: string | null
      fields: CurationDraftField[]
    }
  | {
      type: 'setFieldValue'
      fieldKey: string
      value: unknown
    }
  | {
      type: 'revertField'
      fieldKey: string
    }

export interface UseEditorStateOptions {
  candidateId?: string | null
  fields?: CurationDraftField[]
}

export interface UseEditorStateReturn {
  fields: CurationDraftField[]
  dirtyFieldKeys: string[]
  isDirty: boolean
  getField: (fieldKey: string) => CurationDraftField | undefined
  revertField: (fieldKey: string) => void
  setFieldValue: (fieldKey: string, value: unknown) => void
}

function sortFields(fields: CurationDraftField[]): CurationDraftField[] {
  return [...fields].sort(
    (left, right) =>
      left.order - right.order ||
      left.label.localeCompare(right.label) ||
      left.field_key.localeCompare(right.field_key),
  )
}

function applyFieldValue(
  field: CurationDraftField,
  nextValue: unknown,
): CurationDraftField {
  const resolvedValue = nextValue ?? null
  const nextDirty = !areDraftFieldValuesEqual(resolvedValue, field.seed_value ?? null)

  return {
    ...field,
    value: resolvedValue,
    dirty: nextDirty,
    stale_validation: nextDirty,
  }
}

function createState(
  candidateId: string | null,
  fields: CurationDraftField[],
): EditorState {
  return {
    candidateId,
    sourceFields: fields,
    fields: sortFields(fields),
  }
}

function editorReducer(
  state: EditorState,
  action: EditorAction,
): EditorState {
  switch (action.type) {
    case 'hydrate':
      return createState(action.candidateId, action.fields)
    case 'setFieldValue':
      return {
        ...state,
        fields: state.fields.map((field) =>
          field.field_key === action.fieldKey
            ? applyFieldValue(field, action.value)
            : field),
      }
    case 'revertField':
      return {
        ...state,
        fields: state.fields.map((field) =>
          field.field_key === action.fieldKey
            ? applyFieldValue(field, field.seed_value ?? null)
            : field),
      }
    default:
      return state
  }
}

export function useEditorState(
  options: UseEditorStateOptions = {},
): UseEditorStateReturn {
  const candidateId = options.candidateId ?? null
  const fields = options.fields ?? EMPTY_FIELDS
  const incomingState = useMemo(
    () => createState(candidateId, fields),
    [candidateId, fields],
  )
  const [state, dispatch] = useReducer(editorReducer, incomingState)
  const needsHydration =
    state.candidateId !== incomingState.candidateId ||
    state.sourceFields !== incomingState.sourceFields
  const resolvedState = needsHydration ? incomingState : state

  useLayoutEffect(() => {
    if (!needsHydration) {
      return
    }

    dispatch({
      type: 'hydrate',
      candidateId: incomingState.candidateId,
      fields: incomingState.sourceFields,
    })
  }, [incomingState, needsHydration])

  const fieldByKey = useMemo(
    () => new Map(resolvedState.fields.map((field) => [field.field_key, field])),
    [resolvedState.fields],
  )
  const dirtyFieldKeys = useMemo(
    () => resolvedState.fields
      .filter((field) => field.dirty)
      .map((field) => field.field_key),
    [resolvedState.fields],
  )

  return {
    fields: resolvedState.fields,
    dirtyFieldKeys,
    isDirty: dirtyFieldKeys.length > 0,
    getField: (fieldKey) => fieldByKey.get(fieldKey),
    revertField: (fieldKey) => {
      dispatch({
        type: 'revertField',
        fieldKey,
      })
    },
    setFieldValue: (fieldKey, value) => {
      dispatch({
        type: 'setFieldValue',
        fieldKey,
        value,
      })
    },
  }
}

export default useEditorState

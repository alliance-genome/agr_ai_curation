import { useEffect, useMemo, useReducer } from 'react'

import type { CurationDraftField } from '../types'
import { areDraftFieldValuesEqual } from '../workspace/workspaceState'

interface EditorState {
  candidateId: string | null
  fields: CurationDraftField[]
}

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
    stale_validation: nextDirty || field.stale_validation,
  }
}

function createState(
  candidateId: string | null,
  fields: CurationDraftField[],
): EditorState {
  return {
    candidateId,
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
  const fields = options.fields ?? []
  const [state, dispatch] = useReducer(editorReducer, createState(candidateId, fields))

  useEffect(() => {
    dispatch({
      type: 'hydrate',
      candidateId,
      fields,
    })
  }, [candidateId, fields])

  const fieldByKey = useMemo(
    () => new Map(state.fields.map((field) => [field.field_key, field])),
    [state.fields],
  )
  const dirtyFieldKeys = useMemo(
    () => state.fields.filter((field) => field.dirty).map((field) => field.field_key),
    [state.fields],
  )

  return {
    fields: state.fields,
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

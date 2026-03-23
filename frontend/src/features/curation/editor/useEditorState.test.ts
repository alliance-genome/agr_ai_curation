import { createElement } from 'react'
import { act, render, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { CurationDraftField } from '../types'
import { useEditorState } from './useEditorState'

function createField(
  overrides: Partial<CurationDraftField> = {},
): CurationDraftField {
  return {
    field_key: 'gene_symbol',
    label: 'Gene symbol',
    value: 'BRCA1',
    seed_value: 'BRCA1',
    field_type: 'string',
    group_key: 'primary_data',
    group_label: 'Primary data',
    order: 0,
    required: true,
    read_only: false,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: {},
    ...overrides,
  }
}

describe('useEditorState', () => {
  it('clears stale validation when a field is reverted to its seed value', () => {
    const fields = [createField()]
    const { result } = renderHook(() =>
      useEditorState({
        candidateId: 'candidate-1',
        fields,
      }),
    )

    act(() => {
      result.current.setFieldValue('gene_symbol', 'BRCA2')
    })

    expect(result.current.getField('gene_symbol')).toMatchObject({
      value: 'BRCA2',
      dirty: true,
      stale_validation: true,
    })

    act(() => {
      result.current.revertField('gene_symbol')
    })

    expect(result.current.getField('gene_symbol')).toMatchObject({
      value: 'BRCA1',
      dirty: false,
      stale_validation: false,
    })
  })

  it('renders the latest candidate fields immediately when the active candidate changes', () => {
    const renderSpy = vi.fn()
    const firstFields = [createField()]
    const secondFields = [
      createField({
        field_key: 'disease_term',
        label: 'Disease term',
        value: 'Alzheimer disease',
        seed_value: 'Alzheimer disease',
        group_key: 'context',
        group_label: 'Context',
      }),
    ]

    function EditorStateProbe({
      candidateId,
      fields,
    }: {
      candidateId: string | null
      fields: CurationDraftField[]
    }) {
      const state = useEditorState({
        candidateId,
        fields,
      })

      renderSpy(state.fields.map((field) => field.field_key))

      return null
    }

    const { rerender } = render(
      createElement(EditorStateProbe, {
        candidateId: 'candidate-1',
        fields: firstFields,
      }),
    )

    renderSpy.mockClear()

    rerender(
      createElement(EditorStateProbe, {
        candidateId: 'candidate-2',
        fields: secondFields,
      }),
    )

    expect(renderSpy.mock.calls.map(([fieldKeys]) => fieldKeys)).not.toContainEqual([
      'gene_symbol',
    ])
    expect(renderSpy.mock.calls.at(-1)).toEqual([['disease_term']])
  })
})

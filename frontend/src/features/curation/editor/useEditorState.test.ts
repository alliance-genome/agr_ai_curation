import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

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
})

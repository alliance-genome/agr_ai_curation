import { fireEvent, render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type { CurationDraftField } from '@/features/curation/types'
import theme from '@/theme'
import { getCurationAdapterEditorPack } from '../index'
import { REFERENCE_ADAPTER_KEY, renderReferenceFieldInput } from './index'

function createField(
  overrides: Partial<CurationDraftField> = {},
): CurationDraftField {
  return {
    field_key: 'citation.authors',
    label: 'Authors',
    value: ['Ada Lovelace', 'Grace Hopper'],
    seed_value: ['Ada Lovelace', 'Grace Hopper'],
    field_type: 'json',
    group_key: 'citation_details',
    group_label: 'Citation details',
    order: 10,
    required: false,
    read_only: false,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: {
      widget: 'reference_author_list',
      helper_text: 'One author per line.',
      placeholder: 'Ada Lovelace\nGrace Hopper',
    },
    ...overrides,
  }
}

function renderInput(field: CurationDraftField = createField()) {
  const onChange = vi.fn()

  render(
    <ThemeProvider theme={theme}>
      {renderReferenceFieldInput({
        ariaLabel: field.label,
        disabled: field.read_only,
        field,
        inputId: `input-${field.field_key}`,
        onChange,
        value: field.value,
      })}
    </ThemeProvider>,
  )

  return { onChange }
}

describe('referenceEditorPack', () => {
  it('registers the reference adapter editor pack by adapter key', () => {
    const editorPack = getCurationAdapterEditorPack(REFERENCE_ADAPTER_KEY)

    expect(editorPack?.adapterKey).toBe(REFERENCE_ADAPTER_KEY)
    expect(editorPack?.fieldLayout.map((field) => field.fieldKey)).toContain('citation.authors')
    expect(getCurationAdapterEditorPack('unknown-adapter')).toBeNull()
  })

  it('renders the adapter-owned author list widget and emits normalized array values', () => {
    const { onChange } = renderInput()

    const authorsInput = screen.getByLabelText('Authors')
    expect(authorsInput).toHaveValue('Ada Lovelace\nGrace Hopper')
    expect(screen.getByText('One author per line.')).toBeInTheDocument()

    fireEvent.change(authorsInput, {
      target: { value: 'Ada Lovelace\nKatherine Johnson' },
    })

    expect(onChange).toHaveBeenCalledWith(['Ada Lovelace', 'Katherine Johnson'])
  })
})

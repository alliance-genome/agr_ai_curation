import { fireEvent, render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type { CurationDraftField } from '../types'
import theme from '@/theme'
import FieldRow, { type FieldRowProps } from './FieldRow'

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

function renderFieldRow(props: Partial<FieldRowProps> = {}) {
  const onChange = props.onChange ?? vi.fn()
  const field = props.field ?? createField()
  const resolvedProps: FieldRowProps = {
    field,
    onChange,
    value: props.value ?? field.value,
    validationSlot: props.validationSlot,
    evidenceSlot: props.evidenceSlot,
    revertSlot: props.revertSlot,
    renderInput: props.renderInput,
  }

  const renderResult = render(
    <ThemeProvider theme={theme}>
      <FieldRow {...resolvedProps} />
    </ThemeProvider>,
  )

  return {
    ...renderResult,
    onChange,
  }
}

describe('FieldRow', () => {
  it('renders the field label, default input, and slot content', () => {
    const { onChange } = renderFieldRow({
      validationSlot: <span>Validated</span>,
      evidenceSlot: <button type="button">p.3</button>,
      revertSlot: <button type="button">Revert</button>,
    })

    expect(screen.getByTestId('field-row-gene_symbol')).toHaveAttribute(
      'data-field-key',
      'gene_symbol',
    )
    expect(screen.getByText('Gene symbol')).toBeInTheDocument()
    expect(screen.getByLabelText('Gene symbol')).toHaveValue('BRCA1')
    expect(screen.getByText('Validated')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'p.3' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revert' })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Gene symbol'), {
      target: { value: 'BRCA2' },
    })

    expect(onChange).toHaveBeenCalledWith('BRCA2')
  })

  it('parses number fields to numeric values before notifying the parent', () => {
    const { onChange } = renderFieldRow({
      field: createField({
        field_key: 'confidence',
        label: 'Confidence',
        field_type: 'number',
        value: 0.8,
        seed_value: 0.8,
      }),
      value: 0.8,
    })

    fireEvent.change(screen.getByLabelText('Confidence'), {
      target: { value: '0.42' },
    })

    expect(onChange).toHaveBeenCalledWith(0.42)
  })

  it('supports adapter-owned custom input renderers', () => {
    const onChange = vi.fn()

    renderFieldRow({
      onChange,
      renderInput: ({ onChange: handleChange }) => (
        <button onClick={() => handleChange('adapter-value')} type="button">
          Use adapter input
        </button>
      ),
    })

    fireEvent.click(screen.getByRole('button', { name: 'Use adapter input' }))

    expect(onChange).toHaveBeenCalledWith('adapter-value')
  })
})

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
    expect(screen.getByTestId('field-row-gene_symbol')).toHaveAttribute(
      'data-field-path',
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

  it('parses integer fields to integer values before notifying the parent', () => {
    const { onChange } = renderFieldRow({
      field: createField({
        field_key: 'reference_id',
        label: 'Reference ID',
        field_type: 'integer',
        value: 101,
        seed_value: 101,
      }),
      value: 101,
    })

    fireEvent.change(screen.getByLabelText('Reference ID'), {
      target: { value: '202' },
    })

    expect(onChange).toHaveBeenCalledWith(202)
  })

  it('keeps invalid integer input as text for backend validation', () => {
    const { onChange } = renderFieldRow({
      field: createField({
        field_key: 'reference_id',
        label: 'Reference ID',
        field_type: 'integer',
        value: 101,
        seed_value: 101,
      }),
      value: 101,
    })

    fireEvent.change(screen.getByLabelText('Reference ID'), {
      target: { value: '202.5' },
    })

    expect(onChange).toHaveBeenCalledWith('202.5')
  })

  it('renders object and array fields as JSON editors', () => {
    renderFieldRow({
      field: createField({
        field_key: 'context',
        label: 'Context',
        field_type: 'array',
        value: [{ label: 'probe A' }],
        seed_value: [{ label: 'probe A' }],
      }),
      value: [{ label: 'probe A' }],
    })

    expect(screen.getByLabelText('Context')).toHaveValue(
      JSON.stringify([{ label: 'probe A' }], null, 2),
    )
  })

  it('renders render_as chip fields as chips instead of JSON editors', () => {
    renderFieldRow({
      field: createField({
        field_key: 'evidence_code_curies',
        label: 'Evidence code CURIEs',
        field_type: 'array',
        value: ['ECO:0000033', 'ECO:0000314'],
        seed_value: ['ECO:0000033', 'ECO:0000314'],
        read_only: true,
        metadata: {
          field_metadata: {
            render_as: 'chip',
          },
        },
      }),
      value: ['ECO:0000033', 'ECO:0000314'],
    })

    expect(screen.getByText('ECO:0000033')).toBeInTheDocument()
    expect(screen.getByText('ECO:0000314')).toBeInTheDocument()
    expect(screen.queryByLabelText('Evidence code CURIEs')).not.toBeInTheDocument()
  })

  it('renders read-only note arrays as readable note items', () => {
    renderFieldRow({
      field: createField({
        field_key: 'identity_resolution_notes',
        label: 'Identity resolution notes',
        field_type: 'array',
        value: [
          'Paper context is C. elegans.',
          'Figure 6 reports GFP::TLN-1 effects.',
        ],
        seed_value: [
          'Paper context is C. elegans.',
          'Figure 6 reports GFP::TLN-1 effects.',
        ],
        read_only: true,
        metadata: {
          field_metadata: {
            render_as: 'notes',
          },
        },
      }),
    })

    expect(screen.getByText('Paper context is C. elegans.')).toBeInTheDocument()
    expect(screen.getByText('Figure 6 reports GFP::TLN-1 effects.')).toBeInTheDocument()
    expect(screen.queryByText('[')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Identity resolution notes')).not.toBeInTheDocument()
  })

  it('keeps render_as fields editable when read_only is false', () => {
    const { onChange } = renderFieldRow({
      field: createField({
        field_key: 'disease_annotation_object.curie',
        label: 'Disease term CURIE',
        value: 'DOID:0050200',
        seed_value: 'DOID:0050200',
        metadata: {
          field_metadata: {
            render_as: 'curie-chip',
          },
        },
      }),
      value: 'DOID:0050200',
    })

    fireEvent.change(screen.getByLabelText('Disease term CURIE'), {
      target: { value: 'DOID:1234567' },
    })

    expect(onChange).toHaveBeenCalledWith('DOID:1234567')
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

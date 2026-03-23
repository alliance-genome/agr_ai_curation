import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import type { CurationDraftField, FieldValidationStatus } from '../types'
import theme from '@/theme'
import ValidationBadge from './ValidationBadge'

function buildField(
  status: FieldValidationStatus,
  overrides: Partial<CurationDraftField> = {},
): CurationDraftField {
  return {
    field_key: 'field_a',
    label: 'Field A',
    value: 'Example value',
    seed_value: 'Example value',
    field_type: 'string',
    group_key: 'primary',
    group_label: 'Primary',
    order: 0,
    required: true,
    read_only: false,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: {
      status,
      resolver: 'deterministic_structural_validation',
      candidate_matches: [],
      warnings: [],
    },
    metadata: {},
    ...overrides,
  }
}

function renderBadge(field: CurationDraftField) {
  return render(
    <ThemeProvider theme={theme}>
      <ValidationBadge field={field} />
    </ThemeProvider>,
  )
}

describe('ValidationBadge', () => {
  it.each([
    ['validated', 'Validated'],
    ['ambiguous', 'Ambiguous'],
    ['not_found', 'Not found'],
    ['invalid_format', 'Invalid format'],
    ['conflict', 'Conflict'],
    ['skipped', 'Skipped'],
    ['overridden', 'Overridden'],
  ] as const)('renders the %s status label', (status, label) => {
    renderBadge(buildField(status))

    expect(screen.getByLabelText(`Field A validation ${label.toLowerCase()}`)).toBeInTheDocument()
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('shows dirty and stale indicators alongside the validation status', () => {
    renderBadge(
      buildField('overridden', {
        dirty: true,
        stale_validation: true,
      }),
    )

    expect(screen.getByLabelText('Field A dirty indicator')).toBeInTheDocument()
    expect(screen.getByText('Edited')).toBeInTheDocument()
    expect(screen.getByText('Overridden')).toBeInTheDocument()
    expect(screen.getByText('Refreshing')).toBeInTheDocument()
  })

  it('renders nothing when a field has no validation or dirty state', () => {
    const { container } = renderBadge(
      buildField('skipped', {
        dirty: false,
        stale_validation: false,
        validation_result: null,
      }),
    )

    expect(container).toBeEmptyDOMElement()
  })
})

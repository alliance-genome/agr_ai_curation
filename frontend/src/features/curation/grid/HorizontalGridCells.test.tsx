import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type {
  CurationDraftField,
  DomainEnvelopeReviewFieldResolution,
  DomainEnvelopeReviewResolvedValue,
} from '@/features/curation/types'
import { HorizontalGridFieldCellContent } from './HorizontalGridCells'
import type { HorizontalGridFieldCell } from './horizontalGridModel'

const emptyValidation = {
  summaries: [],
  statuses: [],
  summaryCount: 0,
  findingCount: 0,
  openFindingCount: 0,
}

function resolvedValue(
  overrides: Partial<DomainEnvelopeReviewResolvedValue> = {},
): DomainEnvelopeReviewResolvedValue {
  return {
    value_path: 'site',
    display_text: 'gut (ONT:0000101)',
    mention: 'structures near the gut',
    resolution_state: 'resolved',
    lookup_outcome: 'matched',
    lookup_result: 'Matched',
    validator_explanation: 'Exact synonym match.',
    validator_curator_message: null,
    ...overrides,
  }
}

function draftField(dirty = false): CurationDraftField {
  return {
    field_key: 'site',
    label: 'Site',
    value: null,
    seed_value: null,
    field_type: 'object',
    order: 0,
    required: false,
    read_only: false,
    dirty,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: { source_field_path: 'site' },
  }
}

function cell(
  displayText: string | null,
  resolution: DomainEnvelopeReviewFieldResolution | null,
  dirty = false,
): HorizontalGridFieldCell {
  return {
    columnKey: 'field:site',
    fieldKey: 'site',
    fieldPath: 'site',
    hasField: true,
    value: null,
    displayText,
    resolution,
    resolutionDetails: resolution?.values ?? [],
    resolutionLinesId: resolution ? 'lines-site' : null,
    resolutionDescribedBy: resolution ? ['lines-site'] : [],
    required: false,
    readOnly: false,
    dirty,
    staleValidation: false,
    state: 'resolved',
    fieldValidation: null,
    evidence: [],
    validation: emptyValidation,
    extractorComparison: null,
  }
}

function renderCell(gridCell: HorizontalGridFieldCell) {
  return render(
    <HorizontalGridFieldCellContent
      active={false}
      cell={gridCell}
      field={draftField(gridCell.dirty === true)}
      onSelect={vi.fn()}
      state={gridCell.state}
    />,
  )
}

function slot(container: HTMLElement, name: string): HTMLElement | null {
  return container.querySelector(`[data-slot="${name}"]`)
}

describe('HorizontalGridFieldCellContent', () => {
  it('shows the validated value, with the paper wording and lookup result on labelled lines', () => {
    const { container } = renderCell(cell('gut (ONT:0000101)', {
      display_text: 'gut (ONT:0000101)',
      values: [resolvedValue()],
    }))

    expect(slot(container, 'field-value')).toHaveTextContent(/^gut \(ONT:0000101\)$/)
    expect(slot(container, 'field-paper-wording')).toHaveTextContent(
      'Paper wording: structures near the gut',
    )
    const lookup = slot(container, 'field-lookup-result')!
    expect(lookup).toHaveTextContent('Lookup result: Matched. Validator explanation: Exact synonym match.')
    expect(slot(container, 'field-validator-words')).toHaveTextContent('Validator explanation: Exact synonym match.')
    expect(lookup).not.toHaveAttribute('tabindex')
    expect(lookup).not.toHaveAttribute('aria-label')
    const button = screen.getByRole('button')
    expect(button).toHaveAccessibleName(/^Select Site for site: gut \(ONT:0000101\)\./)
    expect(button).toHaveAccessibleDescription(
      /^Paper wording:\s*structures near the gut\s+Lookup result:\s*Matched\. Validator explanation: Exact synonym match\.$/,
    )
  })

  it('shows UNRESOLVED as the value and keeps the paper wording out of it', () => {
    const unresolved = resolvedValue({
      display_text: 'UNRESOLVED',
      mention: 'structures near the residual body',
      resolution_state: 'unresolved',
      lookup_outcome: 'not_found',
      lookup_result: 'Not found',
      validator_explanation: 'No term matched the wording.',
      validator_curator_message: 'Pick a term by hand.',
    })
    const { container } = renderCell(cell('UNRESOLVED', { display_text: 'UNRESOLVED', values: [unresolved] }))

    const value = slot(container, 'field-value')!
    expect(value).toHaveTextContent(/^UNRESOLVED$/)
    expect(within(value).queryByText(/residual body/)).not.toBeInTheDocument()
    expect(slot(container, 'field-paper-wording')).toHaveTextContent(
      'Paper wording: structures near the residual body',
    )
    const lookup = slot(container, 'field-lookup-result')!
    expect(lookup).toHaveTextContent(/^Lookup result: Not found\./)
    expect(slot(container, 'field-validator-words')).toHaveTextContent(
      'Validator explanation: No term matched the wording.. Validator message: Pick a term by hand.',
    )
    expect(lookup).toHaveAttribute('title', [
      'Value: UNRESOLVED',
      'Paper wording: structures near the residual body',
      'Lookup result: Not found',
      'Validator explanation: No term matched the wording.',
      'Validator message: Pick a term by hand.',
    ].join('\n'))
    expect(screen.getByRole('button')).toHaveAccessibleName(/^Select Site for site: UNRESOLVED\./)
  })

  it('names an unreadable stored value and its issue', () => {
    const unreadable = resolvedValue({
      display_text: 'UNRESOLVED',
      mention: 'gut',
      resolution_state: 'unresolved',
      lookup_outcome: 'invalid_schema',
      lookup_result: 'Invalid validator output',
      validator_explanation: null,
      issue: 'This stored value could not be read; please re-run validation or contact the '
        + 'AI Curation developers.',
    })
    const { container } = renderCell(cell('UNRESOLVED', { display_text: 'UNRESOLVED', values: [unreadable] }))

    expect(slot(container, 'field-value')).toHaveTextContent(/^UNRESOLVED$/)
    expect(slot(container, 'field-lookup-result')).toHaveTextContent(
      'Lookup result: Invalid validator output. This stored value could not be read; '
      + 'please re-run validation or contact the AI Curation developers.',
    )
  })

  it('labels a legacy value as legacy, unverified', () => {
    const legacy = resolvedValue({
      display_text: 'UNRESOLVED',
      mention: 'gut (ONT:0000101) (legacy, unverified)',
      resolution_state: 'unresolved',
      lookup_outcome: 'legacy_unverified',
      lookup_result: 'Legacy, unverified',
      validator_explanation: 'Recorded before validation tracking; not verified.',
    })
    const { container } = renderCell(cell('UNRESOLVED', { display_text: 'UNRESOLVED', values: [legacy] }))

    expect(slot(container, 'field-value')).toHaveTextContent(/^UNRESOLVED$/)
    expect(slot(container, 'field-paper-wording')).toHaveTextContent(
      'Paper wording: gut (ONT:0000101) (legacy, unverified)',
    )
    expect(slot(container, 'field-lookup-result')).toHaveTextContent('Lookup result: Legacy, unverified')
  })

  it('reads each value of a list on its own', () => {
    const values = [
      resolvedValue({ value_path: 'codes[0]', display_text: 'ONT:0000315', mention: 'IMP', validator_explanation: null }),
      resolvedValue({
        value_path: 'codes[1]',
        display_text: 'UNRESOLVED',
        mention: 'IGI',
        resolution_state: 'unresolved',
        lookup_outcome: 'ambiguous',
        lookup_result: 'Several matches',
        validator_explanation: 'Two codes fit.',
      }),
    ]
    const { container } = renderCell(cell('ONT:0000315 | UNRESOLVED', {
      display_text: 'ONT:0000315 | UNRESOLVED',
      values,
    }))

    expect(slot(container, 'field-paper-wording')).toHaveTextContent('Paper wording: IMP; IGI')
    const lookup = slot(container, 'field-lookup-result')!
    expect(lookup).toHaveTextContent(/^Lookup result: Matched; Several matches\./)
    expect(lookup).toHaveAttribute('title', [
      'Value: ONT:0000315\nPaper wording: IMP\nLookup result: Matched',
      'Value: UNRESOLVED\nPaper wording: IGI\nLookup result: Several matches\n'
        + 'Validator explanation: Two codes fit.',
    ].join('\n\n'))
  })

  it('describes a secondary cell by the lines on the cell that carries its details', () => {
    const gridCell = cell('ONT:0000101', { display_text: 'ONT:0000101', values: [resolvedValue()] })
    gridCell.resolutionDetails = []
    gridCell.resolutionLinesId = null
    gridCell.resolutionDescribedBy = ['lines-owner']
    const { container } = render(
      <>
        <div id="lines-owner">Paper wording: structures near the gut Lookup result: Matched</div>
        <HorizontalGridFieldCellContent
          active={false}
          cell={gridCell}
          field={draftField()}
          onSelect={vi.fn()}
          state={gridCell.state}
        />
      </>,
    )

    expect(slot(container, 'field-value')).toHaveTextContent(/^ONT:0000101$/)
    expect(slot(container, 'field-resolution')).toBeNull()
    expect(screen.getByRole('button')).toHaveAccessibleDescription(
      'Paper wording: structures near the gut Lookup result: Matched',
    )
  })

  it("shows a curator's edit with the paper wording but not the seeded lookup result", () => {
    const { container } = renderCell(cell('midgut', {
      display_text: 'UNRESOLVED',
      values: [resolvedValue({
        display_text: 'UNRESOLVED',
        resolution_state: 'unresolved',
        lookup_outcome: 'not_found',
        lookup_result: 'Not found',
      })],
    }, true))

    expect(slot(container, 'field-value')).toHaveTextContent(/^midgut$/)
    expect(slot(container, 'field-paper-wording')).toHaveTextContent('Paper wording: structures near the gut')
    expect(slot(container, 'field-lookup-result')).toBeNull()
  })

  it('adds no resolution lines to a field without validated values', () => {
    const { container } = renderCell(cell('plain text', null))

    expect(slot(container, 'field-value')).toHaveTextContent(/^plain text$/)
    expect(slot(container, 'field-paper-wording')).toBeNull()
    expect(slot(container, 'field-lookup-result')).toBeNull()
  })
})

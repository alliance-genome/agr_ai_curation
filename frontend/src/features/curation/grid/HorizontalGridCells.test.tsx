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
    override_disagreements: [],
    identity_field_paths: ['site.curie', 'site.name'],
    id_key: 'curie',
    label_key: 'name',
    validated_keys: [],
    stored_identity: {},
    container_protected: false,
    ...overrides,
  }
}

function draftField(): CurationDraftField {
  return {
    field_key: 'site',
    label: 'Site',
    value: null,
    seed_value: null,
    field_type: 'object',
    order: 0,
    required: false,
    read_only: false,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: { source_field_path: 'site' },
  }
}

function cell(
  displayText: string | null,
  resolution: DomainEnvelopeReviewFieldResolution | null,
): HorizontalGridFieldCell {
  const overridden = resolution?.values.filter((value) => value.curator_override) ?? []
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
    curatorOverride: overridden.length > 0,
    overrideDisagreements: overridden.flatMap((value) => value.override_disagreements),
    overrideTarget: overridden[0] ?? null,
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
      field={draftField()}
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

  it('marks a curator override and says who made it and when', () => {
    const overridden = resolvedValue({
      display_text: 'midgut (ONT:0000555)',
      mention: 'gut lining',
      lookup_outcome: 'curator_override',
      lookup_result: 'Curator override',
      validator_explanation: null,
      curator_override: { actor_id: 'curator-1', at: '2026-09-23T20:00:00+00:00' },
    })
    const { container } = renderCell(cell('ONT:0000555', { display_text: 'ONT:0000555', values: [overridden] }))

    expect(slot(container, 'field-value')).toHaveTextContent(/^ONT:0000555$/)
    expect(slot(container, 'field-override-badge')).toHaveTextContent('Curator override')
    expect(slot(container, 'field-lookup-result')).toHaveTextContent(
      'Lookup result: Curator override. Curator override by curator-1 on 2026-09-23 20:00 UTC',
    )
    expect(slot(container, 'field-override-disagreement')).toBeNull()
    const button = screen.getByRole('button')
    expect(button).toHaveAccessibleName(/^Select Site for site: ONT:0000555, curator override\. Curator validated\.$/)
    expect(button).toHaveAccessibleDescription(/Curator override by curator-1 on 2026-09-23 20:00 UTC/)
  })

  it('shows an open validator disagreement with the override in full', () => {
    const message = 'Validator disagrees with the curator override: its lookup result is Not found.'
    const overridden = resolvedValue({
      display_text: 'midgut (ONT:0000555)',
      lookup_outcome: 'curator_override',
      lookup_result: 'Curator override',
      curator_override: { actor_id: 'curator-1', at: '2026-09-23T20:00:00+00:00' },
      override_disagreements: [message],
    })
    const gridCell = cell('ONT:0000555', { display_text: 'ONT:0000555', values: [overridden] })
    gridCell.state = 'needs-review'
    const { container } = renderCell(gridCell)

    expect(slot(container, 'field-override-badge')).toHaveTextContent('Curator override')
    expect(slot(container, 'field-override-disagreement')).toHaveTextContent(message)
    // Shown once in full, not repeated in the screen-reader text of the lookup line.
    expect(slot(container, 'field-validator-words')).not.toHaveTextContent('Validator disagrees')
    expect(screen.getByRole('img', { name: `Needs review: ${message}` })).toBeInTheDocument()
    expect(screen.getByRole('button')).toHaveAccessibleDescription(new RegExp(message.replace(/[.:]/g, '\\$&')))
  })

  it('adds no resolution lines to a field without validated values', () => {
    const { container } = renderCell(cell('plain text', null))

    expect(slot(container, 'field-value')).toHaveTextContent(/^plain text$/)
    expect(slot(container, 'field-paper-wording')).toBeNull()
    expect(slot(container, 'field-lookup-result')).toBeNull()
  })
})

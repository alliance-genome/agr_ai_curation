import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type { CurationCandidate, DomainEnvelopeReviewRow } from '@/features/curation/types'
import theme from '@/theme'
import ObjectSelectorStrip from './ObjectSelectorStrip'
import type { WorkspaceEnvelopeObjectReviewRow } from './envelopeObjectReviewRows'

function candidate(
  id: string,
  status: CurationCandidate['status'] = 'pending',
): CurationCandidate {
  return {
    candidate_id: id,
    session_id: 'session-1',
    source: 'extracted',
    status,
    order: 0,
    adapter_key: 'domain-pack',
    display_label: id,
    projection_ref: {
      envelope_id: 'envelope-1',
      object_id: `${id}-object`,
      envelope_revision: 1,
    },
    draft: {
      draft_id: `draft-${id}`,
      candidate_id: id,
      adapter_key: 'domain-pack',
      version: 1,
      fields: [],
      created_at: '2026-05-10T12:00:00Z',
      updated_at: '2026-05-10T12:00:00Z',
      metadata: {},
    },
    evidence_anchors: [],
    created_at: '2026-05-10T12:00:00Z',
    updated_at: '2026-05-10T12:00:00Z',
    metadata: {},
  }
}

function reviewRow(id: string, label: string, objectType: string): DomainEnvelopeReviewRow {
  return {
    envelope_id: 'envelope-1',
    object_id: `${id}-object`,
    envelope_revision: 1,
    domain_pack_id: 'fixture.domain',
    domain_pack_version: '0.7.0',
    object_type: objectType,
    object_role: 'curatable_unit',
    status: 'draft',
    validation_state: 'unresolved',
    projection_type: 'workspace_review_row',
    projection_key: `${id}-object`,
    display_label: label,
    secondary_label: null,
    summary_fields: [],
    schema_provider: null,
    schema_ref: {},
    object_model_ref: {},
    model_field_ref: {},
    metadata: {},
  }
}

function selectorRow(
  id: string,
  label: string,
  objectType: string,
  status: CurationCandidate['status'] = 'pending',
): WorkspaceEnvelopeObjectReviewRow {
  const rowCandidate = candidate(id, status)
  return {
    candidate: rowCandidate,
    projectionRef: rowCandidate.projection_ref!,
    reviewRow: reviewRow(id, label, objectType),
    evidenceAnchors: [],
    validationSummaries: [],
  }
}

function renderStrip(
  rows: WorkspaceEnvelopeObjectReviewRow[],
  activeCandidateId = 'b',
  onSelect = vi.fn(),
) {
  render(
    <ThemeProvider theme={theme}>
      <ObjectSelectorStrip
        activeCandidateId={activeCandidateId}
        onSelect={onSelect}
        rows={rows}
      />
    </ThemeProvider>,
  )

  return onSelect
}

describe('ObjectSelectorStrip', () => {
  it('shows position, object identity, and calls onSelect from the jump menu', async () => {
    const user = userEvent.setup()
    const onSelect = renderStrip([
      selectorRow('a', 'Object A', 'GeneDiseaseAnnotation', 'accepted'),
      selectorRow('b', 'Object B', 'GeneDiseaseAnnotation'),
      selectorRow('c', 'Object C', 'AlleleDiseaseAnnotation', 'rejected'),
    ])

    expect(screen.getByText('2 of 3')).toBeInTheDocument()
    expect(screen.getByText('Object B')).toBeInTheDocument()
    expect(screen.getByText('Gene Disease Annotation')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /all objects/i }))
    await user.click(screen.getByRole('option', { name: /Object C/i }))

    expect(onSelect).toHaveBeenCalledWith('c')
  })

  it('moves to adjacent objects with previous and next controls', async () => {
    const user = userEvent.setup()
    const onSelect = renderStrip([
      selectorRow('a', 'Object A', 'GeneDiseaseAnnotation'),
      selectorRow('b', 'Object B', 'GeneDiseaseAnnotation'),
      selectorRow('c', 'Object C', 'AlleleDiseaseAnnotation'),
    ])

    await user.click(screen.getByRole('button', { name: 'Previous object' }))
    await user.click(screen.getByRole('button', { name: 'Next object' }))

    expect(onSelect).toHaveBeenNthCalledWith(1, 'a')
    expect(onSelect).toHaveBeenNthCalledWith(2, 'c')
  })
})

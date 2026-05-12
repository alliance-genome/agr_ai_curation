import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type {
  CurationCandidate,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeReviewRow,
} from '@/features/curation/types'
import theme from '@/theme'
import EnvelopeObjectReviewTable, {
  formatProjectedSummaryValue,
} from './EnvelopeObjectReviewTable'
import type { WorkspaceEnvelopeObjectReviewRow } from './envelopeObjectReviewRows'

function buildCandidate(): CurationCandidate {
  return {
    candidate_id: 'candidate-tmem67',
    session_id: 'session-1',
    source: 'extracted',
    status: 'pending',
    order: 0,
    adapter_key: 'domain-pack',
    projection_ref: {
      envelope_id: 'tmem67-envelope',
      object_id: 'tmem67-gene-object',
      envelope_revision: 4,
    },
    draft: {
      draft_id: 'draft-tmem67',
      candidate_id: 'candidate-tmem67',
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

function buildReviewRow(overrides: Partial<DomainEnvelopeReviewRow> = {}): DomainEnvelopeReviewRow {
  return {
    envelope_id: 'tmem67-envelope',
    object_id: 'tmem67-gene-object',
    envelope_revision: 4,
    domain_pack_id: 'fixture.domain',
    domain_pack_version: '0.7.0',
    object_type: 'GeneAssertion',
    object_role: 'curatable_unit',
    status: 'draft',
    validation_state: 'unresolved',
    projection_type: 'workspace_review_row',
    projection_key: 'tmem67-gene-object',
    display_label: 'TMEM67',
    secondary_label: null,
    summary_fields: [
      {
        field_path: 'gene.symbol',
        label: 'Symbol',
        value: 'TMEM67',
        field_type: 'string',
        metadata: {},
      },
    ],
    schema_provider: null,
    schema_ref: {},
    object_model_ref: {},
    model_field_ref: {},
    metadata: {},
    ...overrides,
  }
}

function buildEvidenceAnchor(
  overrides: Partial<DomainEnvelopeEvidenceAnchorProjection> = {},
): DomainEnvelopeEvidenceAnchorProjection {
  return {
    anchor_id: 'anchor-1',
    evidence_record_id: 'evidence-1',
    envelope_id: 'tmem67-envelope',
    object_id: 'tmem67-gene-object',
    object_type: 'GeneAssertion',
    field_path: 'gene.symbol',
    envelope_revision: 4,
    document_id: 'document-1',
    quote: 'Projected evidence text.',
    page_number: 3,
    page_label: '3',
    chunk_id: 'chunk-1',
    chunk_ids: ['chunk-1'],
    section_title: 'Results',
    subsection_title: null,
    figure_reference: null,
    table_reference: null,
    source_id: null,
    source_title: null,
    source_url: null,
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      snippet_text: 'Projected evidence text.',
      sentence_text: 'Projected evidence text.',
      chunk_ids: ['chunk-1'],
    },
    metadata: {},
    ...overrides,
  }
}

function buildRow(
  overrides: Partial<WorkspaceEnvelopeObjectReviewRow> = {},
): WorkspaceEnvelopeObjectReviewRow {
  const candidate = buildCandidate()

  return {
    candidate,
    projectionRef: {
      envelope_id: 'tmem67-envelope',
      object_id: 'tmem67-gene-object',
      envelope_revision: 4,
    },
    reviewRow: buildReviewRow(),
    evidenceAnchors: [],
    validationSummaries: [],
    ...overrides,
  }
}

function renderTable(rows: WorkspaceEnvelopeObjectReviewRow[]) {
  return render(
    <ThemeProvider theme={theme}>
      <EnvelopeObjectReviewTable
        errorMessage={null}
        isLoading={false}
        onAcceptRow={vi.fn()}
        onRejectRow={vi.fn()}
        onRetry={vi.fn()}
        onSelectRow={vi.fn()}
        rows={rows}
        selectedCandidateId="candidate-tmem67"
      />
    </ThemeProvider>,
  )
}

describe('EnvelopeObjectReviewTable', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders unexpected validation states without exposing raw error copy', () => {
    renderTable([
      buildRow({
        reviewRow: buildReviewRow({
          validation_state: 'schema_provider_timeout',
        }),
      }),
    ])

    expect(screen.getByText('Schema Provider Timeout')).toBeInTheDocument()
    expect(screen.queryByText(/Unknown validation state/i)).not.toBeInTheDocument()
  })

  it('uses a readable object fallback when a review-row display label is missing', () => {
    renderTable([
      buildRow({
        reviewRow: buildReviewRow({
          display_label: '   ',
        }),
      }),
    ])

    expect(screen.getAllByText('Tmem67 gene object').length).toBeGreaterThan(0)
    expect(
      screen.getByRole('button', { name: 'Accept Tmem67 gene object' }),
    ).toBeInTheDocument()
  })

  it('renders a distinct empty state when an evidence projection carries no text', () => {
    renderTable([
      buildRow({
        evidenceAnchors: [
          buildEvidenceAnchor({
            quote: null,
            anchor: {
              anchor_kind: 'snippet',
              locator_quality: 'unresolved',
              supports_decision: 'neutral',
              snippet_text: null,
              sentence_text: null,
              chunk_ids: [],
            },
          }),
        ],
      }),
    ])

    expect(screen.getByText('No evidence text is available for this anchor.')).toBeInTheDocument()
  })

  it('propagates projected summary serialization failures', () => {
    const circularValue: Record<string, unknown> = {}
    circularValue.self = circularValue

    expect(() => formatProjectedSummaryValue(circularValue)).toThrow(/circular/i)
  })
})

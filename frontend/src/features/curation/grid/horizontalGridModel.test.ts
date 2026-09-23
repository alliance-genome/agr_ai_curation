import { describe, expect, it } from 'vitest'

import type {
  CurationCandidate,
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeReviewFieldResolution,
  DomainEnvelopeReviewResolvedValue,
  DomainEnvelopeReviewRow,
  DomainEnvelopeValidationStatus,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'
import type { WorkspaceEnvelopeObjectReviewRow } from '@/features/curation/workspace/envelopeObjectReviewRows'
import {
  buildHorizontalGridModel,
  HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
} from './horizontalGridModel'

function draftField({
  fieldKey,
  fieldPath = fieldKey,
  label,
  order,
  value = null,
  required = false,
  readOnly = false,
  renderAs,
  groupKey = 'details',
  groupLabel = 'Details',
}: {
  fieldKey: string
  fieldPath?: string
  label: string
  order: number
  value?: unknown
  required?: boolean
  readOnly?: boolean
  renderAs?: string
  groupKey?: string
  groupLabel?: string
}): CurationDraftField {
  return {
    field_key: fieldKey,
    label,
    value,
    seed_value: value,
    field_type: 'string',
    group_key: groupKey,
    group_label: groupLabel,
    order,
    required,
    read_only: readOnly,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: {
      source_field_path: fieldPath,
      ...(renderAs ? { field_metadata: { render_as: renderAs } } : {}),
    },
  }
}

function candidate({
  id,
  objectId,
  order,
  fields,
  adapterKey = 'domain-pack',
  displayLabel = null,
}: {
  id: string
  objectId: string
  order: number
  fields: CurationDraftField[]
  adapterKey?: string
  displayLabel?: string | null
}): CurationCandidate {
  return {
    candidate_id: id,
    session_id: 'session-1',
    source: 'extracted',
    status: 'pending',
    order,
    adapter_key: adapterKey,
    display_label: displayLabel,
    projection_ref: {
      envelope_id: 'envelope-1',
      object_id: objectId,
      envelope_revision: 4,
    },
    draft: {
      draft_id: `draft-${id}`,
      candidate_id: id,
      adapter_key: adapterKey,
      version: 1,
      fields,
      created_at: '2026-08-11T12:00:00Z',
      updated_at: '2026-08-11T12:00:00Z',
      metadata: {},
    },
    evidence_anchors: [],
    created_at: '2026-08-11T12:00:00Z',
    updated_at: '2026-08-11T12:00:00Z',
    metadata: { source: id },
  }
}

function reviewRow(
  objectId: string,
  displayLabel: string,
  secondaryLabel: string | null = null,
): DomainEnvelopeReviewRow {
  return {
    envelope_id: 'envelope-1',
    object_id: objectId,
    envelope_revision: 4,
    domain_pack_id: 'fixture.horizontal_grid',
    domain_pack_version: '1.0.0',
    object_type: 'FixtureObject',
    object_role: 'curatable_unit',
    status: 'draft',
    validation_state: 'unresolved',
    projection_type: 'workspace_review_row',
    projection_key: objectId,
    display_label: displayLabel,
    secondary_label: secondaryLabel,
    summary_fields: [],
    schema_provider: null,
    schema_ref: {},
    object_model_ref: {},
    model_field_ref: {},
    metadata: { context_source: objectId },
  }
}

function workspaceRow({
  candidate: rowCandidate,
  row = reviewRow(
    rowCandidate.projection_ref!.object_id,
    `Review ${rowCandidate.projection_ref!.object_id}`,
  ),
  evidence = [],
  validation = [],
}: {
  candidate: CurationCandidate
  row?: DomainEnvelopeReviewRow | null
  evidence?: DomainEnvelopeEvidenceAnchorProjection[]
  validation?: DomainEnvelopeValidationSummaryProjection[]
}): WorkspaceEnvelopeObjectReviewRow {
  return {
    candidate: rowCandidate,
    projectionRef: rowCandidate.projection_ref!,
    reviewRow: row,
    evidenceAnchors: evidence,
    validationSummaries: validation,
  }
}

function modelForRows(rows: WorkspaceEnvelopeObjectReviewRow[]) {
  return buildHorizontalGridModel({
    candidates: rows.map((row) => row.candidate),
    envelopeReviewRows: rows,
  })
}

function evidenceProjection({
  id,
  fieldPath,
}: {
  id: string
  fieldPath: string | null
}): DomainEnvelopeEvidenceAnchorProjection {
  return {
    anchor_id: id,
    evidence_record_id: `record-${id}`,
    envelope_id: 'envelope-1',
    object_id: 'object-a',
    object_type: 'FixtureObject',
    field_path: fieldPath,
    envelope_revision: 4,
    document_id: 'document-1',
    quote: `Evidence ${id}`,
    page_number: 2,
    page_label: null,
    chunk_id: `chunk-${id}`,
    chunk_ids: [`chunk-${id}`],
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
      snippet_text: `Evidence ${id}`,
      sentence_text: `Evidence ${id}`,
      viewer_search_text: `Evidence ${id}`,
      page_number: 2,
      section_title: 'Results',
      chunk_ids: [`chunk-${id}`],
    },
    metadata: {},
  }
}

function validationProjection({
  id,
  fieldPath,
  status,
  findings,
  openFindings,
}: {
  id: string
  fieldPath: string | null
  status: DomainEnvelopeValidationStatus
  findings: number
  openFindings: number
}): DomainEnvelopeValidationSummaryProjection {
  return {
    summary_id: id,
    envelope_id: 'envelope-1',
    object_id: 'object-a',
    object_type: 'FixtureObject',
    field_path: fieldPath,
    envelope_revision: 4,
    status,
    highest_severity: openFindings > 0 ? 'warning' : null,
    finding_count: findings,
    open_finding_count: openFindings,
    finding_ids: [],
    codes: [],
    messages: [],
    findings: [],
  }
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
    stored_value: {},
    ...overrides,
  }
}

const UNRESOLVED_SITE: DomainEnvelopeReviewResolvedValue = resolvedValue({
  display_text: 'UNRESOLVED',
  mention: 'structures near the residual body',
  resolution_state: 'unresolved',
  lookup_outcome: 'not_found',
  lookup_result: 'Not found',
  validator_explanation: 'No term matched the wording.',
})

function reviewRowWithFields(
  objectId: string,
  fields: Array<{ path: string; resolution: DomainEnvelopeReviewFieldResolution | null }>,
  { workspace = true }: { workspace?: boolean } = {},
): DomainEnvelopeReviewRow {
  const rowFields = fields.map(({ path, resolution }, index) => ({
    field_path: path,
    label: path,
    value: null,
    field_type: 'string',
    metadata: { workspace_order: index },
    resolution,
  }))
  const row = reviewRow(objectId, `Review ${objectId}`)
  return workspace
    ? { ...row, metadata: { ...row.metadata, workspace_fields: rowFields } }
    : { ...row, summary_fields: rowFields }
}

describe('buildHorizontalGridModel', () => {
  it('keeps gene supporting envelope context out of the curator decision grid', () => {
    const geneCandidate = candidate({
      id: 'candidate-gene',
      objectId: 'object-gene',
      order: 0,
      adapterKey: 'gene',
      fields: [
        draftField({
          fieldKey: 'gene-symbol',
          fieldPath: 'gene_symbol',
          label: 'Gene symbol',
          order: 0,
          value: 'abc',
          groupKey: 'identity',
          groupLabel: 'Gene identity',
        }),
        draftField({
          fieldKey: 'species',
          label: 'Species',
          order: 1,
          value: 'Example organism',
          readOnly: true,
          groupKey: 'identity',
          groupLabel: 'Gene identity',
        }),
        draftField({
          fieldKey: 'proposed-symbol',
          fieldPath: 'proposed_gene_symbol',
          label: 'Proposed gene symbol',
          order: 2,
          value: 'abc',
          readOnly: true,
          renderAs: 'divergence',
          groupKey: 'ai_proposal',
          groupLabel: 'AI proposal',
        }),
        draftField({
          fieldKey: 'section',
          label: 'Section',
          order: 3,
          value: 'Results',
          readOnly: true,
          groupKey: 'evidence_location',
          groupLabel: 'Evidence location',
        }),
        draftField({
          fieldKey: 'confidence',
          label: 'Confidence',
          order: 4,
          value: 'high',
          readOnly: true,
          groupKey: 'provenance',
          groupLabel: 'Provenance & notes',
        }),
      ],
    })
    geneCandidate.metadata = {
      ...geneCandidate.metadata,
      domain_pack_id: 'gene',
      object_type: 'gene_mention_evidence',
    }

    const model = modelForRows([workspaceRow({ candidate: geneCandidate })])

    expect(model.columns.map((column) => column.fieldPath)).toEqual([
      null,
      'gene_symbol',
      'species',
    ])
    // An unvalidated value reads UNRESOLVED; the extractor's proposal stays in
    // the comparison and never takes the validated value's place.
    expect(model.rows[0]!.cells[0]).toMatchObject({
      extractorComparison: { outcome: 'unresolved', value: 'abc' },
      value: 'abc',
      displayText: 'UNRESOLVED',
    })
  })

  it('shows the stored rationale on the row context instead of a decision column', () => {
    const fields = (value: unknown) => [
      draftField({ fieldKey: 'term', label: 'Term', order: 0, value: 'Term one' }),
      draftField({
        fieldKey: 'rationale',
        label: 'Rationale',
        order: 1,
        value,
        readOnly: true,
        groupKey: 'rationale',
        groupLabel: 'Rationale',
      }),
    ]
    const recorded = candidate({
      id: 'candidate-recorded',
      objectId: 'object-recorded',
      order: 0,
      fields: fields('  Knockdown removed the phenotype that the rescue restored.  '),
    })
    const notRecorded = candidate({
      id: 'candidate-not-recorded',
      objectId: 'object-not-recorded',
      order: 1,
      fields: fields(null),
    })
    const undeclared = candidate({
      id: 'candidate-undeclared',
      objectId: 'object-undeclared',
      order: 2,
      fields: [draftField({ fieldKey: 'term', label: 'Term', order: 0, value: 'Term two' })],
    })

    const model = modelForRows([
      workspaceRow({ candidate: recorded }),
      workspaceRow({ candidate: notRecorded }),
      workspaceRow({ candidate: undeclared }),
    ])

    expect(model.columns.map((column) => column.fieldPath)).toEqual([null, 'term'])
    expect(model.rows.map((row) => row.contextCell.value.rationale)).toEqual([
      { value: 'Knockdown removed the phenotype that the rescue restored.' },
      { value: null },
      null,
    ])
  })

  it('projects extractor proposals into their canonical field instead of peer columns', () => {
    const confirmedCandidate = candidate({
      id: 'candidate-confirmed',
      objectId: 'object-confirmed',
      order: 0,
      fields: [
        draftField({
          fieldKey: 'canonical-symbol',
          fieldPath: 'symbol',
          label: 'Symbol',
          order: 0,
          value: 'abc',
        }),
        draftField({
          fieldKey: 'extractor-symbol',
          fieldPath: 'proposed_symbol',
          label: 'Proposed symbol',
          order: 1,
          value: 'abc',
          readOnly: true,
          renderAs: 'divergence',
        }),
      ],
    })
    const differentCandidate = candidate({
      id: 'candidate-different',
      objectId: 'object-different',
      order: 1,
      fields: [
        draftField({
          fieldKey: 'canonical-symbol',
          fieldPath: 'symbol',
          label: 'Symbol',
          order: 0,
          value: 'abc',
        }),
        draftField({
          fieldKey: 'extractor-symbol',
          fieldPath: 'proposed_symbol',
          label: 'Proposed symbol',
          order: 1,
          value: 'abcd',
          readOnly: true,
          renderAs: 'divergence',
        }),
      ],
    })
    const unresolvedCandidate = candidate({
      id: 'candidate-unresolved',
      objectId: 'object-unresolved',
      order: 2,
      fields: [
        draftField({
          fieldKey: 'canonical-symbol',
          fieldPath: 'symbol',
          label: 'Symbol',
          order: 0,
          value: null,
        }),
        draftField({
          fieldKey: 'extractor-symbol',
          fieldPath: 'proposed_symbol',
          label: 'Proposed symbol',
          order: 1,
          value: 'abcd',
          readOnly: true,
          renderAs: 'divergence',
        }),
      ],
    })
    const overriddenCandidate = candidate({
      id: 'candidate-overridden',
      objectId: 'object-overridden',
      order: 3,
      fields: [
        draftField({
          fieldKey: 'canonical-symbol',
          fieldPath: 'symbol',
          label: 'Symbol',
          order: 0,
          value: 'curator-symbol',
        }),
        draftField({
          fieldKey: 'extractor-symbol',
          fieldPath: 'proposed_symbol',
          label: 'Proposed symbol',
          order: 1,
          value: 'extracted-symbol',
          readOnly: true,
          renderAs: 'divergence',
        }),
      ],
    })
    const emptyOverrideCandidate = candidate({
      id: 'candidate-empty-override',
      objectId: 'object-empty-override',
      order: 4,
      fields: [
        draftField({
          fieldKey: 'canonical-symbol',
          fieldPath: 'symbol',
          label: 'Symbol',
          order: 0,
          value: null,
        }),
        draftField({
          fieldKey: 'extractor-symbol',
          fieldPath: 'proposed_symbol',
          label: 'Proposed symbol',
          order: 1,
          value: 'extracted-symbol',
          readOnly: true,
          renderAs: 'divergence',
        }),
      ],
    })
    const staleCandidate = candidate({
      id: 'candidate-stale',
      objectId: 'object-stale',
      order: 5,
      fields: [
        {
          ...draftField({
            fieldKey: 'canonical-symbol',
            fieldPath: 'symbol',
            label: 'Symbol',
            order: 0,
            value: 'edited-symbol',
          }),
          stale_validation: true,
        },
        draftField({
          fieldKey: 'extractor-symbol',
          fieldPath: 'proposed_symbol',
          label: 'Proposed symbol',
          order: 1,
          value: 'extracted-symbol',
          readOnly: true,
          renderAs: 'divergence',
        }),
      ],
    })

    const model = modelForRows([
      workspaceRow({
        candidate: confirmedCandidate,
        validation: [validationProjection({
          id: 'confirmed-symbol',
          fieldPath: 'symbol',
          status: 'resolved',
          findings: 0,
          openFindings: 0,
        })],
      }),
      workspaceRow({
        candidate: differentCandidate,
        validation: [validationProjection({
          id: 'different-symbol',
          fieldPath: 'symbol',
          status: 'resolved',
          findings: 0,
          openFindings: 0,
        })],
      }),
      workspaceRow({ candidate: unresolvedCandidate }),
      workspaceRow({
        candidate: overriddenCandidate,
        validation: [validationProjection({
          id: 'overridden-symbol',
          fieldPath: 'symbol',
          status: 'waived',
          findings: 1,
          openFindings: 0,
        })],
      }),
      workspaceRow({
        candidate: emptyOverrideCandidate,
        validation: [validationProjection({
          id: 'empty-overridden-symbol',
          fieldPath: 'symbol',
          status: 'waived',
          findings: 1,
          openFindings: 0,
        })],
      }),
      workspaceRow({
        candidate: staleCandidate,
        validation: [validationProjection({
          id: 'stale-symbol',
          fieldPath: 'symbol',
          status: 'resolved',
          findings: 0,
          openFindings: 0,
        })],
      }),
    ])

    expect(model.columns.map((column) => column.fieldPath)).toEqual([null, 'symbol'])
    expect(model.rows[0]!.cells[0]).toMatchObject({
      value: 'abc',
      displayText: 'abc',
      extractorComparison: { outcome: 'confirmed', value: 'abc' },
    })
    expect(model.rows[1]!.cells[0]).toMatchObject({
      state: 'needs-review',
      value: 'abc',
      displayText: 'abc',
      extractorComparison: { outcome: 'different', value: 'abcd' },
    })
    // The canonical slot keeps the canonical value; an unresolved one reads
    // UNRESOLVED and the extractor's proposal stays in the comparison.
    expect(model.rows[2]!.cells[0]).toMatchObject({
      state: 'ai-unconfirmed',
      value: null,
      displayText: 'UNRESOLVED',
      extractorComparison: { outcome: 'unresolved', value: 'abcd' },
    })
    expect(model.rows[3]!.cells[0]).toMatchObject({
      state: 'resolved',
      value: 'curator-symbol',
      displayText: 'curator-symbol',
      extractorComparison: { outcome: 'overridden', value: 'extracted-symbol' },
    })
    expect(model.rows[4]!.cells[0]).toMatchObject({
      state: 'ai-unconfirmed',
      value: null,
      displayText: 'UNRESOLVED',
      extractorComparison: { outcome: 'unresolved', value: 'extracted-symbol' },
    })
    expect(model.rows[5]!.cells[0]).toMatchObject({
      state: 'needs-review',
      value: 'edited-symbol',
      displayText: 'UNRESOLVED',
      staleValidation: true,
      extractorComparison: { outcome: 'unresolved', value: 'extracted-symbol' },
    })
    for (const row of model.rows) {
      expect(row.cells[0]!.displayText).not.toBe('extracted-symbol')
      expect(row.cells[0]!.displayText).not.toBe('abcd')
    }
  })

  it('orders canonical-path columns and heterogeneous rows deterministically', () => {
    const firstCandidate = candidate({
      id: 'candidate-a',
      objectId: 'object-a',
      order: 20,
      fields: [
        draftField({
          fieldKey: 'disease_name',
          fieldPath: 'disease.name',
          label: 'Name',
          order: 20,
          value: 'Example disease',
        }),
        draftField({
          fieldKey: 'gene_name',
          fieldPath: 'gene.name',
          label: 'Name',
          order: 10,
          value: 'Example gene',
          required: true,
        }),
      ],
    })
    const secondCandidate = candidate({
      id: 'candidate-b',
      objectId: 'object-b',
      order: 10,
      fields: [
        draftField({
          fieldKey: 'phenotype_term',
          fieldPath: 'phenotype.term',
          label: 'Term',
          order: 10,
          value: 'Example phenotype',
          readOnly: true,
        }),
      ],
    })

    const model = modelForRows([
      workspaceRow({ candidate: firstCandidate }),
      workspaceRow({ candidate: secondCandidate }),
    ])

    expect(model.columns.map(({ key, label, fieldPath }) => ({ key, label, fieldPath }))).toEqual([
      { key: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY, label: 'Object', fieldPath: null },
      { key: 'field:gene.name', label: 'Name', fieldPath: 'gene.name' },
      { key: 'field:phenotype.term', label: 'Term', fieldPath: 'phenotype.term' },
      { key: 'field:disease.name', label: 'Name', fieldPath: 'disease.name' },
    ])
    expect(model.rows.map((row) => row.candidateId)).toEqual(['candidate-b', 'candidate-a'])
    expect(model.columns.filter((column) => column.label === 'Name')).toHaveLength(2)

    const missingGeneCell = model.rows[0]!.cells.find(
      (cell) => cell.fieldPath === 'gene.name',
    )
    expect(missingGeneCell).toMatchObject({
      fieldKey: null,
      hasField: false,
      required: null,
      readOnly: null,
      staleValidation: null,
    })
    expect(missingGeneCell?.value).toBeNull()
  })

  it('uses adapter field metadata for labels and ordering without changing canonical identity', () => {
    const referenceCandidate = candidate({
      id: 'candidate-reference',
      objectId: 'reference-object',
      order: 0,
      adapterKey: 'reference',
      fields: [
        draftField({
          fieldKey: 'raw-doi',
          fieldPath: 'identifiers.doi',
          label: 'Raw identifier',
          order: -10,
        }),
        draftField({
          fieldKey: 'raw-title',
          fieldPath: 'citation.title',
          label: 'Raw heading',
          order: 999,
        }),
      ],
    })

    const model = modelForRows([workspaceRow({ candidate: referenceCandidate })])

    expect(model.columns.slice(1).map(({ fieldPath, label, order }) => ({
      fieldPath,
      label,
      order,
    }))).toEqual([
      { fieldPath: 'citation.title', label: 'Title', order: 0 },
      { fieldPath: 'identifiers.doi', label: 'DOI', order: 100 },
    ])
    expect(model.rows[0]!.cells.map((cell) => cell.fieldKey)).toEqual([
      'raw-title',
      'raw-doi',
    ])
  })

  it('keeps identity context separate from fields and preserves explicit context metadata', () => {
    const rowCandidate = candidate({
      id: 'candidate-context',
      objectId: 'object-context',
      order: 0,
      displayLabel: 'Candidate label',
      fields: [
        draftField({ fieldKey: 'arbitrary', label: 'Arbitrary', order: 0 }),
      ],
    })
    const model = modelForRows([
      workspaceRow({
        candidate: rowCandidate,
        row: {
          ...reviewRow('object-context', 'Canonical identity', 'Secondary context'),
          summary_fields: [
            {
              field_path: 'relation.stage',
              label: 'Stage',
              value: 'adult',
              field_type: 'ontology_term',
              metadata: { display_order: 20 },
            },
            {
              field_path: 'relation.assay',
              label: 'Assay',
              value: 'RNA-seq',
              field_type: 'string',
              metadata: { display_order: 30 },
            },
          ],
        },
      }),
    ])

    expect(model.rows[0]).toMatchObject({
      candidateId: 'candidate-context',
      contextCell: {
        columnKey: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
        value: {
          objectId: 'object-context',
          objectType: 'FixtureObject',
          objectRole: 'curatable_unit',
          identityLabel: 'Canonical identity',
          secondaryLabel: 'Secondary context',
          candidateMetadata: { source: 'candidate-context' },
          summaryFields: [
            {
              field_path: 'relation.stage',
              label: 'Stage',
              value: 'adult',
              field_type: 'ontology_term',
              metadata: { display_order: 20 },
            },
            {
              field_path: 'relation.assay',
              label: 'Assay',
              value: 'RNA-seq',
              field_type: 'string',
              metadata: { display_order: 30 },
            },
          ],
          reviewRowMetadata: { context_source: 'object-context' },
        },
      },
    })
    expect(model.rows[0]!.cells).toHaveLength(1)
    expect(model.rows[0]!.cells[0]!.fieldPath).toBe('arbitrary')
  })

  it('associates exact field evidence and keeps otherwise unreachable evidence on context', () => {
    const rowCandidate = candidate({
      id: 'candidate-a',
      objectId: 'object-a',
      order: 0,
      fields: [
        draftField({
          fieldKey: 'gene-symbol-input',
          fieldPath: 'gene.symbol',
          label: 'Symbol',
          order: 0,
        }),
        draftField({
          fieldKey: 'gene-name-input',
          fieldPath: 'gene.name',
          label: 'Name',
          order: 1,
        }),
      ],
    })
    const model = modelForRows([
      workspaceRow({
        candidate: rowCandidate,
        evidence: [
          evidenceProjection({ id: 'name', fieldPath: 'gene.name' }),
          evidenceProjection({ id: 'object', fieldPath: '  ' }),
          evidenceProjection({ id: 'symbol', fieldPath: 'gene.symbol' }),
          evidenceProjection({ id: 'unrepresented', fieldPath: 'other.path' }),
        ],
      }),
    ])

    expect(model.rows[0]!.contextCell.evidence.map((item) => item.anchor_id)).toEqual([
      'object',
      'unrepresented',
    ])
    expect(model.rows[0]!.cells.map((cell) => [
      cell.fieldPath,
      cell.evidence.map((item) => item.anchor_id),
    ])).toEqual([
      ['gene.symbol', ['symbol']],
      ['gene.name', ['name']],
    ])
    expect(model.rows[0]!.evidence.map((item) => item.anchor_id)).toEqual([
      'object',
      'name',
      'symbol',
      'unrepresented',
    ])
    expect(model.rows[0]!.unmappedEvidence.map((item) => item.anchor_id)).toEqual([
      'unrepresented',
    ])
  })

  it('associates path projections with a column when the row lacks its draft field', () => {
    const candidateWithField = candidate({
      id: 'candidate-with-field',
      objectId: 'object-with-field',
      order: 0,
      fields: [
        draftField({
          fieldKey: 'gene-name-input',
          fieldPath: 'gene.name',
          label: 'Name',
          order: 0,
        }),
      ],
    })
    const candidateWithoutField = candidate({
      id: 'candidate-without-field',
      objectId: 'object-without-field',
      order: 1,
      fields: [],
    })
    const model = modelForRows([
      workspaceRow({ candidate: candidateWithField }),
      workspaceRow({
        candidate: candidateWithoutField,
        evidence: [
          evidenceProjection({ id: 'missing-field', fieldPath: 'gene.name' }),
          evidenceProjection({ id: 'unmapped', fieldPath: 'other.path' }),
        ],
        validation: [
          validationProjection({
            id: 'missing-field',
            fieldPath: 'gene.name',
            status: 'blocked',
            findings: 1,
            openFindings: 1,
          }),
          validationProjection({
            id: 'unmapped',
            fieldPath: 'other.path',
            status: 'unresolved',
            findings: 2,
            openFindings: 1,
          }),
        ],
      }),
    ])

    const row = model.rows[1]!
    expect(row.cells[0]).toMatchObject({
      fieldKey: null,
      fieldPath: 'gene.name',
      hasField: false,
      value: null,
      evidence: [{ anchor_id: 'missing-field' }],
      validation: {
        statuses: ['blocked'],
        summaryCount: 1,
        findingCount: 1,
        openFindingCount: 1,
      },
    })
    expect(row.unmappedEvidence.map((item) => item.anchor_id)).toEqual(['unmapped'])
    expect(row.contextCell.evidence.map((item) => item.anchor_id)).toEqual([
      'missing-field',
      'unmapped',
    ])
    expect(row.unmappedValidation).toMatchObject({
      statuses: ['unresolved'],
      summaryCount: 1,
      findingCount: 2,
      openFindingCount: 1,
    })
  })

  it('aggregates existing validation counts and statuses at cell and row scope', () => {
    const rowCandidate = candidate({
      id: 'candidate-a',
      objectId: 'object-a',
      order: 0,
      fields: [
        draftField({
          fieldKey: 'gene-symbol-input',
          fieldPath: 'gene.symbol',
          label: 'Symbol',
          order: 0,
        }),
      ],
    })
    const model = modelForRows([
      workspaceRow({
        candidate: rowCandidate,
        validation: [
          validationProjection({
            id: 'field-resolved',
            fieldPath: 'gene.symbol',
            status: 'resolved',
            findings: 2,
            openFindings: 0,
          }),
          validationProjection({
            id: 'object-unresolved',
            fieldPath: ' ',
            status: 'unresolved',
            findings: 3,
            openFindings: 2,
          }),
          validationProjection({
            id: 'field-blocked',
            fieldPath: 'gene.symbol',
            status: 'blocked',
            findings: 1,
            openFindings: 1,
          }),
        ],
      }),
    ])

    expect(model.rows[0]!.cells[0]!.validation).toMatchObject({
      statuses: ['blocked', 'resolved'],
      summaryCount: 2,
      findingCount: 3,
      openFindingCount: 1,
    })
    expect(model.rows[0]!.contextCell.validation).toMatchObject({
      statuses: ['unresolved'],
      summaryCount: 1,
      findingCount: 3,
      openFindingCount: 2,
    })
    expect(model.rows[0]!.validation).toMatchObject({
      statuses: ['unresolved', 'blocked', 'resolved'],
      summaryCount: 3,
      findingCount: 6,
      openFindingCount: 3,
    })
  })

  it('shows each validated field as validated, with its paper wording kept apart', () => {
    const siteCandidate = candidate({
      id: 'candidate-site',
      objectId: 'object-site',
      order: 0,
      fields: [
        draftField({ fieldKey: 'site-id', fieldPath: 'site.curie', label: 'Site ID', order: 0, value: 'ONT:0000101' }),
        draftField({ fieldKey: 'site-name', fieldPath: 'site.name', label: 'Site', order: 1, value: 'gut' }),
        draftField({ fieldKey: 'note', label: 'Note', order: 2, value: 'plain' }),
      ],
    })
    const otherCandidate = candidate({
      id: 'candidate-other',
      objectId: 'object-other',
      order: 1,
      fields: [
        draftField({ fieldKey: 'site-id', fieldPath: 'site.curie', label: 'Site ID', order: 0, value: null }),
        draftField({ fieldKey: 'site-name', fieldPath: 'site.name', label: 'Site', order: 1, value: null }),
        draftField({ fieldKey: 'note', label: 'Note', order: 2, value: null }),
      ],
    })
    const resolvedRow = reviewRowWithFields('object-site', [
      { path: 'site.curie', resolution: { display_text: 'ONT:0000101', values: [resolvedValue()] } },
      { path: 'site.name', resolution: { display_text: 'gut', values: [resolvedValue()] } },
      { path: 'note', resolution: null },
    ])
    const unresolvedRow = reviewRowWithFields('object-other', [
      { path: 'site.curie', resolution: { display_text: 'UNRESOLVED', values: [UNRESOLVED_SITE] } },
      { path: 'site.name', resolution: { display_text: 'UNRESOLVED', values: [UNRESOLVED_SITE] } },
      { path: 'note', resolution: null },
    ])

    const model = modelForRows([
      workspaceRow({ candidate: siteCandidate, row: resolvedRow }),
      workspaceRow({ candidate: otherCandidate, row: unresolvedRow }),
    ])

    expect(model.rows[0]!.cells.map((cell) => cell.displayText)).toEqual(['ONT:0000101', 'gut', 'plain'])
    expect(model.rows[0]!.cells[0]!.resolution?.values[0]).toMatchObject({
      mention: 'structures near the gut',
      lookup_result: 'Matched',
    })
    expect(model.rows[0]!.cells[2]!.resolution).toBeNull()
    expect(model.rows[1]!.cells.map((cell) => cell.displayText)).toEqual(['UNRESOLVED', 'UNRESOLVED', null])
    expect(model.rows[1]!.cells[1]!.resolution?.values[0]).toMatchObject({
      mention: 'structures near the residual body',
      lookup_result: 'Not found',
      validator_explanation: 'No term matched the wording.',
    })
  })

  it('reads resolutions from summary fields when the pack declares no workspace fields', () => {
    const siteCandidate = candidate({
      id: 'candidate-site',
      objectId: 'object-site',
      order: 0,
      fields: [draftField({ fieldKey: 'site', label: 'Site', order: 0, value: { mention: 'gut lining' } })],
    })
    const row = reviewRowWithFields(
      'object-site',
      [{ path: 'site', resolution: { display_text: 'UNRESOLVED', values: [UNRESOLVED_SITE] } }],
      { workspace: false },
    )

    const model = modelForRows([workspaceRow({ candidate: siteCandidate, row })])

    expect(model.rows[0]!.cells[0]).toMatchObject({ displayText: 'UNRESOLVED', value: { mention: 'gut lining' } })
  })

  it('reads a legacy, unverified stored value as unresolved, even against an extractor proposal', () => {
    const legacyValue = resolvedValue({
      display_text: 'UNRESOLVED',
      mention: 'abc-1 (legacy, unverified)',
      resolution_state: 'unresolved',
      lookup_outcome: 'legacy_unverified',
      lookup_result: 'Legacy, unverified',
      validator_explanation: 'Recorded before validation tracking; not verified.',
    })
    const legacyCandidate = candidate({
      id: 'candidate-legacy',
      objectId: 'object-legacy',
      order: 0,
      fields: [
        draftField({ fieldKey: 'symbol', fieldPath: 'symbol', label: 'Symbol', order: 0, value: 'abc-1' }),
        draftField({
          fieldKey: 'proposed-symbol',
          fieldPath: 'proposed_symbol',
          label: 'Proposed symbol',
          order: 1,
          value: 'abc-1',
          readOnly: true,
          renderAs: 'divergence',
        }),
      ],
    })
    const row = reviewRowWithFields('object-legacy', [
      { path: 'symbol', resolution: { display_text: 'UNRESOLVED', values: [legacyValue] } },
    ])

    const model = modelForRows([workspaceRow({
      candidate: legacyCandidate,
      row,
      validation: [validationProjection({
        id: 'legacy-symbol',
        fieldPath: 'symbol',
        status: 'resolved',
        findings: 0,
        openFindings: 0,
      })],
    })])

    expect(model.rows[0]!.cells[0]).toMatchObject({
      value: 'abc-1',
      displayText: 'UNRESOLVED',
      extractorComparison: { outcome: 'unresolved', value: 'abc-1' },
    })
    expect(model.rows[0]!.cells[0]!.resolution?.values[0]?.mention).toBe('abc-1 (legacy, unverified)')
  })

  it('reads a saved curator edit from the regenerated review row, as a curator override', () => {
    const overridden = resolvedValue({
      display_text: 'midgut (ONT:0000555)',
      mention: 'gut lining',
      lookup_outcome: 'curator_override',
      lookup_result: 'Curator override',
      curator_override: { actor_id: 'curator-1', at: '2026-09-23T20:00:00+00:00' },
    })
    const fields = [
      { ...draftField({ fieldKey: 'site-id', fieldPath: 'site.curie', label: 'Site ID', order: 0, value: 'ONT:0000555' }), dirty: true },
      { ...draftField({ fieldKey: 'site-name', fieldPath: 'site.name', label: 'Site', order: 1, value: 'midgut' }), dirty: true },
      draftField({
        fieldKey: 'site-lookup',
        fieldPath: 'site.lookup_outcome',
        label: 'Site lookup',
        order: 2,
        value: 'curator_override',
      }),
    ]
    const row = reviewRowWithFields('object-override', [
      { path: 'site.curie', resolution: { display_text: 'ONT:0000555', values: [overridden] } },
      { path: 'site.name', resolution: { display_text: 'midgut', values: [overridden] } },
      {
        path: 'site.lookup_outcome',
        resolution: { display_text: 'Curator override', values: [overridden], leaf_key: 'lookup_outcome' },
      },
    ])

    const model = modelForRows([workspaceRow({
      candidate: candidate({ id: 'candidate-override', objectId: 'object-override', order: 0, fields }),
      row,
    })])

    const [idCell, nameCell] = model.rows[0]!.cells
    expect(model.columns.map((column) => column.fieldPath)).toEqual([null, 'site.curie', 'site.name'])
    expect(idCell).toMatchObject({
      displayText: 'ONT:0000555',
      state: 'resolved',
      curatorOverride: true,
      overrideDisagreements: [],
      readOnly: false,
      overrideTarget: overridden,
    })
    expect(idCell!.resolutionDetails).toEqual([overridden])
    expect(nameCell).toMatchObject({ curatorOverride: true, resolutionDetails: [] })
  })

  it('shows needs review with an open validator disagreement, and a proposal as overridden', () => {
    const message = "Validator disagrees with the curator override: it resolved symbol 'abc-9'."
    const overridden = resolvedValue({
      value_path: '',
      display_text: 'abc-2 (GENE:2)',
      lookup_outcome: 'curator_override',
      lookup_result: 'Curator override',
      curator_override: { actor_id: 'curator-1', at: '2026-09-23T20:00:00+00:00' },
      override_disagreements: [message],
      identity_field_paths: ['symbol', 'identifier'],
      id_key: 'curie',
      label_key: 'name',
      validated_keys: [],
      stored_value: {},
    })
    const fields = [
      draftField({ fieldKey: 'symbol', label: 'Symbol', order: 0, value: 'abc-2' }),
      draftField({ fieldKey: 'identifier', label: 'Gene ID', order: 1, value: 'GENE:2', readOnly: true }),
      draftField({
        fieldKey: 'proposed-symbol',
        fieldPath: 'proposed_symbol',
        label: 'Proposed symbol',
        order: 2,
        value: 'abc-1',
        readOnly: true,
        renderAs: 'divergence',
      }),
    ]
    const row = reviewRowWithFields('object-disagree', [
      { path: 'symbol', resolution: { display_text: 'abc-2', values: [overridden] } },
      { path: 'identifier', resolution: { display_text: 'GENE:2', values: [overridden] } },
    ])

    const model = modelForRows([workspaceRow({
      candidate: candidate({ id: 'candidate-disagree', objectId: 'object-disagree', order: 0, fields }),
      row,
    })])

    expect(model.rows[0]!.cells[0]).toMatchObject({
      displayText: 'abc-2',
      state: 'needs-review',
      curatorOverride: true,
      overrideDisagreements: [message],
      extractorComparison: { outcome: 'overridden', value: 'abc-1' },
      // The value is the object itself (an empty path): no whole-value edit here.
      overrideTarget: null,
    })
    // A read-only identity cell never offers an override.
    expect(model.rows[0]!.cells[1]).toMatchObject({ readOnly: true, overrideTarget: null })
  })

  it('shows each value\'s details once, on its first cell, and hides leaves that cell covers', () => {
    const geneValue = resolvedValue({
      value_path: '',
      display_text: 'UNRESOLVED',
      mention: 'abc-1',
      resolution_state: 'unresolved',
      lookup_outcome: 'not_found',
      lookup_result: 'Not found',
    })
    const fields = [
      draftField({ fieldKey: 'symbol', label: 'Symbol', order: 0, value: null }),
      draftField({ fieldKey: 'identifier', label: 'Gene ID', order: 1, value: null }),
      draftField({ fieldKey: 'mention', label: 'Paper mention', order: 2, value: 'abc-1' }),
      draftField({ fieldKey: 'lookup_outcome', label: 'Lookup result', order: 3, value: 'not_found' }),
    ]
    const covered = candidate({ id: 'candidate-covered', objectId: 'object-covered', order: 0, fields })
    const row = reviewRowWithFields('object-covered', [
      { path: 'symbol', resolution: { display_text: 'UNRESOLVED', values: [geneValue] } },
      { path: 'identifier', resolution: { display_text: 'UNRESOLVED', values: [geneValue] } },
      { path: 'mention', resolution: { display_text: 'abc-1', values: [geneValue], leaf_key: 'mention' } },
      {
        path: 'lookup_outcome',
        resolution: { display_text: 'Not found', values: [geneValue], leaf_key: 'lookup_outcome' },
      },
    ])

    const model = modelForRows([workspaceRow({ candidate: covered, row })])

    expect(model.columns.map((column) => column.fieldPath)).toEqual([null, 'symbol', 'identifier'])
    const [symbolCell, identifierCell] = model.rows[0]!.cells
    expect(model.rows[0]!.cells.map((cell) => cell.resolutionDetails.length)).toEqual([1, 0])
    expect(symbolCell!.resolutionLinesId).not.toBeNull()
    expect(identifierCell!.resolutionLinesId).toBeNull()
    // The second identity cell is described by the first cell's lines.
    expect(symbolCell!.resolutionDescribedBy).toEqual([symbolCell!.resolutionLinesId])
    expect(identifierCell!.resolutionDescribedBy).toEqual([symbolCell!.resolutionLinesId])
  })

  it('puts a value\'s details on its first cell, edited or not', () => {
    const geneValue = resolvedValue({ value_path: '', display_text: 'abc-1 (GENE:1)', mention: 'abc-1' })
    const covered = candidate({
      id: 'candidate-edited-owner',
      objectId: 'object-edited-owner',
      order: 0,
      fields: [
        { ...draftField({ fieldKey: 'symbol', label: 'Symbol', order: 0, value: 'abc-2' }), dirty: true },
        draftField({ fieldKey: 'identifier', label: 'Gene ID', order: 1, value: 'GENE:1' }),
      ],
    })
    const row = reviewRowWithFields('object-edited-owner', [
      { path: 'symbol', resolution: { display_text: 'abc-1', values: [geneValue] } },
      { path: 'identifier', resolution: { display_text: 'GENE:1', values: [geneValue] } },
    ])

    const model = modelForRows([workspaceRow({ candidate: covered, row })])

    const [symbolCell, identifierCell] = model.rows[0]!.cells
    expect(symbolCell!.resolutionDetails).toEqual([geneValue])
    expect(identifierCell!.resolutionDetails).toEqual([])
    expect(identifierCell!.resolutionDescribedBy).toEqual([symbolCell!.resolutionLinesId])
  })

  it('shows a covered leaf in its row when another row needs the leaf column', () => {
    const geneValue = resolvedValue({
      value_path: '',
      display_text: 'UNRESOLVED',
      mention: 'abc-1',
      resolution_state: 'unresolved',
      lookup_outcome: 'not_found',
      lookup_result: 'Not found',
    })
    const projected = candidate({
      id: 'candidate-projected',
      objectId: 'object-projected',
      order: 0,
      fields: [
        draftField({ fieldKey: 'symbol', label: 'Symbol', order: 0, value: null }),
        draftField({ fieldKey: 'lookup_outcome', label: 'Lookup result', order: 1, value: 'not_found' }),
      ],
    })
    const manual = candidate({
      id: 'candidate-manual',
      objectId: 'object-manual',
      order: 1,
      fields: [
        draftField({ fieldKey: 'symbol', label: 'Symbol', order: 0, value: 'abc-3' }),
        draftField({ fieldKey: 'lookup_outcome', label: 'Lookup result', order: 1, value: 'matched' }),
      ],
    })
    manual.projection_ref = null
    const row = reviewRowWithFields('object-projected', [
      { path: 'symbol', resolution: { display_text: 'UNRESOLVED', values: [geneValue] } },
      {
        path: 'lookup_outcome',
        resolution: { display_text: 'Not found', values: [geneValue], leaf_key: 'lookup_outcome' },
      },
    ])

    const model = buildHorizontalGridModel({
      candidates: [projected, manual],
      envelopeReviewRows: [workspaceRow({ candidate: projected, row })],
    })

    expect(model.columns.map((column) => column.fieldPath)).toEqual([null, 'symbol', 'lookup_outcome'])
    expect(model.rows[0]!.cells[1]).toMatchObject({ hasField: true, displayText: 'Not found' })
    expect(model.rows[1]!.cells[1]).toMatchObject({ hasField: true, displayText: 'matched' })
  })

  it('shows a value\'s own leaf in plain words when no cell of its value is in the grid', () => {
    const leafOnly = candidate({
      id: 'candidate-leaf',
      objectId: 'object-leaf',
      order: 0,
      fields: [
        draftField({ fieldKey: 'lookup_outcome', label: 'Lookup result', order: 0, value: 'not_found' }),
      ],
    })
    const row = reviewRowWithFields('object-leaf', [
      {
        path: 'lookup_outcome',
        resolution: { display_text: 'Not found', values: [UNRESOLVED_SITE], leaf_key: 'lookup_outcome' },
      },
    ])

    const model = modelForRows([workspaceRow({ candidate: leafOnly, row })])

    expect(model.rows[0]!.cells[0]).toMatchObject({ displayText: 'Not found', resolutionDetails: [] })
  })

  it('never presents an unresolved value as validated', () => {
    const waived = candidate({
      id: 'candidate-waived',
      objectId: 'object-waived',
      order: 0,
      fields: [draftField({ fieldKey: 'site', label: 'Site', order: 0, value: null })],
    })
    const row = reviewRowWithFields('object-waived', [
      { path: 'site', resolution: { display_text: 'UNRESOLVED', values: [UNRESOLVED_SITE] } },
    ])

    const model = modelForRows([workspaceRow({
      candidate: waived,
      row,
      validation: [validationProjection({
        id: 'waived-site',
        fieldPath: 'site',
        status: 'waived',
        findings: 1,
        openFindings: 0,
      })],
    })])

    expect(model.rows[0]!.cells[0]).toMatchObject({ displayText: 'UNRESOLVED', state: 'needs-review' })
  })

  it('never renders an object value as JSON', () => {
    const objectCandidate = candidate({
      id: 'candidate-object',
      objectId: 'object-object',
      order: 0,
      fields: [
        draftField({ fieldKey: 'term', label: 'Term', order: 0, value: { curie: 'ONT:1', name: 'term one' } }),
        draftField({
          fieldKey: 'staged',
          label: 'Staged',
          order: 1,
          value: { mention: 'a wording', curie: null, resolution_state: 'unresolved', lookup_outcome: 'not_validated' },
        }),
        draftField({
          fieldKey: 'provider',
          label: 'Provider',
          order: 2,
          value: [{ abbreviation: 'XB', tags: ['a', 'b'] }, { abbreviation: 'YB' }],
        }),
        draftField({ fieldKey: 'aliases', label: 'Aliases', order: 3, value: ['one', 'two'] }),
        draftField({
          fieldKey: 'demoted',
          label: 'Demoted',
          order: 5,
          value: {
            mention: 'gut lining',
            curie: null,
            overruled_curie: 'ONT:1',
            proposed_curie: 'ONT:2',
            resolution_state: 'unresolved',
            lookup_outcome: 'rejected_candidates',
          },
        }),
        draftField({
          fieldKey: 'reresolved',
          label: 'Re-resolved',
          order: 6,
          value: {
            abbreviation: 'XB',
            proposed_abbreviation: 'YB',
            overruled_abbreviation: 'ZB',
            mention: 'Xenbase',
            resolution_state: 'resolved',
            lookup_outcome: 'matched',
          },
        }),
        draftField({
          fieldKey: 'record',
          label: 'Record',
          order: 7,
          value: { relation: 'has_condition', overruled_curie: 'ONT:3' },
        }),
        draftField({
          fieldKey: 'provider_ref',
          label: 'Provider reference',
          order: 4,
          value: {
            abbreviation: 'XB',
            mention: 'Xenbase',
            resolution_state: 'resolved',
            lookup_outcome: 'matched',
          },
        }),
      ],
    })

    const model = modelForRows([workspaceRow({ candidate: objectCandidate })])

    // The same readings as backend exports (value_display.py).
    expect(model.rows[0]!.cells.map((cell) => cell.displayText)).toEqual([
      'term one (ONT:1)',
      'UNRESOLVED',
      'abbreviation: XB; tags: a, b | abbreviation: YB',
      'one; two',
      'abbreviation: XB',
      // Neither a validator's overruled identity nor the extractor's proposal
      // ever reads as the value.
      'UNRESOLVED',
      'abbreviation: XB',
      'relation: has_condition',
    ])
  })

  it('rejects multiple draft fields with the same canonical path', () => {
    const collidingCandidate = candidate({
      id: 'candidate-collision',
      objectId: 'object-collision',
      order: 0,
      fields: [
        draftField({
          fieldKey: 'first-input',
          fieldPath: 'canonical.path',
          label: 'First',
          order: 0,
        }),
        draftField({
          fieldKey: 'second-input',
          fieldPath: 'canonical.path',
          label: 'Second',
          order: 1,
        }),
      ],
    })

    expect(() => modelForRows([
      workspaceRow({ candidate: collidingCandidate }),
    ])).toThrow(
      "Candidate 'candidate-collision' has multiple draft fields for canonical path "
      + "'canonical.path': 'first-input' and 'second-input'",
    )
  })

  it('includes current candidates without envelope object identity', () => {
    const genericCandidate = candidate({
      id: 'candidate-generic',
      objectId: 'unused-object-id',
      order: 0,
      displayLabel: 'Generic candidate',
      fields: [
        draftField({
          fieldKey: 'generic.value',
          label: 'Generic value',
          order: 0,
          value: 'Current value',
        }),
      ],
    })
    genericCandidate.projection_ref = null

    const model = buildHorizontalGridModel({
      candidates: [genericCandidate],
      envelopeReviewRows: [],
    })

    expect(model.rows[0]).toMatchObject({
      candidateId: 'candidate-generic',
      contextCell: {
        value: {
          objectId: null,
          identityLabel: 'Generic candidate',
          envelopeId: null,
          envelopeRevision: null,
          objectType: null,
          objectRole: null,
          summaryFields: null,
        },
      },
    })
    expect(model.rows[0]!.cells[0]).toMatchObject({
      fieldPath: 'generic.value',
      hasField: true,
      value: 'Current value',
    })
  })

  it('rejects envelope candidates without their canonical review row', () => {
    const envelopeCandidate = candidate({
      id: 'candidate-envelope',
      objectId: 'object-envelope',
      order: 0,
      fields: [],
    })

    expect(() => buildHorizontalGridModel({
      candidates: [envelopeCandidate],
      envelopeReviewRows: [],
    })).toThrow(
      "Candidate 'candidate-envelope' has an envelope projection but no envelope review row",
    )
  })

  it('rejects review rows for candidates without an envelope projection', () => {
    const genericCandidate = candidate({
      id: 'candidate-generic',
      objectId: 'object-generic',
      order: 0,
      fields: [],
    })
    const orphanReviewRow = workspaceRow({ candidate: genericCandidate })
    genericCandidate.projection_ref = null

    expect(() => buildHorizontalGridModel({
      candidates: [genericCandidate],
      envelopeReviewRows: [orphanReviewRow],
    })).toThrow(
      "Envelope review row references candidate 'candidate-generic' without an envelope projection",
    )
  })
})

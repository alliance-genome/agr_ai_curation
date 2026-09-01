import { describe, expect, it } from 'vitest'

import type {
  CurationCandidate,
  CurationDraftField,
  DomainEnvelopeEvidenceAnchorProjection,
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
}: {
  fieldKey: string
  fieldPath?: string
  label: string
  order: number
  value?: unknown
  required?: boolean
  readOnly?: boolean
}): CurationDraftField {
  return {
    field_key: fieldKey,
    label,
    value,
    seed_value: value,
    field_type: 'string',
    group_key: 'details',
    group_label: 'Details',
    order,
    required,
    read_only: readOnly,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: { source_field_path: fieldPath },
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

describe('buildHorizontalGridModel', () => {
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

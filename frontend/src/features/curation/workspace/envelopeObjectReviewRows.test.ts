import { describe, expect, it } from 'vitest'

import type {
  CurationCandidate,
  DomainEnvelopeReviewRow,
  DomainEnvelopeReviewRowsResponse,
} from '@/features/curation/types'
import { buildWorkspaceEnvelopeObjectReviewRows } from './envelopeObjectReviewRows'

function candidateForObject(objectId: string): CurationCandidate {
  return {
    candidate_id: `candidate-${objectId}`,
    session_id: 'session-1',
    source: 'extracted',
    status: 'pending',
    order: 0,
    adapter_key: 'domain-pack',
    projection_ref: {
      envelope_id: 'tmem67-envelope',
      object_id: objectId,
      envelope_revision: 3,
    },
    draft: {
      draft_id: `draft-${objectId}`,
      candidate_id: `candidate-${objectId}`,
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

function reviewRow(objectId: string, objectType: string): DomainEnvelopeReviewRow {
  return {
    envelope_id: 'tmem67-envelope',
    object_id: objectId,
    envelope_revision: 3,
    domain_pack_id: 'fixture.first_pass',
    domain_pack_version: '0.7.0',
    object_type: objectType,
    object_role: 'curatable_unit',
    status: 'draft',
    validation_state: 'unresolved',
    projection_type: 'workspace_review_row',
    projection_key: objectId,
    display_label: `${objectType} ${objectId}`,
    secondary_label: null,
    summary_fields: [
      {
        field_path: 'label',
        label: 'Label',
        value: `${objectType} label`,
        field_type: 'string',
        metadata: {},
      },
    ],
    schema_provider: null,
    schema_ref: {},
    object_model_ref: {},
    model_field_ref: {},
    metadata: {
      semantic_source: 'domain_envelope.objects',
    },
  }
}

function reviewRowsResponse(rows: DomainEnvelopeReviewRow[]): DomainEnvelopeReviewRowsResponse {
  return {
    envelope_id: 'tmem67-envelope',
    envelope_revision: 3,
    row_count: rows.length,
    rows,
  }
}

describe('workspace envelope object review rows', () => {
  it('projects tmem67 and first-pass domain fixtures through one envelope-object row shape', () => {
    const fixtureObjects = [
      ['tmem67-gene', 'Gene'],
      ['tmem67-allele', 'Allele'],
      ['tmem67-disease', 'Disease'],
      ['tmem67-chemical', 'Chemical'],
      ['tmem67-phenotype', 'Phenotype'],
    ] as const

    const rows = buildWorkspaceEnvelopeObjectReviewRows({
      candidates: fixtureObjects.map(([objectId]) => candidateForObject(objectId)),
      reviewRowResponses: [
        reviewRowsResponse(
          fixtureObjects.map(([objectId, objectType]) => reviewRow(objectId, objectType)),
        ),
      ],
    })

    expect(rows.map((row) => row.projectionRef.object_id)).toEqual([
      'tmem67-gene',
      'tmem67-allele',
      'tmem67-disease',
      'tmem67-chemical',
      'tmem67-phenotype',
    ])
    expect(rows.map((row) => row.reviewRow?.object_type)).toEqual([
      'Gene',
      'Allele',
      'Disease',
      'Chemical',
      'Phenotype',
    ])
    expect(rows.every((row) => row.reviewRow?.metadata.semantic_source === 'domain_envelope.objects')).toBe(true)
  })

  it('keeps missing review rows explicit instead of reading candidate draft semantics', () => {
    const [row] = buildWorkspaceEnvelopeObjectReviewRows({
      candidates: [candidateForObject('tmem67-gene')],
      reviewRowResponses: [reviewRowsResponse([])],
    })

    expect(row.projectionRef).toEqual({
      envelope_id: 'tmem67-envelope',
      object_id: 'tmem67-gene',
      envelope_revision: 3,
    })
    expect(row.reviewRow).toBeNull()
  })
})

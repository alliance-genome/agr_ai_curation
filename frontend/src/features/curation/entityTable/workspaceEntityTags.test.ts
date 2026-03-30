import { describe, expect, it } from 'vitest'
import type { CurationCandidate } from '@/features/curation/types'
import {
  buildEntityTagFieldChanges,
  buildManualCandidateDraft,
} from './workspaceEntityTags'

function buildCandidate(overrides: Partial<CurationCandidate> = {}): CurationCandidate {
  return {
    candidate_id: 'candidate-1',
    session_id: 'session-1',
    source: 'extracted',
    status: 'pending',
    order: 0,
    adapter_key: 'gene_extraction',
    draft: {
      draft_id: 'draft-1',
      candidate_id: 'candidate-1',
      adapter_key: 'gene_extraction',
      version: 3,
      fields: [
        {
          field_key: 'gene_symbol',
          label: 'Gene symbol',
          value: 'BRCA1',
          seed_value: 'BRCA1',
          order: 0,
          required: true,
          read_only: false,
          dirty: false,
          stale_validation: false,
          evidence_anchor_ids: ['anchor-1'],
          validation_result: {
            status: 'validated',
            resolver: 'agr_db',
            candidate_matches: [{ label: 'BRCA1', identifier: 'HGNC:1100' }],
            warnings: [],
          },
          metadata: {},
        },
        {
          field_key: 'entity_type',
          label: 'Entity type',
          value: 'ATP:0000005',
          seed_value: 'ATP:0000005',
          order: 1,
          required: true,
          read_only: false,
          dirty: false,
          stale_validation: false,
          evidence_anchor_ids: [],
          validation_result: null,
          metadata: {},
        },
      ],
      created_at: '2026-03-30T10:00:00Z',
      updated_at: '2026-03-30T10:00:00Z',
      metadata: {},
    },
    evidence_anchors: [
      {
        anchor_id: 'anchor-1',
        candidate_id: 'candidate-1',
        source: 'extracted',
        field_keys: ['gene_symbol'],
        field_group_keys: ['primary'],
        is_primary: true,
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'exact_quote',
          supports_decision: 'supports',
          snippet_text: 'BRCA1 is required for DNA repair.',
          chunk_ids: ['chunk-1'],
          page_number: 5,
          section_title: 'Results',
        },
        created_at: '2026-03-30T10:00:00Z',
        updated_at: '2026-03-30T10:00:00Z',
        warnings: [],
      },
    ],
    validation: {
      state: 'completed',
      counts: {
        validated: 1,
        ambiguous: 0,
        not_found: 0,
        invalid_format: 0,
        conflict: 0,
        skipped: 0,
        overridden: 0,
      },
      stale_field_keys: [],
      warnings: [],
    },
    created_at: '2026-03-30T10:00:00Z',
    updated_at: '2026-03-30T10:00:00Z',
    metadata: {},
    ...overrides,
  }
}

describe('workspaceEntityTags', () => {
  it('builds field changes for editable entity values', () => {
    const candidate = buildCandidate({
      draft: {
        ...buildCandidate().draft,
        fields: [
          ...buildCandidate().draft.fields,
          {
            field_key: 'topic',
            label: 'Topic',
            value: '',
            seed_value: '',
            order: 1,
            required: false,
            read_only: false,
            dirty: false,
            stale_validation: false,
            evidence_anchor_ids: [],
            validation_result: null,
            metadata: {},
          },
        ],
      },
    })

    const fieldChanges = buildEntityTagFieldChanges(candidate, {
      entity_name: 'BRCA2',
      entity_type: 'ATP:0000005',
      topic: 'phenotype',
    })

    expect(fieldChanges).toEqual([
      { field_key: 'gene_symbol', value: 'BRCA2' },
      { field_key: 'topic', value: 'phenotype' },
    ])
  })

  it('fails clearly when an update cannot be stored in the draft fields', () => {
    expect(() =>
      buildEntityTagFieldChanges(buildCandidate(), {
        topic: 'phenotype',
      }),
    ).toThrow(/cannot store topic/i)
  })

  it('builds a manual draft from a template candidate', () => {
    const manualDraft = buildManualCandidateDraft(
      buildCandidate(),
      {
        entity_name: 'TP53',
        entity_type: 'ATP:0000005',
        species: '',
        topic: '',
      },
      '2026-03-30T11:00:00Z',
    )

    expect(manualDraft.fields[0]?.value).toBe('TP53')
    expect(manualDraft.fields[1]?.value).toBe('ATP:0000005')
    expect(manualDraft.fields[0]?.validation_result).toBeNull()
    expect(manualDraft.fields[0]?.dirty).toBe(false)
  })

  it('rejects unsupported ATP entity type codes in editable updates', () => {
    expect(() =>
      buildEntityTagFieldChanges(buildCandidate(), {
        entity_type: 'ATP:9999999',
      }),
    ).toThrow(/not a supported ATP code/i)
  })
})

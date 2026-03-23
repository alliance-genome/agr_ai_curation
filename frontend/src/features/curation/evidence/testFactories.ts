import type { CurationEvidenceRecord } from '../types'

export type EvidenceRecordOverrides = Partial<
  Omit<CurationEvidenceRecord, 'anchor'>
> & {
  anchor?: Partial<CurationEvidenceRecord['anchor']>
}

export function createEvidenceRecord(
  anchorId: string,
  overrides: EvidenceRecordOverrides = {},
): CurationEvidenceRecord {
  const { anchor: anchorOverrides, ...recordOverrides } = overrides

  return {
    anchor_id: anchorId,
    candidate_id: 'candidate-1',
    source: 'extracted',
    field_keys: ['gene_symbol'],
    field_group_keys: ['identity'],
    is_primary: anchorId === 'anchor-1',
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      snippet_text: `Snippet for ${anchorId}`,
      viewer_search_text: `Search text for ${anchorId}`,
      page_number: 3,
      section_title: 'Results',
      chunk_ids: [`chunk-${anchorId}`],
      sentence_text: `Sentence for ${anchorId}`,
      ...anchorOverrides,
    },
    created_at: '2026-03-20T12:00:00Z',
    updated_at: '2026-03-20T12:00:00Z',
    warnings: [],
    ...recordOverrides,
  }
}

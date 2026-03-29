import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type { EvidenceRecord } from '@/features/curation/types'

function buildAnchorToken(value: string, fallback: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)

  return normalized || fallback
}

function buildChatEvidenceAnchorId(evidenceRecord: EvidenceRecord): string {
  return [
    'chat-evidence',
    evidenceRecord.chunk_id,
    `p${evidenceRecord.page}`,
    buildAnchorToken(evidenceRecord.entity, 'entity'),
    buildAnchorToken(evidenceRecord.verified_quote, 'quote'),
  ].join(':')
}

export function buildChatEvidenceNavigationCommand(
  evidenceRecord: EvidenceRecord,
): EvidenceNavigationCommand {
  const quote = evidenceRecord.verified_quote.trim()

  return {
    anchorId: buildChatEvidenceAnchorId(evidenceRecord),
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      snippet_text: quote || null,
      sentence_text: quote || null,
      normalized_text: quote || null,
      viewer_search_text: quote || null,
      viewer_highlightable: true,
      page_number: evidenceRecord.page,
      section_title: evidenceRecord.section,
      subsection_title: evidenceRecord.subsection ?? null,
      figure_reference: evidenceRecord.figure_reference ?? null,
      chunk_ids: [evidenceRecord.chunk_id],
    },
    searchText: quote || null,
    pageNumber: evidenceRecord.page,
    sectionTitle: evidenceRecord.section,
    mode: 'select',
  }
}

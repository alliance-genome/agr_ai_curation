import type { CurationEvidenceRecord } from '@/features/curation/types'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type { EntityTag } from './types'

function normalizeText(value: string | null | undefined): string | null {
  const normalized = value?.trim() ?? ''
  return normalized.length > 0 ? normalized : null
}

function evidenceRecordQuote(evidence: CurationEvidenceRecord): string | null {
  return normalizeText(evidence.anchor.sentence_text) ?? normalizeText(evidence.anchor.snippet_text)
}

export function buildEntityTagNavigationCommand(
  tag: EntityTag,
  evidenceRecord?: CurationEvidenceRecord | null,
): EvidenceNavigationCommand | null {
  if (evidenceRecord) {
    const quote = evidenceRecordQuote(evidenceRecord)
    if (!quote) return null

    const searchText = normalizeText(evidenceRecord.anchor.viewer_search_text)
      ?? normalizeText(evidenceRecord.anchor.normalized_text)
      ?? quote

    return {
      anchorId: evidenceRecord.anchor_id,
      anchor: evidenceRecord.anchor,
      searchText,
      pageNumber: evidenceRecord.anchor.page_number ?? null,
      sectionTitle: evidenceRecord.anchor.section_title ?? null,
      mode: 'select',
    }
  }

  if (!tag.evidence) return null

  const quote = tag.evidence.sentence_text.trim()
  if (!quote) return null

  return {
    anchorId: `entity-tag:${tag.tag_id}`,
    anchor: {
      anchor_kind: 'sentence',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      snippet_text: quote,
      sentence_text: quote,
      normalized_text: quote,
      viewer_search_text: quote,
      viewer_highlightable: true,
      page_number: tag.evidence.page_number,
      section_title: tag.evidence.section_title,
      chunk_ids: tag.evidence.chunk_ids,
    },
    searchText: quote,
    pageNumber: tag.evidence.page_number,
    sectionTitle: tag.evidence.section_title,
    mode: 'select',
  }
}

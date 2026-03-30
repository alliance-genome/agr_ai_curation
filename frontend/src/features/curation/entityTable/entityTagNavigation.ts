import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type { EntityTag } from './types'

export function buildEntityTagNavigationCommand(
  tag: EntityTag,
): EvidenceNavigationCommand | null {
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

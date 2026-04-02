import type { CurationEvidenceRecord } from '@/features/curation/types'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import {
  buildQuoteCentricEvidenceNavigationCommand,
  normalizeEvidenceNavigationText,
} from '@/features/curation/evidence/navigationCommandBuilder'
import type { EntityTag } from './types'

function evidenceRecordQuote(evidence: CurationEvidenceRecord): string | null {
  return normalizeEvidenceNavigationText(evidence.anchor.sentence_text)
    ?? normalizeEvidenceNavigationText(evidence.anchor.snippet_text)
}

export function buildEntityTagNavigationCommand(
  tag: EntityTag,
  evidenceRecord?: CurationEvidenceRecord | null,
): EvidenceNavigationCommand | null {
  if (evidenceRecord) {
    const quote = evidenceRecordQuote(evidenceRecord)
    if (!quote) return null

    return buildQuoteCentricEvidenceNavigationCommand({
      anchorId: evidenceRecord.anchor_id,
      anchor: evidenceRecord.anchor,
      quote,
      pageNumber: evidenceRecord.anchor.page_number ?? null,
      sectionTitle: evidenceRecord.anchor.section_title ?? null,
      mode: 'select',
    })
  }

  if (!tag.evidence) return null

  const quote = normalizeEvidenceNavigationText(tag.evidence.sentence_text)
  if (!quote) return null

  return buildQuoteCentricEvidenceNavigationCommand({
    anchorId: `entity-tag:${tag.tag_id}`,
    anchor: {
      anchor_kind: 'sentence',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      viewer_highlightable: true,
      page_number: tag.evidence.page_number,
      section_title: tag.evidence.section_title,
      chunk_ids: tag.evidence.chunk_ids,
    },
    quote,
    pageNumber: tag.evidence.page_number,
    sectionTitle: tag.evidence.section_title,
    mode: 'select',
  })
}

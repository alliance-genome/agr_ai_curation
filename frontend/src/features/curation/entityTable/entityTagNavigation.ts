import type { CurationEvidenceRecord } from '@/features/curation/types'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import {
  buildNavigationCommandFromCurationEvidenceRecord,
} from '@/features/curation/evidence/navigationSourceAdapters'
import type { EntityTag } from './types'

function buildSyntheticCurationEvidenceRecordFromTag(tag: EntityTag): CurationEvidenceRecord | null {
  if (!tag.evidence?.sentence_text.trim()) {
    return null
  }

  return {
    anchor_id: `entity-tag:${tag.tag_id}`,
    candidate_id: tag.tag_id,
    source: tag.source === 'manual' ? 'manual' : 'extracted',
    field_keys: [],
    field_group_keys: [],
    is_primary: true,
    anchor: {
      anchor_kind: 'sentence',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      sentence_text: tag.evidence.sentence_text,
      snippet_text: tag.evidence.sentence_text,
      viewer_search_text: tag.evidence.sentence_text,
      viewer_highlightable: true,
      page_number: tag.evidence.page_number ?? null,
      section_title: tag.evidence.section_title ?? null,
      chunk_ids: tag.evidence.chunk_ids ?? [],
    },
    created_at: '',
    updated_at: '',
    warnings: [],
  }
}

export function buildEntityTagNavigationCommand(
  tag: EntityTag,
  evidenceRecord?: CurationEvidenceRecord | null,
): EvidenceNavigationCommand | null {
  if (evidenceRecord) {
    return buildNavigationCommandFromCurationEvidenceRecord(evidenceRecord, 'select')
  }

  const syntheticEvidenceRecord = buildSyntheticCurationEvidenceRecordFromTag(tag)
  if (!syntheticEvidenceRecord) return null

  return buildNavigationCommandFromCurationEvidenceRecord(syntheticEvidenceRecord, 'select')
}

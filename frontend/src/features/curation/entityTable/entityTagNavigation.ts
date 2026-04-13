import type { CurationEvidenceRecord } from '@/features/curation/types'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import {
  buildNavigationCommandFromCurationEvidenceRecord,
  buildNavigationCommandFromLegacyEntityTagEvidence,
} from '@/features/curation/evidence/navigationSourceAdapters'
import type { EntityTag } from './types'

export function buildEntityTagNavigationCommand(
  tag: EntityTag,
  evidenceRecord?: CurationEvidenceRecord | null,
): EvidenceNavigationCommand | null {
  if (evidenceRecord) {
    return buildNavigationCommandFromCurationEvidenceRecord(evidenceRecord, 'select')
  }

  if (!tag.evidence) return null

  return buildNavigationCommandFromLegacyEntityTagEvidence(
    `entity-tag:${tag.tag_id}`,
    tag.evidence,
    'select',
  )
}

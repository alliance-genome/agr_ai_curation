import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import { buildNavigationCommandFromChatEvidenceRecord } from '@/features/curation/evidence/navigationSourceAdapters'
import type { EvidenceRecord } from '@/features/curation/types'

export function buildChatEvidenceNavigationCommand(
  evidenceRecord: EvidenceRecord,
): EvidenceNavigationCommand {
  return buildNavigationCommandFromChatEvidenceRecord(evidenceRecord, 'select')
}

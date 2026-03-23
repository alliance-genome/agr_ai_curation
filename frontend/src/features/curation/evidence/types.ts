import type {
  EvidenceAnchor,
  EvidenceLocatorQuality,
} from '../contracts'
import type { CurationEvidenceRecord } from '../types'

export interface EvidenceChipProps {
  evidence: CurationEvidenceRecord
  isSelected: boolean
  isHovered: boolean
  quality: EvidenceLocatorQuality
  label: string
  onClick: (evidence: CurationEvidenceRecord) => void
  onHoverStart: (evidence: CurationEvidenceRecord) => void
  onHoverEnd: () => void
}

export interface EvidenceNavigationCommand {
  anchor: EvidenceAnchor
  searchText: string | null
  pageNumber: number | null
  sectionTitle: string | null
  mode: 'hover' | 'select'
}

export interface EvidenceNavigationState {
  selectedEvidence: CurationEvidenceRecord | null
  hoveredEvidence: CurationEvidenceRecord | null
  pendingNavigation: EvidenceNavigationCommand | null
  candidateEvidence: CurationEvidenceRecord[]
  evidenceByAnchorId: Record<string, CurationEvidenceRecord>
  evidenceByField: Record<string, CurationEvidenceRecord[]>
  evidenceByGroup: Record<string, CurationEvidenceRecord[]>
}

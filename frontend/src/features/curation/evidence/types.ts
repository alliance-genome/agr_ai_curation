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

/** One copied span of the evidence, as stored: an exact slice of one stored piece. */
export interface TextEvidenceFragment {
  text: string
  section: string | null
  subsection: string | null
}

/**
 * What a text file needs to locate the evidence exactly: its stored fragments (each
 * located on its own) and its section names, unchanged from the record (never split
 * or re-joined), for the exact section comparison.
 */
export interface TextEvidenceTarget {
  /** Null when the record carries no fragments (review-grid anchors): the quote is used. */
  fragments: TextEvidenceFragment[] | null
  section: string | null
  subsection: string | null
}

export interface EvidenceNavigationCommand {
  anchorId: string
  /**
   * The paper file the evidence is in. Null only for evidence saved before files
   * were recorded (0.12.0): the viewer then opens nothing.
   */
  fileId: string | null
  /** For a text file: the stored fragments and raw section names. */
  textEvidence: TextEvidenceTarget
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

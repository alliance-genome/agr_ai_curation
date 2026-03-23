import { useMemo } from 'react'
import { Stack } from '@mui/material'

import type { EvidenceAnchor } from '../contracts'
import type { CurationEvidenceRecord } from '../types'
import type { UseEvidenceNavigationReturn } from './useEvidenceNavigation'
import EvidenceChip from './EvidenceChip'

type EvidenceChipGroupNavigationProps = Pick<
  UseEvidenceNavigationReturn,
  | 'evidenceByAnchorId'
  | 'hoverEvidence'
  | 'hoveredEvidence'
  | 'selectEvidence'
  | 'selectedEvidence'
>

export interface EvidenceChipGroupProps extends EvidenceChipGroupNavigationProps {
  evidenceAnchorIds: string[]
}

function getEvidenceChipLabel(anchor: EvidenceAnchor): string {
  const pageLabel = anchor.page_label?.trim()
  if (pageLabel) {
    return `p.${pageLabel}`
  }

  if (anchor.page_number !== null && anchor.page_number !== undefined) {
    return `p.${anchor.page_number}`
  }

  if (anchor.section_title?.trim()) {
    return `§${anchor.section_title.trim()}`
  }

  return 'Context'
}

function isEvidenceRecord(record: CurationEvidenceRecord | null): record is CurationEvidenceRecord {
  return record !== null
}

export default function EvidenceChipGroup({
  evidenceAnchorIds,
  evidenceByAnchorId,
  hoveredEvidence,
  selectedEvidence,
  selectEvidence,
  hoverEvidence,
}: EvidenceChipGroupProps) {
  const fieldEvidence = useMemo(
    () =>
      [...new Set(evidenceAnchorIds)]
        .map((anchorId) => evidenceByAnchorId[anchorId] ?? null)
        .filter(isEvidenceRecord),
    [evidenceAnchorIds, evidenceByAnchorId]
  )

  if (fieldEvidence.length === 0) {
    return null
  }

  return (
    <Stack direction="row" flexWrap="wrap" spacing={0.75} useFlexGap>
      {fieldEvidence.map((record) => (
        <EvidenceChip
          evidence={record}
          isHovered={hoveredEvidence?.anchor_id === record.anchor_id}
          isSelected={selectedEvidence?.anchor_id === record.anchor_id}
          key={record.anchor_id}
          label={getEvidenceChipLabel(record.anchor)}
          onClick={selectEvidence}
          onHoverEnd={() => hoverEvidence(null)}
          onHoverStart={hoverEvidence}
          quality={record.anchor.locator_quality}
        />
      ))}
    </Stack>
  )
}

import { useState } from 'react'

import { Box } from '@mui/material'
import type { CurationWorkspaceLaunchTarget } from '@/features/curation/navigation/openCurationWorkspace'

import type { EvidenceRecord } from '@/features/curation/types'

import EntityChipBar, { type EntityChipBarItem } from './EntityChipBar'
import EvidenceQuoteList, { type EvidenceQuoteGroup } from './EvidenceQuoteList'

const EVIDENCE_COLOR_PALETTE = [
  {
    colorHex: '#64b5f6',
    chipBackground: 'rgba(100, 181, 246, 0.2)',
    chipBorder: 'rgba(100, 181, 246, 0.4)',
    activeBackground: 'rgba(100, 181, 246, 0.45)',
    inactiveBackground: 'rgba(100, 181, 246, 0.15)',
    inactiveBorder: 'rgba(100, 181, 246, 0.3)',
  },
  {
    colorHex: '#81c784',
    chipBackground: 'rgba(129, 199, 132, 0.2)',
    chipBorder: 'rgba(129, 199, 132, 0.4)',
    activeBackground: 'rgba(129, 199, 132, 0.45)',
    inactiveBackground: 'rgba(129, 199, 132, 0.15)',
    inactiveBorder: 'rgba(129, 199, 132, 0.3)',
  },
  {
    colorHex: '#ffb74d',
    chipBackground: 'rgba(255, 183, 77, 0.2)',
    chipBorder: 'rgba(255, 183, 77, 0.4)',
    activeBackground: 'rgba(255, 183, 77, 0.45)',
    inactiveBackground: 'rgba(255, 183, 77, 0.15)',
    inactiveBorder: 'rgba(255, 183, 77, 0.3)',
  },
  {
    colorHex: '#ce93d8',
    chipBackground: 'rgba(206, 147, 216, 0.2)',
    chipBorder: 'rgba(206, 147, 216, 0.4)',
    activeBackground: 'rgba(206, 147, 216, 0.45)',
    inactiveBackground: 'rgba(206, 147, 216, 0.15)',
    inactiveBorder: 'rgba(206, 147, 216, 0.3)',
  },
  {
    colorHex: '#ef9a9a',
    chipBackground: 'rgba(239, 154, 154, 0.2)',
    chipBorder: 'rgba(239, 154, 154, 0.4)',
    activeBackground: 'rgba(239, 154, 154, 0.45)',
    inactiveBackground: 'rgba(239, 154, 154, 0.15)',
    inactiveBorder: 'rgba(239, 154, 154, 0.3)',
  },
  {
    colorHex: '#80cbc4',
    chipBackground: 'rgba(128, 203, 196, 0.2)',
    chipBorder: 'rgba(128, 203, 196, 0.4)',
    activeBackground: 'rgba(128, 203, 196, 0.45)',
    inactiveBackground: 'rgba(128, 203, 196, 0.15)',
    inactiveBorder: 'rgba(128, 203, 196, 0.3)',
  },
  {
    colorHex: '#f48fb1',
    chipBackground: 'rgba(244, 143, 177, 0.2)',
    chipBorder: 'rgba(244, 143, 177, 0.4)',
    activeBackground: 'rgba(244, 143, 177, 0.45)',
    inactiveBackground: 'rgba(244, 143, 177, 0.15)',
    inactiveBorder: 'rgba(244, 143, 177, 0.3)',
  },
  {
    colorHex: '#aed581',
    chipBackground: 'rgba(174, 213, 129, 0.2)',
    chipBorder: 'rgba(174, 213, 129, 0.4)',
    activeBackground: 'rgba(174, 213, 129, 0.45)',
    inactiveBackground: 'rgba(174, 213, 129, 0.15)',
    inactiveBorder: 'rgba(174, 213, 129, 0.3)',
  },
] as const

interface EvidenceCardProps {
  evidenceRecords: EvidenceRecord[]
  headerLabel?: string
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
  onReviewAndCurateClick?: (() => void) | null
  interactionMode?: 'interactive' | 'readOnly'
  containerTestId?: string
  headerIconTestId?: string
  quoteTestId?: string
}

function buildEntityData(
  evidenceRecords: EvidenceRecord[],
): {
  chipItems: EntityChipBarItem[]
  quoteGroups: EvidenceQuoteGroup[]
} {
  const groupedRecords = new Map<string, EvidenceRecord[]>()

  evidenceRecords.forEach((record) => {
    const entityKey = record.entity.trim()
    if (!groupedRecords.has(entityKey)) {
      groupedRecords.set(entityKey, [])
    }
    groupedRecords.get(entityKey)?.push(record)
  })

  const chipItems: EntityChipBarItem[] = []
  const quoteGroups: EvidenceQuoteGroup[] = []

  Array.from(groupedRecords.entries()).forEach(([entity, records], index) => {
    const palette = EVIDENCE_COLOR_PALETTE[index % EVIDENCE_COLOR_PALETTE.length]

    chipItems.push({
      entity,
      quoteCount: records.length,
      ...palette,
    })

    quoteGroups.push({
      entity,
      evidenceRecords: records,
      colorHex: palette.colorHex,
    })
  })

  return { chipItems, quoteGroups }
}

export default function EvidenceCard({
  evidenceRecords,
  headerLabel,
  onReviewAndCurateClick,
  interactionMode = 'interactive',
  containerTestId,
  headerIconTestId = 'evidence-card-header-icon',
  quoteTestId,
}: EvidenceCardProps) {
  const [activeEntity, setActiveEntity] = useState<string | null>(null)
  const { chipItems, quoteGroups } = buildEntityData(evidenceRecords)
  const isInteractive = interactionMode === 'interactive'

  const handleEntityToggle = (entity: string) => {
    setActiveEntity((currentEntity) => (currentEntity === entity ? null : entity))
  }

  return (
    <Box
      data-testid={containerTestId}
      sx={{
        backgroundColor: '#0d47a1',
        borderRadius: '0 0 18px 4px',
        borderTop: '1px solid rgba(255, 255, 255, 0.1)',
        px: '1rem',
        py: '10px',
        maxWidth: '100%',
        boxSizing: 'border-box',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          px: '4px',
          mb: '8px',
        }}
      >
        <Box
          aria-hidden="true"
          component="svg"
          data-testid={headerIconTestId}
          fill="none"
          sx={{
            flexShrink: 0,
            width: '14px',
            height: '14px',
            display: 'block',
          }}
          viewBox="0 0 24 24"
        >
          <path
            d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
          <polyline
            points="14 2 14 8 20 8"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
          <line
            x1="16"
            x2="8"
            y1="13"
            y2="13"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
          <line
            x1="16"
            x2="8"
            y1="17"
            y2="17"
            stroke="rgba(255,255,255,0.6)"
            strokeWidth="2"
          />
        </Box>

        <Box
          sx={{
            fontSize: '12px',
            color: 'rgba(255, 255, 255, 0.7)',
          }}
        >
          {headerLabel ?? `${evidenceRecords.length} evidence quotes`}
        </Box>
      </Box>

      <EntityChipBar
        activeEntity={activeEntity}
        items={chipItems}
        onEntityToggle={handleEntityToggle}
      />

      <EvidenceQuoteList
        activeEntity={activeEntity}
        groups={quoteGroups}
        interactive={isInteractive}
        onReviewAndCurateClick={isInteractive ? onReviewAndCurateClick : null}
        quoteTestId={quoteTestId}
      />
    </Box>
  )
}

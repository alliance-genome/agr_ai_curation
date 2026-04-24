import { useMemo, useState } from 'react'

import { Box } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import type { Theme } from '@mui/material/styles'
import type { CurationWorkspaceLaunchTarget } from '@/features/curation/navigation/openCurationWorkspace'

import type { EvidenceRecord } from '@/features/curation/types'

import EntityChipBar, { type EntityChipBarItem } from './EntityChipBar'
import EvidenceQuoteList, { type EvidenceQuoteGroup } from './EvidenceQuoteList'

interface EvidenceColorTone {
  colorHex: string
  chipBackground: string
  chipBorder: string
  activeBackground: string
  inactiveBackground: string
  inactiveBorder: string
  textColor: string
  activeTextColor: string
}

function buildEvidenceColorPalette(theme: Theme): EvidenceColorTone[] {
  const isDark = theme.palette.mode === 'dark'
  const textColor = theme.palette.text.primary
  const colors = [
    isDark ? theme.palette.primary.light : theme.palette.primary.main,
    isDark ? theme.palette.success.light : theme.palette.success.dark,
    isDark ? theme.palette.warning.light : theme.palette.warning.dark,
    isDark ? theme.palette.secondary.light : theme.palette.secondary.main,
    isDark ? theme.palette.error.light : theme.palette.error.dark,
    isDark ? theme.palette.info.light : theme.palette.info.dark,
    isDark ? theme.palette.primary.main : theme.palette.primary.dark,
    isDark ? theme.palette.success.main : theme.palette.success.dark,
  ]

  return colors.map((colorHex) => ({
    colorHex,
    chipBackground: alpha(colorHex, isDark ? 0.2 : 0.12),
    chipBorder: alpha(colorHex, isDark ? 0.4 : 0.32),
    activeBackground: alpha(colorHex, isDark ? 0.45 : 0.18),
    inactiveBackground: alpha(colorHex, isDark ? 0.15 : 0.08),
    inactiveBorder: alpha(colorHex, isDark ? 0.3 : 0.2),
    textColor,
    activeTextColor: textColor,
  }))
}

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
  evidenceColorPalette: EvidenceColorTone[],
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
    const palette = evidenceColorPalette[index % evidenceColorPalette.length]

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
  const theme = useTheme()
  const [activeEntity, setActiveEntity] = useState<string | null>(null)
  const evidenceColorPalette = useMemo(() => buildEvidenceColorPalette(theme), [theme])
  const { chipItems, quoteGroups } = useMemo(
    () => buildEntityData(evidenceRecords, evidenceColorPalette),
    [evidenceRecords, evidenceColorPalette],
  )
  const isInteractive = interactionMode === 'interactive'

  const handleEntityToggle = (entity: string) => {
    setActiveEntity((currentEntity) => (currentEntity === entity ? null : entity))
  }

  return (
    <Box
      data-testid={containerTestId}
      sx={(theme) => ({
        backgroundColor: theme.palette.mode === 'dark'
          ? theme.palette.secondary.dark
          : alpha(theme.palette.secondary.main, 0.09),
        borderRadius: '0 0 18px 4px',
        borderTop: `1px solid ${alpha(theme.palette.divider, 0.85)}`,
        color: theme.palette.text.primary,
        px: '1rem',
        py: '10px',
        maxWidth: '100%',
        boxSizing: 'border-box',
      })}
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
            color: 'text.secondary',
          }}
          viewBox="0 0 24 24"
        >
          <path
            d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
            stroke="currentColor"
            strokeWidth="2"
          />
          <polyline
            points="14 2 14 8 20 8"
            stroke="currentColor"
            strokeWidth="2"
          />
          <line
            x1="16"
            x2="8"
            y1="13"
            y2="13"
            stroke="currentColor"
            strokeWidth="2"
          />
          <line
            x1="16"
            x2="8"
            y1="17"
            y2="17"
            stroke="currentColor"
            strokeWidth="2"
          />
        </Box>

        <Box
          sx={{
            fontSize: '12px',
            color: 'text.secondary',
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

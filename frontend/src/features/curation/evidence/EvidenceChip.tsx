import { ButtonBase, Tooltip, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { EvidenceLocatorQuality } from '../contracts'
import type { EvidenceChipProps } from './types'

const DEGRADED_LOCATOR_QUALITIES = new Set<EvidenceLocatorQuality>([
  'page_only',
  'document_only',
  'unresolved',
])

function getTooltipText({
  snippetText,
  sentenceText,
}: {
  snippetText?: string | null
  sentenceText?: string | null
}): string {
  const resolvedText = snippetText?.trim() || sentenceText?.trim()

  return resolvedText && resolvedText.length > 0
    ? resolvedText
    : 'No evidence snippet available.'
}

export default function EvidenceChip({
  evidence,
  isSelected,
  isHovered,
  quality,
  label,
  onClick,
  onHoverStart,
  onHoverEnd,
}: EvidenceChipProps) {
  const theme = useTheme()
  const isDegraded = DEGRADED_LOCATOR_QUALITIES.has(quality)

  return (
    <Tooltip
      arrow
      enterDelay={200}
      placement="top"
      title={getTooltipText({
        snippetText: evidence.anchor.snippet_text,
        sentenceText: evidence.anchor.sentence_text,
      })}
    >
      <ButtonBase
        aria-pressed={isSelected}
        data-hovered={isHovered ? 'true' : 'false'}
        data-quality={quality}
        data-selected={isSelected ? 'true' : 'false'}
        data-testid={`evidence-chip-${evidence.anchor_id}`}
        onBlur={onHoverEnd}
        onClick={() => onClick(evidence)}
        onFocus={() => onHoverStart(evidence)}
        onMouseEnter={() => onHoverStart(evidence)}
        onMouseLeave={onHoverEnd}
        sx={{
          px: 1,
          py: 0.4,
          borderRadius: 999,
          border: `1px solid ${
            isSelected
              ? alpha(theme.palette.primary.main, 0.88)
              : isHovered
                ? alpha(theme.palette.info.main, 0.78)
                : isDegraded
                  ? alpha(theme.palette.warning.main, 0.42)
                  : alpha(theme.palette.divider, 0.78)
          }`,
          backgroundColor: isSelected
            ? alpha(theme.palette.primary.main, 0.18)
            : isHovered
              ? alpha(theme.palette.info.main, 0.14)
              : alpha(theme.palette.background.paper, 0.56),
          color: isSelected
            ? theme.palette.primary.light
            : isHovered
              ? theme.palette.info.light
              : theme.palette.text.secondary,
          minHeight: 24,
          transition: 'border-color 0.2s ease, background-color 0.2s ease, color 0.2s ease',
          '&:hover': {
            borderColor: alpha(theme.palette.primary.main, 0.72),
            backgroundColor: isSelected
              ? alpha(theme.palette.primary.main, 0.22)
              : alpha(theme.palette.primary.main, 0.12),
          },
          '&:focus-visible': {
            outline: `2px solid ${theme.palette.primary.main}`,
            outlineOffset: 2,
          },
        }}
      >
        <Typography
          component="span"
          sx={{ fontSize: theme.typography.caption.fontSize, fontWeight: 700, lineHeight: 1 }}
        >
          {label}
        </Typography>
      </ButtonBase>
    </Tooltip>
  )
}

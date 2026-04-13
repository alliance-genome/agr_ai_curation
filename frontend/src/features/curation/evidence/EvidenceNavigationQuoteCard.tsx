import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { Box, IconButton, Tooltip } from '@mui/material'
import type { MouseEvent, ReactNode } from 'react'

import type { EvidenceNavigationCommand } from './types'
import {
  buildEvidenceLocationLabel,
  dispatchEvidenceNavigationCommand,
} from './navigationPresentation'

interface EvidenceNavigationQuoteCardProps {
  command: EvidenceNavigationCommand
  quote: string
  quoteContent?: ReactNode
  ariaLabel: string
  appearance: 'chat' | 'workspace'
  accentColor?: string
  debugContext?: Record<string, unknown>
  footerText?: string | null
  copyButtonAriaLabel?: string | null
  onCopy?: ((event: MouseEvent<HTMLButtonElement>) => void) | null
}

export default function EvidenceNavigationQuoteCard({
  command,
  quote,
  quoteContent,
  ariaLabel,
  appearance,
  accentColor,
  debugContext,
  footerText = 'Click to highlight this passage in the PDF',
  copyButtonAriaLabel = null,
  onCopy = null,
}: EvidenceNavigationQuoteCardProps) {
  const locationLabel = buildEvidenceLocationLabel({
    pageNumber: command.pageNumber ?? command.anchor.page_number ?? null,
    sectionTitle: command.sectionTitle ?? command.anchor.section_title ?? null,
    subsectionTitle: command.anchor.subsection_title ?? null,
  })
  const isChatAppearance = appearance === 'chat'
  const resolvedAccentColor = accentColor
    ?? (isChatAppearance ? 'rgba(107, 208, 255, 0.72)' : '#2e7d32')

  return (
    <Box sx={{ position: 'relative' }}>
      <Box
        aria-label={ariaLabel}
        component="button"
        onClick={() => dispatchEvidenceNavigationCommand(command, debugContext)}
        sx={{
          backgroundColor: isChatAppearance
            ? 'rgba(255, 255, 255, 0.08)'
            : 'rgba(46, 125, 50, 0.06)',
          borderRadius: '8px',
          border: 0,
          px: '12px',
          py: '10px',
          pr: onCopy ? '44px' : '12px',
          pb: footerText ? (onCopy ? '34px' : '30px') : '10px',
          borderLeft: `3px solid ${resolvedAccentColor}`,
          cursor: 'pointer',
          display: 'block',
          font: 'inherit',
          textAlign: 'left',
          width: '100%',
          transition: 'background-color 140ms ease, transform 140ms ease',
          color: isChatAppearance ? 'inherit' : 'text.primary',
          '&:hover': {
            backgroundColor: isChatAppearance
              ? 'rgba(255, 255, 255, 0.12)'
              : 'rgba(46, 125, 50, 0.1)',
            transform: 'translateX(2px)',
          },
          '&:focus-visible': {
            outline: `2px solid ${resolvedAccentColor}`,
            outlineOffset: '2px',
          },
        }}
        type="button"
      >
        <Box
          sx={{
            fontSize: '11px',
            color: isChatAppearance ? 'rgba(255, 255, 255, 0.6)' : 'text.secondary',
            mb: '4px',
          }}
        >
          {locationLabel}
        </Box>

        <Box
          sx={{
            fontSize: isChatAppearance ? '13px' : '0.82rem',
            fontStyle: 'italic',
            lineHeight: 1.5,
            color: isChatAppearance ? 'rgba(255, 255, 255, 0.9)' : 'text.primary',
          }}
        >
          &ldquo;{quoteContent ?? quote}&rdquo;
        </Box>

        {footerText ? (
          <Box
            sx={{
              fontSize: '11px',
              color: isChatAppearance ? 'rgba(255, 255, 255, 0.56)' : 'text.secondary',
              mt: '6px',
            }}
          >
            {footerText}
          </Box>
        ) : null}
      </Box>

      {onCopy && copyButtonAriaLabel ? (
        <Tooltip title="Copy evidence quote">
          <IconButton
            aria-label={copyButtonAriaLabel}
            onClick={onCopy}
            size="small"
            sx={{
              position: 'absolute',
              right: '8px',
              bottom: '8px',
              backgroundColor: isChatAppearance
                ? 'rgba(255, 255, 255, 0.08)'
                : 'rgba(46, 125, 50, 0.08)',
              border: isChatAppearance
                ? '1px solid rgba(255, 255, 255, 0.12)'
                : '1px solid rgba(46, 125, 50, 0.16)',
              color: isChatAppearance ? 'rgba(255, 255, 255, 0.68)' : 'rgba(46, 125, 50, 0.84)',
              '&:hover': {
                backgroundColor: isChatAppearance
                  ? 'rgba(255, 255, 255, 0.16)'
                  : 'rgba(46, 125, 50, 0.14)',
                color: isChatAppearance ? '#ffffff' : '#1b5e20',
              },
            }}
            type="button"
          >
            <ContentCopyIcon fontSize="inherit" />
          </IconButton>
        </Tooltip>
      ) : null}
    </Box>
  )
}

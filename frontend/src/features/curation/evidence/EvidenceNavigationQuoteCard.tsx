import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { Box, IconButton, Tooltip } from '@mui/material'
import { alpha } from '@mui/material/styles'
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
    ?? (isChatAppearance ? null : '#2e7d32')

  return (
    <Box sx={{ position: 'relative' }}>
      <Box
        aria-label={ariaLabel}
        component="button"
        onClick={() => dispatchEvidenceNavigationCommand(command, debugContext)}
        sx={(theme) => {
          const chatAccentColor = resolvedAccentColor ?? alpha(theme.palette.info.main, 0.72)
          const visibleAccentColor = isChatAppearance ? chatAccentColor : (resolvedAccentColor ?? '#2e7d32')

          return {
            backgroundColor: isChatAppearance
              ? theme.palette.mode === 'dark'
                ? alpha(theme.palette.common.white, 0.08)
                : alpha(theme.palette.background.paper, 0.72)
              : 'rgba(46, 125, 50, 0.06)',
            borderRadius: '8px',
            border: 0,
            px: '12px',
            py: '10px',
            pr: onCopy ? '44px' : '12px',
            pb: footerText ? (onCopy ? '34px' : '30px') : '10px',
            borderLeft: `3px solid ${visibleAccentColor}`,
            cursor: 'pointer',
            display: 'block',
            font: 'inherit',
            textAlign: 'left',
            width: '100%',
            transition: 'background-color 140ms ease, transform 140ms ease',
            color: theme.palette.text.primary,
            '&:hover': {
              backgroundColor: isChatAppearance
                ? theme.palette.mode === 'dark'
                  ? alpha(theme.palette.common.white, 0.12)
                  : alpha(theme.palette.background.paper, 0.9)
                : 'rgba(46, 125, 50, 0.1)',
              transform: 'translateX(2px)',
            },
            '&:focus-visible': {
              outline: `2px solid ${visibleAccentColor}`,
              outlineOffset: '2px',
            },
          }
        }}
        type="button"
      >
        <Box
          sx={{
            fontSize: '11px',
            color: 'text.secondary',
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
            color: 'text.primary',
          }}
        >
          &ldquo;{quoteContent ?? quote}&rdquo;
        </Box>

        {footerText ? (
          <Box
            sx={{
              fontSize: '11px',
              color: 'text.secondary',
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
            sx={(theme) => {
              const chatAccentColor = resolvedAccentColor ?? theme.palette.info.main

              return {
                position: 'absolute',
                right: '8px',
                bottom: '8px',
                backgroundColor: isChatAppearance
                  ? theme.palette.mode === 'dark'
                    ? alpha(theme.palette.common.white, 0.08)
                    : alpha(theme.palette.background.paper, 0.76)
                  : 'rgba(46, 125, 50, 0.08)',
                border: isChatAppearance
                  ? `1px solid ${alpha(chatAccentColor, 0.2)}`
                  : '1px solid rgba(46, 125, 50, 0.16)',
                color: isChatAppearance ? theme.palette.text.secondary : 'rgba(46, 125, 50, 0.84)',
                '&:hover': {
                  backgroundColor: isChatAppearance
                    ? alpha(chatAccentColor, theme.palette.mode === 'dark' ? 0.16 : 0.12)
                    : 'rgba(46, 125, 50, 0.14)',
                  color: isChatAppearance ? theme.palette.text.primary : '#1b5e20',
                },
              }
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

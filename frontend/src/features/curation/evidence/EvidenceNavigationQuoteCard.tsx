import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { Box, IconButton, Tooltip } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import type { MouseEvent, ReactNode } from 'react'

import type { EvidenceNavigationCommand } from './types'
import {
  buildEvidenceLocationLabel,
  dispatchEvidenceNavigationCommand,
} from './navigationPresentation'

interface EvidenceNavigationQuoteCardProps {
  command: EvidenceNavigationCommand | null
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
  const theme = useTheme()
  const locationLabel = command
    ? buildEvidenceLocationLabel({
        pageNumber: command.pageNumber ?? command.anchor.page_number ?? null,
        sectionTitle: command.sectionTitle ?? command.anchor.section_title ?? null,
        subsectionTitle: command.anchor.subsection_title ?? null,
      })
    : 'PDF location unavailable'
  const isChatAppearance = appearance === 'chat'
  const chatSurfaceColor = theme.palette.secondary.main
  const chatSurfaceTextColor = theme.palette.getContrastText(chatSurfaceColor)
  const chatAccentColor = accentColor ?? alpha(theme.palette.info.main, 0.72)
  const workspaceAccentColor = accentColor ?? theme.palette.success.main
  const visibleAccentColor = isChatAppearance ? chatAccentColor : workspaceAccentColor

  return (
    <Box sx={{
      backgroundColor: isChatAppearance
        ? theme.palette.mode === 'dark'
          ? alpha(chatSurfaceTextColor, 0.08)
          : alpha(theme.palette.background.paper, 0.72)
        : alpha(theme.palette.primary.main, 0.08),
      borderRadius: '8px',
      border: isChatAppearance ? 0 : `1px solid ${alpha(theme.palette.primary.main, 0.42)}`,
      borderLeft: `3px solid ${visibleAccentColor}`,
      overflowWrap: 'anywhere',
    }}>
      <Box
        aria-label={ariaLabel}
        component="button"
        disabled={!command}
        onClick={command
          ? () => dispatchEvidenceNavigationCommand(command, debugContext)
          : undefined}
        sx={{
          backgroundColor: 'transparent',
          borderRadius: '8px',
          border: 0,
          px: '12px',
          py: '10px',
          cursor: command ? 'pointer' : 'not-allowed',
          display: 'block',
          font: 'inherit',
          textAlign: 'left',
          width: '100%',
          transition: 'background-color 140ms ease',
          color: theme.palette.text.primary,
          '&:hover:not(:disabled)': {
            backgroundColor: isChatAppearance
              ? theme.palette.mode === 'dark'
                ? alpha(chatSurfaceTextColor, 0.12)
                : alpha(theme.palette.background.paper, 0.9)
              : alpha(theme.palette.primary.main, 0.12),
          },
          '&:focus-visible': {
            outline: `2px solid ${visibleAccentColor}`,
            outlineOffset: '2px',
          },
          '&:disabled': {
            opacity: 0.72,
          },
        }}
        type="button"
      >
        <Box
          sx={{
            fontSize: '11px',
            color: 'text.secondary',
            mb: '4px',
            fontWeight: isChatAppearance ? 400 : 700,
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
            fontWeight: isChatAppearance ? 400 : 500,
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
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', px: '8px', pb: '8px' }}>
          <Tooltip title="Copy evidence quote">
            <IconButton
              aria-label={copyButtonAriaLabel}
              onClick={onCopy}
              size="small"
              sx={{
                backgroundColor: theme.palette.background.paper,
                color: theme.palette.text.primary,
                border: `1px solid ${theme.palette.divider}`,
                borderRadius: '6px',
                width: 40,
                height: 40,
                '&:hover': { backgroundColor: theme.palette.background.paper, color: theme.palette.primary.main },
                '&:focus-visible': { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: 2 },
                '@media (hover: none)': { width: 44, height: 44 },
              }}
              type="button"
            >
              <ContentCopyIcon fontSize="inherit" />
            </IconButton>
          </Tooltip>
        </Box>
      ) : null}
    </Box>
  )
}

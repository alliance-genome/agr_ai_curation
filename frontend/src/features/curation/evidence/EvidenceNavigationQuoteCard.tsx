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
  const theme = useTheme()
  const locationLabel = buildEvidenceLocationLabel({
    pageNumber: command.pageNumber ?? command.anchor.page_number ?? null,
    sectionTitle: command.sectionTitle ?? command.anchor.section_title ?? null,
    subsectionTitle: command.anchor.subsection_title ?? null,
  })
  const isChatAppearance = appearance === 'chat'
  const chatSurfaceColor = theme.palette.secondary.main
  const chatSurfaceTextColor = theme.palette.getContrastText(chatSurfaceColor)
  const chatAccentColor = accentColor ?? alpha(theme.palette.info.main, 0.72)
  const copyChatAccentColor = accentColor ?? theme.palette.info.main
  const workspaceAccentColor = accentColor ?? theme.palette.success.main
  const visibleAccentColor = isChatAppearance ? chatAccentColor : workspaceAccentColor

  return (
    <Box sx={{ position: 'relative' }}>
      <Box
        aria-label={ariaLabel}
        component="button"
        onClick={() => dispatchEvidenceNavigationCommand(command, debugContext)}
        sx={{
          backgroundColor: isChatAppearance
            ? theme.palette.mode === 'dark'
              ? alpha(chatSurfaceTextColor, 0.08)
              : alpha(theme.palette.background.paper, 0.72)
            : alpha(theme.palette.success.main, 0.06),
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
                ? alpha(chatSurfaceTextColor, 0.12)
                : alpha(theme.palette.background.paper, 0.9)
              : alpha(theme.palette.success.main, 0.1),
            transform: 'translateX(2px)',
          },
          '&:focus-visible': {
            outline: `2px solid ${visibleAccentColor}`,
            outlineOffset: '2px',
          },
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
            sx={{
              position: 'absolute',
              right: '8px',
              bottom: '8px',
              backgroundColor: isChatAppearance
                ? theme.palette.mode === 'dark'
                  ? alpha(chatSurfaceTextColor, 0.08)
                  : alpha(theme.palette.background.paper, 0.76)
                : alpha(theme.palette.success.main, 0.08),
              border: isChatAppearance
                ? `1px solid ${alpha(copyChatAccentColor, 0.2)}`
                : `1px solid ${alpha(theme.palette.success.main, 0.16)}`,
              color: isChatAppearance
                ? theme.palette.text.secondary
                : alpha(theme.palette.success.main, 0.84),
              '&:hover': {
                backgroundColor: isChatAppearance
                  ? alpha(copyChatAccentColor, theme.palette.mode === 'dark' ? 0.16 : 0.12)
                  : alpha(theme.palette.success.main, 0.14),
                color: isChatAppearance ? theme.palette.text.primary : theme.palette.success.dark,
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

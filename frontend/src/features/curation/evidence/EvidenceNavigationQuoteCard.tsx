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
  const resolvedAccentColor = accentColor
    ?? (isChatAppearance ? theme.palette.info.light : theme.palette.success.main)
  const chatTextColor = theme.palette.common.white
  const subtleTextColor = alpha(chatTextColor, 0.6)
  const mutedTextColor = alpha(chatTextColor, 0.56)

  return (
    <Box sx={{ position: 'relative' }}>
      <Box
        aria-label={ariaLabel}
        component="button"
        onClick={() => dispatchEvidenceNavigationCommand(command, debugContext)}
        sx={{
          backgroundColor: isChatAppearance
            ? alpha(chatTextColor, 0.08)
            : alpha(theme.palette.success.main, 0.06),
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
          color: isChatAppearance ? chatTextColor : theme.palette.text.primary,
          '&:hover': {
            backgroundColor: isChatAppearance
              ? alpha(chatTextColor, 0.12)
              : alpha(theme.palette.success.main, 0.1),
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
            color: isChatAppearance ? subtleTextColor : theme.palette.text.secondary,
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
            color: isChatAppearance ? alpha(chatTextColor, 0.9) : theme.palette.text.primary,
          }}
        >
          &ldquo;{quoteContent ?? quote}&rdquo;
        </Box>

        {footerText ? (
          <Box
            sx={{
              fontSize: '11px',
              color: isChatAppearance ? mutedTextColor : theme.palette.text.secondary,
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
                ? alpha(chatTextColor, 0.08)
                : alpha(theme.palette.success.main, 0.08),
              border: isChatAppearance
                ? `1px solid ${alpha(chatTextColor, 0.12)}`
                : `1px solid ${alpha(theme.palette.success.main, 0.16)}`,
              color: isChatAppearance
                ? alpha(chatTextColor, 0.68)
                : alpha(theme.palette.success.main, 0.84),
              '&:hover': {
                backgroundColor: isChatAppearance
                  ? alpha(chatTextColor, 0.16)
                  : alpha(theme.palette.success.main, 0.14),
                color: isChatAppearance ? chatTextColor : theme.palette.success.dark,
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

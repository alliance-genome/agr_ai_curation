import type { CSSProperties } from 'react'

import { alpha } from '@mui/material/styles'
import type { Theme } from '@mui/material/styles'

import type { ChatCssVariables, ChatNoticeTone } from './types'

function getNoticeColor(theme: Theme, tone: ChatNoticeTone): string {
  const contrastScale = theme.palette.mode === 'dark' ? 'light' : 'dark'

  switch (tone) {
    case 'success':
      return theme.palette.success[contrastScale]
    case 'error':
      return theme.palette.error[contrastScale]
    case 'warning':
      return theme.palette.warning[contrastScale]
    case 'info':
      return theme.palette.info[contrastScale]
  }
}

export function buildNoticeStyle(theme: Theme, tone: ChatNoticeTone): CSSProperties {
  const color = getNoticeColor(theme, tone)

  return {
    border: `1px solid ${alpha(color, tone === 'warning' ? 0.4 : 0.35)}`,
    background: alpha(color, tone === 'warning' ? 0.12 : 0.08),
    color,
  }
}

export function buildAssistantNoticeStyle(theme: Theme, tone: ChatNoticeTone): CSSProperties {
  const color = getNoticeColor(theme, tone)
  const textColor = theme.palette.secondary.contrastText

  return {
    border: `1px solid ${alpha(textColor, 0.32)}`,
    borderLeft: `3px solid ${alpha(color, 0.9)}`,
    background: alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.2 : 0.18),
    color: textColor,
  }
}

export function buildSolidButtonStyle(
  theme: Theme,
  backgroundColor: string,
  disabled: boolean,
): Pick<CSSProperties, 'backgroundColor' | 'color' | 'cursor'> {
  if (disabled) {
    return {
      backgroundColor: theme.palette.action.disabledBackground,
      color: theme.palette.text.disabled,
      cursor: 'not-allowed',
    }
  }

  return {
    backgroundColor,
    color: theme.palette.getContrastText(backgroundColor),
    cursor: 'pointer',
  }
}

export function buildChatCssVariables(theme: Theme): ChatCssVariables {
  const isDark = theme.palette.mode === 'dark'
  const actionBaseColor = isDark ? theme.palette.common.white : theme.palette.text.primary
  const warningColor = getNoticeColor(theme, 'warning')

  return {
    '--chat-text-primary': theme.palette.text.primary,
    '--chat-text-secondary': theme.palette.text.secondary,
    '--chat-divider': theme.palette.divider,
    '--chat-subtle-divider': theme.palette.divider,
    '--chat-user-bg': isDark ? theme.palette.grey[800] : theme.palette.grey[100],
    '--chat-user-color': theme.palette.text.primary,
    '--chat-assistant-bg': theme.palette.secondary.main,
    '--chat-assistant-color': theme.palette.secondary.contrastText,
    '--chat-message-shadow': `0 1px 3px ${alpha(theme.palette.common.black, isDark ? 0.28 : 0.12)}`,
    '--chat-action-bg': alpha(actionBaseColor, isDark ? 0.1 : 0.06),
    '--chat-action-border': alpha(actionBaseColor, isDark ? 0.2 : 0.18),
    '--chat-action-color': theme.palette.text.secondary,
    '--chat-action-hover-bg': alpha(actionBaseColor, isDark ? 0.18 : 0.1),
    '--chat-action-hover-color': theme.palette.text.primary,
    '--chat-input-border': alpha(theme.palette.text.primary, isDark ? 0.23 : 0.28),
    '--chat-input-focus-border': theme.palette.primary.main,
    '--chat-input-placeholder': alpha(theme.palette.text.primary, isDark ? 0.5 : 0.45),
    '--chat-send-bg': theme.palette.primary.main,
    '--chat-send-hover-bg': theme.palette.primary.dark,
    '--chat-send-color': theme.palette.primary.contrastText,
    '--chat-send-shadow': `0 2px 4px ${alpha(theme.palette.primary.main, isDark ? 0.3 : 0.24)}`,
    '--chat-send-hover-shadow': `0 4px 8px ${alpha(theme.palette.primary.main, isDark ? 0.4 : 0.32)}`,
    '--chat-send-disabled-bg': theme.palette.action.disabledBackground,
    '--chat-warning-bg': alpha(warningColor, isDark ? 0.18 : 0.16),
    '--chat-warning-color': isDark ? theme.palette.warning.light : theme.palette.text.primary,
    '--chat-warning-border': alpha(warningColor, 0.7),
    '--chat-empty-color': alpha(theme.palette.text.primary, isDark ? 0.5 : 0.52),
    '--chat-scrollbar-track': alpha(theme.palette.text.primary, isDark ? 0.05 : 0.08),
    '--chat-scrollbar-thumb': alpha(theme.palette.text.primary, isDark ? 0.15 : 0.22),
    '--chat-scrollbar-thumb-hover': alpha(theme.palette.text.primary, isDark ? 0.25 : 0.32),
  }
}

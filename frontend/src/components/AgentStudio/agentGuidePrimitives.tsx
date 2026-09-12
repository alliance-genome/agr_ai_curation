/**
 * Shared presentation primitives for the Agent Browser detail panel.
 *
 * Section headings, state dots, and the monospace font stack used by the
 * Guide, Envelope, and Prompts tabs.
 */

import type { ReactNode } from 'react'
import { Box, Typography } from '@mui/material'
import { alpha } from '@mui/material/styles'
import type { SxProps, Theme } from '@mui/material/styles'

export const MONO_FONT_FAMILY = '"Geist Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

/** Width below which the Browser hides the agent list and shows the Back control. */
export const NARROW_BROWSER_WIDTH = 720

export const sectionHeadingSx: SxProps<Theme> = {
  display: 'flex',
  alignItems: 'center',
  gap: 1,
  m: 0,
  fontSize: 11,
  fontWeight: 500,
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  color: 'text.secondary',
}

/** Sticky-capable header cell style shared by the guide and envelope tables. */
export const tableHeadCellSx = {
  fontSize: 11,
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  fontWeight: 500,
  color: 'text.secondary',
  py: 0.75,
  borderBottom: 1,
  borderColor: 'divider',
  backgroundColor: (theme: Theme) => (
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.common.white, 0.06)
      : alpha(theme.palette.primary.main, 0.06)
  ),
} as const

interface SectionHeadingProps {
  children: ReactNode
  id?: string
  /** Optional trailing control, pushed to the right edge. */
  action?: ReactNode
}

export function SectionHeading({ children, id, action }: SectionHeadingProps) {
  return (
    <Typography component="h3" id={id} sx={sectionHeadingSx}>
      {children}
      {action && <Box component="span" sx={{ ml: 'auto', textTransform: 'none', letterSpacing: 0 }}>{action}</Box>}
    </Typography>
  )
}

export type StateDotTone = 'active' | 'under_development' | 'unavailable' | 'none'

const STATE_DOT_LABEL: Record<StateDotTone, string> = {
  active: 'Active',
  under_development: 'Under development',
  unavailable: 'Unavailable',
  none: 'Not checked',
}

const STATE_DOT_COLOR: Record<StateDotTone, string> = {
  active: 'success.main',
  under_development: 'warning.main',
  unavailable: 'warning.main',
  none: 'text.disabled',
}

export function StateDot({ tone }: { tone: StateDotTone }) {
  return (
    <Box
      component="span"
      role="img"
      aria-label={STATE_DOT_LABEL[tone]}
      sx={{
        width: 8,
        height: 8,
        borderRadius: '50%',
        flex: 'none',
        display: 'inline-block',
        backgroundColor: STATE_DOT_COLOR[tone],
      }}
    />
  )
}

/** Count pill used next to headings and list items. */
export function CountPill({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <Box
      component="span"
      aria-label={label}
      sx={{
        fontSize: 11,
        fontWeight: 500,
        px: 0.875,
        py: '1px',
        borderRadius: 999,
        backgroundColor: 'action.hover',
        color: 'text.secondary',
        lineHeight: 1.5,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </Box>
  )
}

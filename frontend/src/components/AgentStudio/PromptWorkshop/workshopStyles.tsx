import type { ReactNode } from 'react'
import { Box, Typography } from '@mui/material'
import { alpha, styled } from '@mui/material/styles'

export const MONO_FONT = '"Geist Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace'

/** Width below which the navigation column becomes a row above the content. */
export const NARROW_QUERY = '@container workshop (max-width: 719px)'

export const CONTENT_MAX_WIDTH = 780

/** Uppercase section label with an optional right-aligned action. */
export function SectionHeading({ children, action, id }: { children: ReactNode; action?: ReactNode; id?: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minHeight: 24 }}>
      <Typography
        id={id}
        component="h3"
        sx={{
          fontSize: 11,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          color: 'text.secondary',
          fontWeight: 500,
          m: 0,
        }}
      >
        {children}
      </Typography>
      {action ? <Box sx={{ ml: 'auto', display: 'inline-flex', alignItems: 'center' }}>{action}</Box> : null}
    </Box>
  )
}

export const Section = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(1),
  maxWidth: CONTENT_MAX_WIDTH,
}))

export const HelpText = styled(Typography)(({ theme }) => ({
  fontSize: 12.5,
  color: theme.palette.text.secondary,
  maxWidth: '70ch',
  margin: 0,
}))

export const FieldRow = styled(Box)(({ theme }) => ({
  display: 'flex',
  gap: theme.spacing(1.5),
  alignItems: 'flex-start',
  flexWrap: 'wrap',
}))

/** Bordered pane with a header strip; used for the prompt editor and read-only layers. */
export const EditorFrame = styled(Box)(({ theme }) => ({
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: 6,
  overflow: 'hidden',
}))

export const EditorHeader = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1.25),
  padding: theme.spacing(0.75, 1.25),
  backgroundColor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.1 : 0.06),
  borderBottom: `1px solid ${theme.palette.divider}`,
  fontSize: 12,
  color: theme.palette.text.secondary,
}))

export const ReadOnlyBody = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.25, 1.5),
  fontFamily: MONO_FONT,
  fontSize: 12.5,
  lineHeight: 1.55,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  minHeight: 200,
  maxHeight: 480,
  overflow: 'auto',
  backgroundColor: theme.palette.background.default,
  color: theme.palette.text.secondary,
}))

export const InfoNote = styled(Box)(({ theme }) => ({
  display: 'flex',
  gap: theme.spacing(1.25),
  alignItems: 'flex-start',
  padding: theme.spacing(1, 1.5),
  borderRadius: 6,
  backgroundColor: alpha(theme.palette.info.main, 0.1),
  fontSize: 12.5,
  '&[data-tone="warning"]': {
    backgroundColor: alpha(theme.palette.warning.main, 0.12),
  },
}))

export const DataTable = styled(Box)(({ theme }) => ({
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: 6,
  overflow: 'hidden',
  '& table': {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  },
  '& th': {
    textAlign: 'left',
    fontSize: 11,
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    fontWeight: 500,
    color: theme.palette.text.secondary,
    backgroundColor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.1 : 0.06),
    padding: theme.spacing(0.875, 1.5),
    borderBottom: `1px solid ${theme.palette.divider}`,
  },
  '& td': {
    padding: theme.spacing(0.875, 1.5),
    borderBottom: `1px solid ${theme.palette.divider}`,
    verticalAlign: 'middle',
  },
  '& tbody tr:last-of-type td': {
    borderBottom: 0,
  },
}))

export const LinkButtonSx = {
  textTransform: 'none',
  fontSize: 12.5,
  fontWeight: 500,
  px: 0.75,
  py: 0.25,
  minWidth: 0,
  minHeight: 0,
  lineHeight: 1.4,
} as const

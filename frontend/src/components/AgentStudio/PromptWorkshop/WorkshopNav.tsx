import { Box, ButtonBase, Typography } from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh'

import type { DraftDirtyState, WorkshopSection } from './workshopDraftUtils'
import { NARROW_QUERY } from './workshopStyles'

export interface WorkshopNavProps {
  section: WorkshopSection
  onSectionChange: (section: WorkshopSection) => void
  dirty: DraftDirtyState
  toolCount: number
  versionCount: number
  showOutputStructure?: boolean
  onAskClaude?: () => void
}

interface NavItem {
  key: WorkshopSection
  label: string
  dirty: boolean
  hint?: string
}

const itemSx: SxProps<Theme> = {
  display: 'flex',
  alignItems: 'center',
  gap: 1,
  width: '100%',
  px: 1.25,
  py: 0.875,
  borderRadius: 1.5,
  fontSize: 13,
  color: 'text.secondary',
  textAlign: 'left',
  justifyContent: 'flex-start',
  boxShadow: 'inset 3px 0 0 transparent',
  '&:hover': { backgroundColor: 'action.hover' },
  '&.Mui-focusVisible': {
    outline: (theme) => `2px solid ${theme.palette.primary.main}`,
    outlineOffset: -2,
  },
  '&[aria-current="page"]': {
    backgroundColor: 'action.selected',
    color: 'text.primary',
    fontWeight: 500,
    boxShadow: (theme) => `inset 3px 0 0 ${theme.palette.primary.main}`,
  },
  [NARROW_QUERY]: {
    width: 'auto',
    py: 0.75,
    px: 1.25,
    borderRadius: 0,
    boxShadow: 'inset 0 -2px 0 transparent',
    '&[aria-current="page"]': {
      backgroundColor: 'transparent',
      boxShadow: (theme) => `inset 0 -2px 0 ${theme.palette.primary.main}`,
      color: 'primary.main',
    },
  },
}

const askClaudeSx: SxProps<Theme> = [itemSx, { [NARROW_QUERY]: { ml: 'auto' } }] as SxProps<Theme>

export default function WorkshopNav({
  section,
  onSectionChange,
  dirty,
  toolCount,
  versionCount,
  showOutputStructure = false,
  onAskClaude,
}: WorkshopNavProps) {
  const items: NavItem[] = [
    { key: 'setup', label: 'Setup', dirty: dirty.setup },
    ...(showOutputStructure ? [{ key: 'output_structure' as const, label: 'Output Structure', dirty: dirty.outputStructure }] : []),
    { key: 'prompt', label: 'Prompt', dirty: dirty.prompt || dirty.groups.length > 0 },
    { key: 'tools', label: 'Tools', dirty: dirty.tools, hint: `${toolCount} attached` },
    { key: 'versions', label: 'Versions', dirty: false, hint: String(versionCount) },
  ]

  return (
    <Box
      component="nav"
      aria-label="Agent Workshop sections"
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 0.25,
        p: 1,
        borderRight: (theme) => `1px solid ${theme.palette.divider}`,
        [NARROW_QUERY]: {
          flexDirection: 'row',
          alignItems: 'center',
          gap: 0.25,
          px: 2,
          pt: 0.75,
          pb: 0,
          borderRight: 0,
          borderBottom: (theme) => `1px solid ${theme.palette.divider}`,
          overflowX: 'auto',
        },
      }}
    >
      {items.map((item) => {
        const selected = item.key === section
        const description = [item.hint, item.dirty ? 'unsaved edits' : null].filter(Boolean).join(', ')
        return (
          <ButtonBase
            key={item.key}
            aria-current={selected ? 'page' : undefined}
            aria-label={description ? `${item.label}, ${description}` : item.label}
            onClick={() => onSectionChange(item.key)}
            sx={itemSx}
          >
            <span>{item.label}</span>
            {item.dirty && (
              <Box
                aria-hidden
                sx={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  backgroundColor: 'warning.main',
                  ml: 'auto',
                  flexShrink: 0,
                }}
              />
            )}
            {!item.dirty && item.hint && (
              <Typography
                component="span"
                aria-hidden
                sx={{ ml: 'auto', fontSize: 11, color: 'text.disabled', whiteSpace: 'nowrap' }}
              >
                {item.hint}
              </Typography>
            )}
          </ButtonBase>
        )
      })}
      {onAskClaude && (
        <>
          <Typography
            component="div"
            sx={{
              fontSize: 11,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: 'text.disabled',
              px: 1.25,
              pt: 1.25,
              pb: 0.5,
              [NARROW_QUERY]: { display: 'none' },
            }}
          >
            Help
          </Typography>
          <ButtonBase onClick={onAskClaude} sx={askClaudeSx}>
            <AutoFixHighIcon sx={{ fontSize: 15 }} />
            <span>Ask AI Chat</span>
          </ButtonBase>
        </>
      )}
    </Box>
  )
}

import { useEffect, useState } from 'react'
import type { MouseEvent } from 'react'
import { Box, Button, CircularProgress, Divider, IconButton, Menu, MenuItem, Typography } from '@mui/material'
import { alpha } from '@mui/material/styles'
import AddIcon from '@mui/icons-material/Add'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import FolderOpenOutlinedIcon from '@mui/icons-material/FolderOpenOutlined'
import MoreVertIcon from '@mui/icons-material/MoreVert'

import { formatRelativeTime, type SaveState } from './workshopDraftUtils'

export interface WorkshopHeaderProps {
  icon: string
  name: string
  originLabel: string
  saveState: SaveState
  lastSavedAt: number | null
  dirty: boolean
  canSave: boolean
  canDelete: boolean
  saving: boolean
  onOpen: () => void
  onNew: () => void
  onSave: () => void
  onSaveAs: () => void
  onManage: () => void
  onDelete: () => void
}

type PillTone = 'warning' | 'success' | 'error' | 'neutral'

function StatusPill({ tone, icon, label }: { tone: PillTone; icon: React.ReactNode; label: string }) {
  return (
    <Box
      role="status"
      sx={(theme) => {
        const color = tone === 'neutral' ? theme.palette.text.secondary : theme.palette[tone].main
        return {
          display: 'inline-flex',
          alignItems: 'center',
          gap: 0.75,
          fontSize: 12,
          px: 1,
          py: 0.25,
          borderRadius: 999,
          whiteSpace: 'nowrap',
          color: tone === 'neutral' ? theme.palette.text.secondary : (theme.palette.mode === 'dark' ? theme.palette[tone].light : theme.palette[tone].dark),
          backgroundColor: tone === 'neutral' ? theme.palette.action.hover : alpha(color, 0.14),
        }
      }}
    >
      {icon}
      {label}
    </Box>
  )
}

function SaveStatus({ saveState, lastSavedAt, dirty, saving }: Pick<WorkshopHeaderProps, 'saveState' | 'lastSavedAt' | 'dirty' | 'saving'>) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!lastSavedAt) return
    setNow(Date.now())
    const interval = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(interval)
  }, [lastSavedAt])

  if (saving || saveState === 'saving') {
    return <StatusPill tone="neutral" icon={<CircularProgress size={12} thickness={5} />} label="Saving…" />
  }
  if (saveState === 'failed') {
    return <StatusPill tone="error" icon={<CloseIcon sx={{ fontSize: 14 }} />} label="Save failed" />
  }
  if (dirty) {
    return <StatusPill tone="warning" icon={<EditOutlinedIcon sx={{ fontSize: 14 }} />} label="Unsaved changes" />
  }
  if (saveState === 'saved' && lastSavedAt) {
    return (
      <StatusPill
        tone="success"
        icon={<CheckIcon sx={{ fontSize: 14 }} />}
        label={`Saved ${formatRelativeTime(lastSavedAt, now)}`}
      />
    )
  }
  return null
}

export default function WorkshopHeader({
  icon,
  name,
  originLabel,
  saveState,
  lastSavedAt,
  dirty,
  canSave,
  canDelete,
  saving,
  onOpen,
  onNew,
  onSave,
  onSaveAs,
  onManage,
  onDelete,
}: WorkshopHeaderProps) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const displayName = name.trim() || 'New agent'

  const openMenu = (event: MouseEvent<HTMLElement>) => setMenuAnchor(event.currentTarget)
  const closeMenu = () => setMenuAnchor(null)
  const runFromMenu = (action: () => void) => () => {
    closeMenu()
    action()
  }

  return (
    <Box
      component="header"
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        px: 2,
        py: 1.25,
        minHeight: 56,
        borderBottom: (theme) => `1px solid ${theme.palette.divider}`,
        flexWrap: 'wrap',
        flexShrink: 0,
      }}
    >
      <Box
        aria-hidden
        sx={{
          width: 32,
          height: 32,
          borderRadius: 2,
          display: 'grid',
          placeItems: 'center',
          fontSize: 15,
          fontWeight: 600,
          flexShrink: 0,
          backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.12),
          color: (theme) => (theme.palette.mode === 'dark' ? theme.palette.primary.light : theme.palette.primary.dark),
        }}
      >
        {icon}
      </Box>
      <Box sx={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: '1 1 260px' }}>
        <Typography
          component="h2"
          sx={{
            fontSize: 16,
            fontWeight: 600,
            lineHeight: 1.3,
            letterSpacing: '-0.01em',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            color: name.trim() ? 'text.primary' : 'text.secondary',
            m: 0,
          }}
        >
          {displayName}
        </Typography>
        <Typography sx={{ fontSize: 12, color: 'text.secondary', lineHeight: 1.4 }}>{originLabel}</Typography>
      </Box>
      <SaveStatus saveState={saveState} lastSavedAt={lastSavedAt} dirty={dirty} saving={saving} />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, ml: 'auto' }}>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<FolderOpenOutlinedIcon sx={{ fontSize: 16 }} />}
          onClick={onOpen}
          sx={{ textTransform: 'none', borderColor: 'divider' }}
        >
          Open
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          startIcon={<AddIcon sx={{ fontSize: 16 }} />}
          onClick={onNew}
          sx={{ textTransform: 'none', borderColor: 'divider' }}
        >
          New
        </Button>
        <Button
          size="small"
          variant="contained"
          disableElevation
          onClick={onSave}
          disabled={!canSave}
          sx={{ textTransform: 'none' }}
        >
          Save
        </Button>
        <IconButton
          size="small"
          aria-label="More actions"
          aria-haspopup="menu"
          aria-controls={menuAnchor ? 'workshop-more-menu' : undefined}
          aria-expanded={menuAnchor ? 'true' : undefined}
          onClick={openMenu}
        >
          <MoreVertIcon fontSize="small" />
        </IconButton>
        <Menu
          id="workshop-more-menu"
          anchorEl={menuAnchor}
          open={Boolean(menuAnchor)}
          onClose={closeMenu}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        >
          <MenuItem onClick={runFromMenu(onSaveAs)} disabled={saving}>Save as…</MenuItem>
          <MenuItem onClick={runFromMenu(onManage)}>Manage agents…</MenuItem>
          <Divider />
          <MenuItem
            onClick={runFromMenu(onDelete)}
            disabled={!canDelete || saving}
            sx={{ color: 'error.main' }}
          >
            Delete agent
          </MenuItem>
        </Menu>
      </Box>
    </Box>
  )
}

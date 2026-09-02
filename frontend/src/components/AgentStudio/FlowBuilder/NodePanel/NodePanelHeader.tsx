/**
 * Pinned header of the node panel: icon, name, "Step N of M", a status pill,
 * then the step-kind label with Cancel and Apply and an overflow menu that
 * holds Delete step. A configuration error pins under the header.
 */

import { useState } from 'react'
import type { MouseEvent } from 'react'
import { Alert, Box, Button, IconButton, Menu, MenuItem, Tooltip, Typography } from '@mui/material'
import { alpha } from '@mui/material/styles'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import CloseIcon from '@mui/icons-material/Close'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline'

export type NodePanelStatus = 'clean' | 'dirty' | 'error'

interface NodePanelHeaderProps {
  icon: string
  name: string
  /** "Step 2 of 4 · disease_extractor v1" */
  subtitle: string
  kindLabel: string
  status: NodePanelStatus
  errorMessage?: string
  applyDisabled: boolean
  /** Drawer mode shows Close; docked mode shows Hide panel. */
  mode: 'docked' | 'drawer'
  onApply: () => void
  onCancel: () => void
  onDelete?: () => void
  onHide: () => void
}

const STATUS_PILL: Record<Exclude<NodePanelStatus, 'clean'>, { label: string; tone: 'warning' | 'error' }> = {
  dirty: { label: 'Unsaved changes', tone: 'warning' },
  error: { label: 'Configuration error', tone: 'error' },
}

function NodePanelHeader({
  icon,
  name,
  subtitle,
  kindLabel,
  status,
  errorMessage,
  applyDisabled,
  mode,
  onApply,
  onCancel,
  onDelete,
  onHide,
}: NodePanelHeaderProps) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const pill = status === 'clean' ? null : STATUS_PILL[status]

  const openMenu = (event: MouseEvent<HTMLElement>) => setMenuAnchor(event.currentTarget)
  const closeMenu = () => setMenuAnchor(null)

  return (
    <Box component="header" sx={{ flex: 'none', borderBottom: 1, borderColor: 'divider' }}>
      <Box sx={{ px: 1.75, pt: 1.25, display: 'flex', flexDirection: 'column', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
          <Box
            aria-hidden="true"
            sx={{
              width: 30,
              height: 30,
              borderRadius: 1.5,
              flex: 'none',
              display: 'grid',
              placeItems: 'center',
              fontSize: 15,
              backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.12),
            }}
          >
            {icon}
          </Box>
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography
              component="h2"
              sx={{ m: 0, fontSize: 14.5, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
              title={name}
            >
              {name}
            </Typography>
            <Typography sx={{ fontSize: 12, color: 'text.secondary', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {subtitle}
            </Typography>
          </Box>
          {pill && (
            <Box
              component="span"
              sx={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 0.5,
                fontSize: 11.5,
                px: 0.875,
                py: '2px',
                borderRadius: 999,
                flex: 'none',
                color: `${pill.tone}.dark`,
                backgroundColor: (theme) => alpha(theme.palette[pill.tone].main, theme.palette.mode === 'dark' ? 0.16 : 0.12),
              }}
            >
              {pill.tone === 'error' && <ErrorOutlineIcon sx={{ fontSize: 14 }} />}
              {pill.label}
            </Box>
          )}
          {onDelete && (
            <>
              <IconButton size="small" aria-label="More step actions" aria-haspopup="menu" onClick={openMenu}>
                <MoreVertIcon fontSize="small" />
              </IconButton>
              <Menu open={Boolean(menuAnchor)} anchorEl={menuAnchor} onClose={closeMenu}>
                <MenuItem
                  onClick={() => {
                    closeMenu()
                    onDelete()
                  }}
                  sx={{ fontSize: 13, color: 'error.main', gap: 1 }}
                >
                  <DeleteOutlineIcon fontSize="small" />
                  Delete step
                </MenuItem>
              </Menu>
            </>
          )}
          <Tooltip title={mode === 'drawer' ? 'Close' : 'Hide panel'}>
            <IconButton size="small" aria-label={mode === 'drawer' ? 'Close panel' : 'Hide panel'} onClick={onHide}>
              {mode === 'drawer' ? <CloseIcon fontSize="small" /> : <ChevronRightIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, pb: 1 }}>
          <Typography sx={{ fontSize: 12, color: 'text.secondary', flex: 1 }}>{kindLabel}</Typography>
          <Button size="small" variant="outlined" onClick={onCancel} sx={{ textTransform: 'none', height: 26, fontSize: 12 }}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            disableElevation
            onClick={onApply}
            disabled={applyDisabled}
            sx={{ textTransform: 'none', height: 26, fontSize: 12 }}
          >
            Apply
          </Button>
        </Box>
      </Box>

      {status === 'error' && (
        <Alert
          severity="error"
          icon={<ErrorOutlineIcon fontSize="inherit" />}
          sx={{ borderRadius: 0, py: 0.25, px: 1.75, '& .MuiAlert-message': { fontSize: 12.5 } }}
        >
          {errorMessage || 'This step has a configuration error.'}
        </Alert>
      )}
    </Box>
  )
}

export default NodePanelHeader

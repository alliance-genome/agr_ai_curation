/**
 * Pinned header of the node panel: icon, name (wraps to two lines), an
 * overflow menu that holds Delete step, and Hide; "Step N of M · agent_id"
 * on its own line; then the step-kind label, the status pill, Cancel, and
 * Apply. A configuration error pins under the header.
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
  /** "Step 2 of 4" */
  stepLabel: string
  /** "disease_extractor v1" or "task input"; the only part that may ellipsize. */
  stepDetail: string
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
  stepLabel,
  stepDetail,
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
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.25 }}>
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
          <Typography
            component="h2"
            sx={{
              m: 0,
              minWidth: 0,
              flex: 1,
              fontSize: 14.5,
              fontWeight: 600,
              lineHeight: 1.3,
              overflowWrap: 'anywhere',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              pt: '5px',
            }}
            title={name}
          >
            {name}
          </Typography>
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

        <Typography
          component="p"
          data-testid="node-panel-step-line"
          sx={{ m: 0, display: 'flex', minWidth: 0, fontSize: 12, color: 'text.secondary' }}
        >
          <Box component="span" sx={{ flex: 'none' }}>{stepLabel}</Box>
          <Box component="span" sx={{ flex: 'none', whiteSpace: 'pre' }}>{' · '}</Box>
          <Box component="span" sx={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={stepDetail}>
            {stepDetail}
          </Box>
        </Typography>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, pb: 1 }}>
          <Typography sx={{ fontSize: 12, color: 'text.secondary', flex: 1, minWidth: 0 }}>{kindLabel}</Typography>
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

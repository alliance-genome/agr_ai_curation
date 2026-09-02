/**
 * NodePanelDock
 *
 * Places the node panel inside the canvas area. Docked mode is a resizable
 * column the canvas shrinks beside; it collapses to a 44px rail. Drawer mode,
 * used when the builder is narrower than 1100px, is a full-height drawer over
 * the canvas.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import { Box, Drawer, IconButton, Tooltip, Typography } from '@mui/material'
import { alpha } from '@mui/material/styles'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'

import { safeGetItem, safeSetItem } from '@/lib/browserStorage'
import {
  NODE_PANEL_DEFAULT_WIDTH,
  NODE_PANEL_MIN_WIDTH,
  NODE_PANEL_RAIL_WIDTH,
  NODE_PANEL_WIDTH_STORAGE_KEY,
  clampNodePanelWidth,
} from './nodePanelLayout'
import type { NodePanelMode } from './nodePanelLayout'

const RESIZE_STEP = 16

function readStoredWidth(): number {
  const result = safeGetItem(() => window.localStorage, NODE_PANEL_WIDTH_STORAGE_KEY, {
    owner: 'preferences',
    key: NODE_PANEL_WIDTH_STORAGE_KEY,
    quiet: true,
  })
  const parsed = result.ok && result.value ? Number.parseInt(result.value, 10) : Number.NaN
  return Number.isFinite(parsed) ? parsed : NODE_PANEL_DEFAULT_WIDTH
}

export interface NodePanelDockProps {
  mode: NodePanelMode
  collapsed: boolean
  /** Width of the canvas area the panel docks into; null before measurement. */
  areaWidth: number | null
  /** Name shown on the collapsed rail. */
  railLabel: string
  onExpand: () => void
  /** Drawer dismissal (scrim click or Escape). */
  onClose: () => void
  children: ReactNode
}

function NodePanelDock({ mode, collapsed, areaWidth, railLabel, onExpand, onClose, children }: NodePanelDockProps) {
  const [width, setWidth] = useState<number>(() => clampNodePanelWidth(readStoredWidth(), null))
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null)

  const applyWidth = useCallback((next: number) => {
    const clamped = clampNodePanelWidth(next, areaWidth)
    setWidth(clamped)
    safeSetItem(() => window.localStorage, NODE_PANEL_WIDTH_STORAGE_KEY, String(clamped), {
      owner: 'preferences',
      key: NODE_PANEL_WIDTH_STORAGE_KEY,
    })
  }, [areaWidth])

  // Keep the panel inside the half-area cap when the canvas area shrinks.
  useEffect(() => {
    setWidth((current) => clampNodePanelWidth(current, areaWidth))
  }, [areaWidth])

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    dragRef.current = { startX: event.clientX, startWidth: width }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) return
    applyWidth(drag.startWidth + (drag.startX - event.clientX))
  }

  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return
    dragRef.current = null
    event.currentTarget.releasePointerCapture(event.pointerId)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      applyWidth(width + RESIZE_STEP)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      applyWidth(width - RESIZE_STEP)
    }
  }

  if (mode === 'drawer') {
    return (
      <Drawer
        anchor="right"
        open
        onClose={onClose}
        ModalProps={{ keepMounted: false }}
        PaperProps={{ sx: { width: { xs: '100%', sm: NODE_PANEL_DEFAULT_WIDTH }, maxWidth: '100%' } }}
      >
        {children}
      </Drawer>
    )
  }

  if (collapsed) {
    return (
      <Box
        data-testid="node-panel-rail"
        sx={{
          width: NODE_PANEL_RAIL_WIDTH,
          flex: 'none',
          borderLeft: 1,
          borderColor: 'divider',
          backgroundColor: 'background.paper',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          pt: 1,
          gap: 1.25,
        }}
      >
        <Tooltip title="Show panel" placement="left">
          <IconButton size="small" aria-label="Show panel" onClick={onExpand} sx={{ color: 'primary.main' }}>
            <ChevronLeftIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Typography
          sx={{
            writingMode: 'vertical-rl',
            transform: 'rotate(180deg)',
            fontSize: 11.5,
            fontWeight: 500,
            color: 'text.secondary',
            maxHeight: 260,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {railLabel}
        </Typography>
      </Box>
    )
  }

  return (
    <Box
      data-testid="node-panel-dock"
      sx={{
        width,
        flex: 'none',
        position: 'relative',
        borderLeft: 1,
        borderColor: 'divider',
        backgroundColor: 'background.paper',
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
      }}
    >
      <Box
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize step panel"
        aria-valuemin={NODE_PANEL_MIN_WIDTH}
        aria-valuenow={width}
        tabIndex={0}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onKeyDown={handleKeyDown}
        sx={{
          position: 'absolute',
          left: -4,
          top: 0,
          bottom: 0,
          width: 8,
          cursor: 'col-resize',
          zIndex: 2,
          '&:hover, &:focus-visible': {
            backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.35),
            outline: 'none',
          },
        }}
      />
      {children}
    </Box>
  )
}

export default NodePanelDock

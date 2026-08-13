import {
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  Box,
  IconButton,
  Paper,
  Portal,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'
import { alpha, useTheme } from '@mui/material/styles'
import CloseIcon from '@mui/icons-material/Close'
import DragIndicatorIcon from '@mui/icons-material/DragIndicator'

type SurfaceWidth = 'sm' | 'md' | number

interface ModelessFeedbackSurfaceProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  actions?: ReactNode
  titleIcon?: ReactNode
  width?: SurfaceWidth
  sx?: SxProps<Theme>
  moveControlLabel?: string
  closeControlLabel?: string
}

interface Position {
  x: number
  y: number
}

interface DragState {
  startX: number
  startY: number
  originX: number
  originY: number
}

const VIEWPORT_MARGIN = 16
const KEYBOARD_NUDGE = 24
const DEFAULT_PANEL_HEIGHT = 420
const MIN_PANEL_WIDTH = 360
const MIN_PANEL_HEIGHT = 300

function resolveWidth(width: SurfaceWidth): number {
  if (typeof width === 'number') {
    return width
  }

  return width === 'md' ? 720 : 560
}

function getViewportSize() {
  return {
    width: window.innerWidth || document.documentElement.clientWidth,
    height: window.innerHeight || document.documentElement.clientHeight,
  }
}

function getDefaultPosition(panelWidth: number, panelHeight: number): Position {
  const viewport = getViewportSize()
  const x = (viewport.width - panelWidth) / 2
  const y = (viewport.height - panelHeight) / 2

  return clampPosition({ x, y }, panelWidth, panelHeight)
}

function clampPosition(position: Position, panelWidth: number, panelHeight: number): Position {
  const viewport = getViewportSize()
  const maxX = Math.max(VIEWPORT_MARGIN, viewport.width - panelWidth - VIEWPORT_MARGIN)
  const maxY = Math.max(VIEWPORT_MARGIN, viewport.height - panelHeight - VIEWPORT_MARGIN)

  return {
    x: Math.min(Math.max(VIEWPORT_MARGIN, position.x), maxX),
    y: Math.min(Math.max(VIEWPORT_MARGIN, position.y), maxY),
  }
}

function ModelessFeedbackSurface({
  open,
  title,
  onClose,
  children,
  actions,
  titleIcon,
  width = 'sm',
  sx,
  moveControlLabel = 'Move popup',
  closeControlLabel = 'Close popup',
}: ModelessFeedbackSurfaceProps) {
  const theme = useTheme()
  const titleId = useId()
  const isSmallScreen = useMediaQuery(theme.breakpoints.down('sm'))
  const panelWidth = useMemo(() => resolveWidth(width), [width])
  const panelRef = useRef<HTMLDivElement | null>(null)
  const dragStateRef = useRef<DragState | null>(null)
  const [position, setPosition] = useState<Position>(() => getDefaultPosition(panelWidth, DEFAULT_PANEL_HEIGHT))

  const getPanelSize = useCallback(() => {
    const rect = panelRef.current?.getBoundingClientRect()
    return {
      width: rect?.width || panelWidth,
      height: rect?.height || DEFAULT_PANEL_HEIGHT,
    }
  }, [panelWidth])

  const clampCurrentPosition = useCallback((nextPosition: Position) => {
    const size = getPanelSize()
    return clampPosition(nextPosition, size.width, size.height)
  }, [getPanelSize])

  useEffect(() => {
    if (!open || isSmallScreen) {
      dragStateRef.current = null
      return
    }

    setPosition(getDefaultPosition(panelWidth, DEFAULT_PANEL_HEIGHT))
  }, [isSmallScreen, open, panelWidth])

  useLayoutEffect(() => {
    if (!open || isSmallScreen) {
      return
    }

    setPosition((currentPosition) => clampCurrentPosition(currentPosition))
  }, [clampCurrentPosition, isSmallScreen, open])

  useLayoutEffect(() => {
    if (!open || isSmallScreen) {
      return
    }

    const size = getPanelSize()
    setPosition(getDefaultPosition(size.width, size.height))
  }, [getPanelSize, isSmallScreen, open])

  useEffect(() => {
    if (!open || isSmallScreen) {
      return
    }

    const handleResize = () => {
      setPosition((currentPosition) => clampCurrentPosition(currentPosition))
    }

    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
    }
  }, [clampCurrentPosition, isSmallScreen, open])

  useEffect(() => {
    if (!open || isSmallScreen || !panelRef.current || typeof ResizeObserver === 'undefined') {
      return
    }

    const observer = new ResizeObserver(() => {
      setPosition((currentPosition) => clampCurrentPosition(currentPosition))
    })
    observer.observe(panelRef.current)

    return () => {
      observer.disconnect()
    }
  }, [clampCurrentPosition, isSmallScreen, open])

  const handlePointerMove = useCallback((event: PointerEvent) => {
    const dragState = dragStateRef.current
    if (!dragState) {
      return
    }

    const nextPosition = {
      x: dragState.originX + event.clientX - dragState.startX,
      y: dragState.originY + event.clientY - dragState.startY,
    }
    setPosition(clampCurrentPosition(nextPosition))
  }, [clampCurrentPosition])

  const handlePointerUp = useCallback(() => {
    dragStateRef.current = null
    window.removeEventListener('pointermove', handlePointerMove)
    window.removeEventListener('pointerup', handlePointerUp)
  }, [handlePointerMove])

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
    }
  }, [handlePointerMove, handlePointerUp])

  useEffect(() => {
    if (!open) {
      return
    }

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        const activeDialog = document.activeElement?.closest('[role="dialog"]')
        if (activeDialog && activeDialog !== panelRef.current && !panelRef.current?.contains(activeDialog)) {
          return
        }

        event.stopPropagation()
        onClose()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [onClose, open])

  const handleDragStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (isSmallScreen) {
      return
    }

    event.preventDefault()
    dragStateRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
    }
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
  }

  const handleDragKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (isSmallScreen) {
      return
    }

    const movement: Partial<Position> = {}
    if (event.key === 'ArrowLeft') movement.x = position.x - KEYBOARD_NUDGE
    if (event.key === 'ArrowRight') movement.x = position.x + KEYBOARD_NUDGE
    if (event.key === 'ArrowUp') movement.y = position.y - KEYBOARD_NUDGE
    if (event.key === 'ArrowDown') movement.y = position.y + KEYBOARD_NUDGE

    if (movement.x === undefined && movement.y === undefined) {
      return
    }

    event.preventDefault()
    setPosition(clampCurrentPosition({
      x: movement.x ?? position.x,
      y: movement.y ?? position.y,
    }))
  }

  if (!open) {
    return null
  }

  return (
    <Portal>
      <Paper
        ref={panelRef}
        data-testid="modeless-feedback-surface"
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        elevation={8}
        sx={[
          (surfaceTheme) => ({
            position: 'fixed',
            zIndex: surfaceTheme.zIndex.modal + 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            resize: 'both',
            bgcolor: 'background.paper',
            color: 'text.primary',
            border: `1px solid ${surfaceTheme.palette.divider}`,
            borderRadius: 1,
            boxShadow: surfaceTheme.shadows[8],
            width: panelWidth,
            height: DEFAULT_PANEL_HEIGHT,
            minWidth: MIN_PANEL_WIDTH,
            minHeight: MIN_PANEL_HEIGHT,
            maxWidth: `calc(100vw - ${VIEWPORT_MARGIN * 2}px)`,
            maxHeight: `calc(100vh - ${VIEWPORT_MARGIN * 2}px)`,
            boxSizing: 'border-box',
            left: position.x,
            top: position.y,
            [surfaceTheme.breakpoints.down('sm')]: {
              left: VIEWPORT_MARGIN / 2,
              right: VIEWPORT_MARGIN / 2,
              top: 'auto',
              bottom: VIEWPORT_MARGIN / 2,
              width: 'auto',
              height: 'auto',
              minWidth: 0,
              minHeight: 0,
              maxWidth: 'none',
              maxHeight: 'min(78vh, 640px)',
              resize: 'none',
            },
          }),
          ...(Array.isArray(sx) ? sx : [sx]),
        ]}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            px: 2,
            py: 1.25,
            borderBottom: `1px solid ${theme.palette.divider}`,
            bgcolor: alpha(theme.palette.primary.main, 0.04),
            flexShrink: 0,
            userSelect: 'none',
          }}
        >
          <Box
            onPointerDown={handleDragStart}
            onKeyDown={handleDragKeyboard}
            role={isSmallScreen ? undefined : 'button'}
            tabIndex={isSmallScreen ? undefined : 0}
            aria-label={isSmallScreen ? undefined : moveControlLabel}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              flex: 1,
              minWidth: 0,
              cursor: isSmallScreen ? 'default' : 'move',
              '&:focus-visible': {
                outline: `2px solid ${theme.palette.primary.main}`,
                outlineOffset: 2,
              },
            }}
          >
            {titleIcon}
            {!isSmallScreen && (
              <Tooltip title="Drag to move">
                <DragIndicatorIcon fontSize="small" sx={{ color: 'text.secondary', flexShrink: 0 }} />
              </Tooltip>
            )}
            <Typography id={titleId} variant="h6" component="h2" sx={{ flex: 1, fontSize: '1.05rem' }}>
              {title}
            </Typography>
          </Box>
          <Tooltip title="Close">
            <IconButton size="small" onClick={onClose} aria-label={closeControlLabel}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        <Box sx={{ display: 'flex', flex: 1, flexDirection: 'column', minHeight: 0, overflow: 'auto', px: 3, py: 2 }}>
          {children}
        </Box>

        {actions && (
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 1,
              px: 3,
              py: 2,
              flexShrink: 0,
              borderTop: `1px solid ${theme.palette.divider}`,
            }}
          >
            {actions}
          </Box>
        )}
      </Paper>
    </Portal>
  )
}

export default ModelessFeedbackSurface

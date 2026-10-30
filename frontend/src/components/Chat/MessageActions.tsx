import React, { useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Box, ClickAwayListener, IconButton, Menu, MenuItem, Tooltip, Divider } from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'
import MoreHorizIcon from '@mui/icons-material/MoreHoriz'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import FingerprintIcon from '@mui/icons-material/Fingerprint'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import RateReviewIcon from '@mui/icons-material/RateReview'

import ReviewAndCurateButton from '@/features/curation/components/ReviewAndCurateButton'
import type { CurationWorkspaceLaunchTarget } from '@/features/curation/navigation/openCurationWorkspace'

import { copyText } from './copyText'

interface MessageActionsProps {
  messageContent: string
  sessionId?: string
  traceId?: string
  onFeedbackClick: () => void
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
  onReviewAndCurateOpened?: (sessionId: string) => void
}

const actionButtonSx: SxProps<Theme> = (theme) => ({
  color: theme.palette.text.primary,
  borderRadius: '5px',
  width: 40,
  height: 40,
  '&:hover': { backgroundColor: theme.palette.action.hover },
  '&:focus-visible': { outline: `2px solid ${theme.palette.primary.main}`, outlineOffset: '-2px' },
  '@media (hover: none)': { width: 44, height: 44 },
})

const menuSx: SxProps<Theme> = (theme) => ({
  '& .MuiPaper-root': {
    backgroundColor: theme.palette.background.paper,
    color: theme.palette.text.primary,
    border: `1px solid ${theme.palette.divider}`,
  }
})

function MessageActions({
  messageContent,
  sessionId,
  traceId,
  onFeedbackClick,
  reviewAndCurateTarget,
  onReviewAndCurateOpened,
}: MessageActionsProps) {
  const navigate = useNavigate()
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null)
  const menuOpen = Boolean(anchorEl)
  const rootRef = useRef<HTMLDivElement>(null)
  const disclosureRef = useRef<HTMLButtonElement>(null)
  const feedbackPendingRef = useRef(false)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const menuId = useId()
  const toolbarId = useId()
  const expanded = menuOpen || (!dismissed && (hovered || focused || pinned))

  // Hover belongs to the whole message; controls retain their own focus/pin lifecycle.
  useEffect(() => {
    const message = rootRef.current?.closest('.assistant-message')
    if (!message) return
    const enter = (event: Event) => {
      if ((event as PointerEvent).pointerType !== 'mouse') return
      setHovered(true)
      setDismissed(false)
    }
    const leave = () => setHovered(false)
    message.addEventListener('pointerenter', enter)
    message.addEventListener('pointerleave', leave)
    return () => {
      message.removeEventListener('pointerenter', enter)
      message.removeEventListener('pointerleave', leave)
    }
  }, [])

  const dismissToolbar = (restoreFocus = false) => {
    setPinned(false)
    setDismissed(true)
    if (restoreFocus) disclosureRef.current?.focus()
  }

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setPinned(true)
    setDismissed(false)
    setAnchorEl(event.currentTarget)
  }

  const handleMenuClose = () => {
    setAnchorEl(null)
  }

  const handleCopyMessage = () => {
    copyText(messageContent).catch(err => {
      console.error('Failed to copy:', err)
    })
  }
  
  const handleCopyTraceId = () => {
    if (traceId) {
      handleMenuClose()
      copyText(traceId).catch(err => {
        console.error('Failed to copy trace ID:', err)
      })
    }
  }

  const handleProvideFeedback = () => {
    // Finish menu focus restoration before opening another feedback surface.
    feedbackPendingRef.current = true
    handleMenuClose()
  }

  const handleOpenInAgentStudio = () => {
    handleMenuClose()
    const params = new URLSearchParams()

    if (sessionId?.trim()) {
      params.set('session_id', sessionId.trim())
    }

    if (traceId?.trim()) {
      params.set('trace_id', traceId.trim())
    }

    const query = params.toString()
    navigate(query ? `/agent-studio?${query}` : '/agent-studio')
  }

  return (
    <ClickAwayListener onClickAway={() => { if (!menuOpen) dismissToolbar() }}>
    <Box
      ref={rootRef}
      className="message-actions"
      onFocus={(event) => {
        if (!event.currentTarget.contains(event.target)) return
        setFocused(true)
        if (!event.currentTarget.contains(event.relatedTarget)) setDismissed(false)
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !menuOpen && expanded) {
          event.stopPropagation()
          dismissToolbar(true)
        }
      }}
      sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1, mt: 1.5 }}
    >
      <IconButton
        ref={disclosureRef}
        aria-label={pinned ? 'Hide message actions' : 'Show message actions'}
        aria-expanded={expanded}
        aria-controls={toolbarId}
        onClick={() => {
          if (pinned) dismissToolbar(true)
          else { setPinned(true); setDismissed(false) }
        }}
        sx={(theme) => ({
          color: theme.palette.secondary.contrastText,
          backgroundColor: theme.palette.secondary.dark,
          border: `1px solid ${theme.palette.secondary.light}`,
          borderRadius: '6px', width: 32, height: 40,
          '&:hover': { backgroundColor: theme.palette.secondary.dark },
          '&:focus-visible': { outline: `2px solid ${theme.palette.secondary.contrastText}`, outlineOffset: 2 },
          '@media (hover: none)': { width: 44, height: 44 },
        })}
      >
        <MoreHorizIcon fontSize="small" />
      </IconButton>
      <Box
        id={toolbarId}
        role="group"
        aria-label="Message actions"
        sx={(theme) => ({
          visibility: expanded ? 'visible' : 'hidden',
          display: 'flex', flexShrink: 0, gap: 0.5, p: 0.5, borderRadius: '8px',
          backgroundColor: theme.palette.background.paper,
        })}
      >
      {reviewAndCurateTarget && (
        <ReviewAndCurateButton
          sessionId={reviewAndCurateTarget.sessionId}
          documentId={reviewAndCurateTarget.documentId}
          flowRunId={reviewAndCurateTarget.flowRunId}
          originSessionId={reviewAndCurateTarget.originSessionId}
          adapterKeys={reviewAndCurateTarget.adapterKeys}
          iconOnly={true}
          size="small"
          sx={actionButtonSx}
          onOpened={onReviewAndCurateOpened}
        />
      )}

      {/* Copy button */}
      <Tooltip title="Copy message">
      <IconButton
        size="small"
        onClick={handleCopyMessage}
        aria-label="copy message"
        sx={actionButtonSx}
      >
        <ContentCopyIcon fontSize="small" />
      </IconButton>
      </Tooltip>

      {/* Triple-dot menu button */}
      <IconButton
        size="small"
        onClick={handleMenuOpen}
        aria-label="more actions"
        aria-controls={menuOpen ? menuId : undefined}
        aria-haspopup="true"
        aria-expanded={menuOpen ? 'true' : undefined}
        sx={actionButtonSx}
      >
        <MoreVertIcon fontSize="small" />
      </IconButton>

      </Box>

      {/* Menu for actions */}
      <Menu
        id={menuId}
        disableEnforceFocus
        slotProps={{ transition: { onExited: () => {
          if (feedbackPendingRef.current) {
            feedbackPendingRef.current = false
            dismissToolbar(true)
            onFeedbackClick()
          }
        } } }}
        anchorEl={anchorEl}
        open={menuOpen}
        onClose={handleMenuClose}
        anchorOrigin={{
          vertical: 'top',
          horizontal: 'left',
        }}
        transformOrigin={{
          vertical: 'bottom',
          horizontal: 'left',
        }}
        sx={menuSx}
      >
        <MenuItem onClick={handleProvideFeedback}>
          <RateReviewIcon fontSize="small" sx={{ mr: 1 }} />
          Provide Feedback
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />
        <MenuItem onClick={handleOpenInAgentStudio}>
          <AutoAwesomeIcon fontSize="small" sx={{ mr: 1 }} />
          Open in Agent Studio
        </MenuItem>
        {traceId && (
          <MenuItem onClick={handleCopyTraceId}>
            <FingerprintIcon fontSize="small" sx={{ mr: 1 }} />
            Copy Debug ID
          </MenuItem>
        )}
      </Menu>
    </Box>
    </ClickAwayListener>
  )
}

export default MessageActions

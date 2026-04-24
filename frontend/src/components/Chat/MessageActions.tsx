import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { IconButton, Menu, MenuItem, Tooltip, Divider } from '@mui/material'
import { alpha } from '@mui/material/styles'
import type { SxProps, Theme } from '@mui/material/styles'
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
  backgroundColor: alpha(theme.palette.secondary.contrastText, 0.1),
  border: `1px solid ${alpha(theme.palette.secondary.contrastText, 0.2)}`,
  color: alpha(theme.palette.secondary.contrastText, 0.72),
  '&:hover': {
    backgroundColor: alpha(theme.palette.secondary.contrastText, 0.2),
    color: theme.palette.secondary.contrastText,
    transform: 'scale(1.1)'
  },
  transition: 'all 0.2s',
  padding: '0.25rem'
})

const traceActionButtonSx: SxProps<Theme> = (theme) => ({
  backgroundColor: alpha(theme.palette.secondary.contrastText, 0.05),
  border: `1px solid ${alpha(theme.palette.secondary.contrastText, 0.12)}`,
  color: alpha(theme.palette.secondary.contrastText, 0.72),
  '&:hover': {
    backgroundColor: alpha(theme.palette.secondary.contrastText, 0.2),
    color: theme.palette.secondary.contrastText,
    transform: 'scale(1.1)',
    borderColor: alpha(theme.palette.secondary.contrastText, 0.3)
  },
  transition: 'all 0.2s',
  padding: '0.25rem'
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

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget)
  }

  const handleMenuClose = () => {
    setAnchorEl(null)
  }

  const handleCopyMessage = () => {
    copyText(messageContent).then(() => {
      handleMenuClose()
    }).catch(err => {
      console.error('Failed to copy:', err)
    })
  }
  
  const handleCopyTraceId = () => {
    if (traceId) {
      copyText(traceId).then(() => {
        handleMenuClose()
      }).catch(err => {
        console.error('Failed to copy trace ID:', err)
      })
    }
  }

  const handleProvideFeedback = () => {
    handleMenuClose()
    onFeedbackClick()
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
    <div
      style={{
        position: 'absolute',
        bottom: '0.5rem',
        left: '0.5rem',
        display: 'flex',
        gap: '0.25rem',
        opacity: 0,
        transition: 'opacity 0.2s'
      }}
      className="message-actions"
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

      {/* Triple-dot menu button */}
      <IconButton
        size="small"
        onClick={handleMenuOpen}
        aria-label="more actions"
        aria-controls={menuOpen ? 'message-actions-menu' : undefined}
        aria-haspopup="true"
        aria-expanded={menuOpen ? 'true' : undefined}
        sx={actionButtonSx}
      >
        <MoreVertIcon fontSize="small" />
      </IconButton>

      {/* Copy button */}
      <IconButton
        size="small"
        onClick={handleCopyMessage}
        aria-label="copy message"
        sx={actionButtonSx}
      >
        <ContentCopyIcon fontSize="small" />
      </IconButton>

      {/* Trace ID Copy button - only show if traceId exists */}
      {traceId && (
        <Tooltip title="Copy Debug ID">
          <IconButton
            size="small"
            onClick={handleCopyTraceId}
            aria-label="copy debug id"
            sx={traceActionButtonSx}
          >
            <FingerprintIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}

      {/* Menu for actions */}
      <Menu
        id="message-actions-menu"
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
      </Menu>
    </div>
  )
}

export default MessageActions

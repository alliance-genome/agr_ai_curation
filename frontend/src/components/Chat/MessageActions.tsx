import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { IconButton, Menu, MenuItem, Tooltip, Divider } from '@mui/material'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import FingerprintIcon from '@mui/icons-material/Fingerprint'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import RateReviewIcon from '@mui/icons-material/RateReview'

interface MessageActionsProps {
  messageContent: string
  traceId?: string
  onFeedbackClick: () => void
}

function MessageActions({ messageContent, traceId, onFeedbackClick }: MessageActionsProps) {
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
    navigator.clipboard.writeText(messageContent).then(() => {
      handleMenuClose()
    }).catch(err => {
      console.error('Failed to copy:', err)
    })
  }
  
  const handleCopyTraceId = () => {
    if (traceId) {
      navigator.clipboard.writeText(traceId).catch(err => {
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
    // Navigate to Agent Studio with trace context
    if (traceId) {
      navigate(`/agent-studio?trace_id=${traceId}`)
    } else {
      navigate('/agent-studio')
    }
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
      {/* Triple-dot menu button */}
      <IconButton
        size="small"
        onClick={handleMenuOpen}
        aria-label="more actions"
        aria-controls={menuOpen ? 'message-actions-menu' : undefined}
        aria-haspopup="true"
        aria-expanded={menuOpen ? 'true' : undefined}
        sx={{
          backgroundColor: 'rgba(255, 255, 255, 0.1)',
          border: '1px solid rgba(255, 255, 255, 0.2)',
          color: 'rgba(255, 255, 255, 0.7)',
          '&:hover': {
            backgroundColor: 'rgba(255, 255, 255, 0.2)',
            color: '#ffffff',
            transform: 'scale(1.1)'
          },
          transition: 'all 0.2s',
          padding: '0.25rem'
        }}
      >
        <MoreVertIcon fontSize="small" />
      </IconButton>

      {/* Copy button */}
      <IconButton
        size="small"
        onClick={handleCopyMessage}
        aria-label="copy message"
        sx={{
          backgroundColor: 'rgba(255, 255, 255, 0.1)',
          border: '1px solid rgba(255, 255, 255, 0.2)',
          color: 'rgba(255, 255, 255, 0.7)',
          '&:hover': {
            backgroundColor: 'rgba(255, 255, 255, 0.2)',
            color: '#ffffff',
            transform: 'scale(1.1)'
          },
          transition: 'all 0.2s',
          padding: '0.25rem'
        }}
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
            sx={{
              backgroundColor: 'rgba(255, 255, 255, 0.05)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: 'rgba(255, 255, 255, 0.4)',
              '&:hover': {
                backgroundColor: 'rgba(255, 255, 255, 0.2)',
                color: '#ffffff',
                transform: 'scale(1.1)',
                borderColor: 'rgba(255, 255, 255, 0.3)'
              },
              transition: 'all 0.2s',
              padding: '0.25rem'
            }}
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
        sx={{
          '& .MuiPaper-root': {
            backgroundColor: '#2c2c2c',
            color: '#ffffff',
            border: '1px solid rgba(255, 255, 255, 0.12)',
          }
        }}
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

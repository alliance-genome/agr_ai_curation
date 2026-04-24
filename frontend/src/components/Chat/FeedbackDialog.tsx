import { useState, useEffect, useRef } from 'react'
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  Box,
  CircularProgress,
  Alert
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'

interface FeedbackDialogProps {
  open: boolean
  onClose: () => void
  sessionId: string | null
  traceIds?: string[]
  curatorId?: string
  onSubmit: (feedback: {
    session_id: string
    curator_id: string
    feedback_text: string
    trace_ids: string[]
  }) => Promise<void>
}

function FeedbackDialog({
  open,
  onClose,
  sessionId,
  traceIds = [],
  curatorId = 'curator@example.com',
  onSubmit
}: FeedbackDialogProps) {
  const [feedbackText, setFeedbackText] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const resetTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const autoCloseTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearResetTimeout = () => {
    if (resetTimeoutRef.current !== null) {
      clearTimeout(resetTimeoutRef.current)
      resetTimeoutRef.current = null
    }
  }

  const clearAutoCloseTimeout = () => {
    if (autoCloseTimeoutRef.current !== null) {
      clearTimeout(autoCloseTimeoutRef.current)
      autoCloseTimeoutRef.current = null
    }
  }

  // Reset state when dialog opens/closes
  useEffect(() => {
    if (open) {
      clearResetTimeout()
      return undefined
    }

    clearAutoCloseTimeout()
    clearResetTimeout()

    // Reset on close after the dialog animation completes.
    resetTimeoutRef.current = setTimeout(() => {
      setFeedbackText('')
      setError(null)
      setSuccess(false)
      setIsSubmitting(false)
      resetTimeoutRef.current = null
    }, 300)

    return () => {
      clearResetTimeout()
    }
  }, [open])

  useEffect(() => {
    return () => {
      clearResetTimeout()
      clearAutoCloseTimeout()
    }
  }, [])

  const handleCancel = () => {
    clearAutoCloseTimeout()
    onClose()
  }

  const handleSend = async () => {
    if (!feedbackText.trim()) {
      setError('Feedback text cannot be empty')
      return
    }

    if (!sessionId) {
      setError('Session ID is missing')
      return
    }

    // Note: trace_ids can be empty - backend will handle this gracefully
    // Frontend still requires at least session_id for tracking

    setIsSubmitting(true)
    setError(null)

    try {
      await onSubmit({
        session_id: sessionId,
        curator_id: curatorId,
        feedback_text: feedbackText.trim(),
        trace_ids: traceIds
      })

      // Show success animation
      setSuccess(true)

      // Auto-close after 2 seconds
      clearAutoCloseTimeout()
      autoCloseTimeoutRef.current = setTimeout(() => {
        autoCloseTimeoutRef.current = null
        onClose()
      }, 2000)
    } catch (err) {
      console.error('Failed to submit feedback:', err)
      setError(err instanceof Error ? err.message : 'Failed to submit feedback. Please try again.')
      setIsSubmitting(false)
    }
  }

  const isValid = feedbackText.trim().length > 0

  return (
    <Dialog
      open={open}
      onClose={handleCancel}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: (theme) => ({
          backgroundColor: theme.palette.background.paper,
          color: theme.palette.text.primary,
          border: `1px solid ${theme.palette.divider}`,
        })
      }}
    >
      <DialogTitle>Provide Feedback</DialogTitle>
      <DialogContent>
        {success ? (
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              minHeight: '200px',
              gap: 2
            }}
          >
            <CheckCircleIcon
              sx={{
                fontSize: 64,
                color: 'success.main',
                animation: 'fadeInScale 0.5s ease-in-out'
              }}
            />
            <Box sx={{ fontSize: '1.1rem', fontWeight: 500 }}>
              Feedback submitted successfully!
            </Box>
          </Box>
        ) : (
          <>
            {error && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {error}
              </Alert>
            )}
            <TextField
              autoFocus
              multiline
              rows={6}
              fullWidth
              variant="outlined"
              placeholder="Enter your detailed feedback here..."
              value={feedbackText}
              onChange={(e) => setFeedbackText(e.target.value)}
              disabled={isSubmitting}
              sx={(theme) => ({
                '& .MuiOutlinedInput-root': {
                  color: theme.palette.text.primary,
                  '& fieldset': {
                    borderColor: theme.palette.divider,
                  },
                  '&:hover fieldset': {
                    borderColor: alpha(theme.palette.text.primary, 0.4),
                  },
                  '&.Mui-focused fieldset': {
                    borderColor: theme.palette.primary.main,
                  },
                },
                '& .MuiInputBase-input': {
                  color: theme.palette.text.primary,
                },
                '& .MuiInputBase-input::placeholder': {
                  color: theme.palette.text.secondary,
                  opacity: 1,
                }
              })}
            />
          </>
        )}
      </DialogContent>
      {!success && (
        <DialogActions sx={{ padding: '16px 24px' }}>
          <Button
            onClick={handleCancel}
            disabled={isSubmitting}
            sx={{ color: 'text.secondary' }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleSend}
            variant="contained"
            disabled={!isValid || isSubmitting}
            sx={{
              backgroundColor: 'primary.main',
              '&:hover': {
                backgroundColor: 'primary.dark',
              },
              '&:disabled': {
                backgroundColor: 'action.disabledBackground',
                color: 'text.disabled',
              }
            }}
          >
            {isSubmitting ? (
              <>
                <CircularProgress size={20} sx={{ mr: 1 }} />
                Sending...
              </>
            ) : (
              'Send'
            )}
          </Button>
        </DialogActions>
      )}
    </Dialog>
  )
}

export default FeedbackDialog

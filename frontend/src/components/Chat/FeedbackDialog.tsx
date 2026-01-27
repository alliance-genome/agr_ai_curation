import { useState, useEffect } from 'react'
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

  // Reset state when dialog opens/closes
  useEffect(() => {
    if (!open) {
      // Reset on close
      setTimeout(() => {
        setFeedbackText('')
        setError(null)
        setSuccess(false)
        setIsSubmitting(false)
      }, 300) // Wait for dialog close animation
    }
  }, [open])

  const handleCancel = () => {
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
      setTimeout(() => {
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
        sx: {
          backgroundColor: '#2c2c2c',
          color: '#ffffff',
        }
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
                color: '#4caf50',
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
              sx={{
                '& .MuiOutlinedInput-root': {
                  color: '#ffffff',
                  '& fieldset': {
                    borderColor: 'rgba(255, 255, 255, 0.23)',
                  },
                  '&:hover fieldset': {
                    borderColor: 'rgba(255, 255, 255, 0.4)',
                  },
                  '&.Mui-focused fieldset': {
                    borderColor: '#2196f3',
                  },
                },
                '& .MuiInputBase-input': {
                  color: '#ffffff',
                },
                '& .MuiInputBase-input::placeholder': {
                  color: 'rgba(255, 255, 255, 0.5)',
                  opacity: 1,
                }
              }}
            />
          </>
        )}
      </DialogContent>
      {!success && (
        <DialogActions sx={{ padding: '16px 24px' }}>
          <Button
            onClick={handleCancel}
            disabled={isSubmitting}
            sx={{ color: 'rgba(255, 255, 255, 0.7)' }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleSend}
            variant="contained"
            disabled={!isValid || isSubmitting}
            sx={{
              backgroundColor: '#2196f3',
              '&:hover': {
                backgroundColor: '#1976d2',
              },
              '&:disabled': {
                backgroundColor: 'rgba(255, 255, 255, 0.12)',
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

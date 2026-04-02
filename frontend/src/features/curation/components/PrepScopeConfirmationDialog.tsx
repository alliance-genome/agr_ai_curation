import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material'

import type { CurationPrepPreview } from '@/features/curation/services/curationPrepService'

interface PrepScopeConfirmationDialogProps {
  open: boolean
  preview: CurationPrepPreview | null
  supplementalNotice?: string | null
  loading: boolean
  submitting: boolean
  error: string | null
  onClose: () => void
  onConfirm: () => Promise<void> | void
}
function humanizeScopeValue(value: string) {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function displayScopeValues(values: string[]) {
  return values
    .map((value) => value.trim())
    .filter(Boolean)
    .map(humanizeScopeValue)
}

function ScopePill({ label, values }: { label: string; values: string[] }) {
  const displayValues = displayScopeValues(values)

  if (displayValues.length === 0) {
    return null
  }

  return (
    <Box
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 1,
        px: 1.5,
        py: 0.75,
        borderRadius: 999,
        bgcolor: 'rgba(33, 150, 243, 0.12)',
        color: '#90caf9',
        border: '1px solid rgba(144, 202, 249, 0.28)',
      }}
    >
      <Typography component="span" sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
        {label}
      </Typography>
      <Typography component="span" sx={{ fontSize: '0.8rem' }}>
        {displayValues.join(', ')}
      </Typography>
    </Box>
  )
}

function PrepScopeConfirmationDialog({
  open,
  preview,
  supplementalNotice = null,
  loading,
  submitting,
  error,
  onClose,
  onConfirm,
}: PrepScopeConfirmationDialogProps) {
  const confirmDisabled = loading || submitting || !preview?.ready

  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          backgroundColor: '#1f1f1f',
          color: '#ffffff',
        },
      }}
    >
      <DialogTitle>Prepare for Curation</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          {loading ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, py: 2 }}>
              <CircularProgress size={20} />
              <Typography>Loading curation scope...</Typography>
            </Box>
          ) : preview ? (
            <>
              <Typography>{preview.summary_text}</Typography>

              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                  gap: 1.25,
                }}
              >
                <Box>
                  <Typography sx={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.6)' }}>
                    Candidates
                  </Typography>
                  <Typography sx={{ fontSize: '1.1rem', fontWeight: 600 }}>
                    {preview.candidate_count}
                  </Typography>
                </Box>
                <Box>
                  <Typography sx={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.6)' }}>
                    Extraction runs
                  </Typography>
                  <Typography sx={{ fontSize: '1.1rem', fontWeight: 600 }}>
                    {preview.extraction_result_count}
                  </Typography>
                </Box>
                <Box>
                  <Typography sx={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.6)' }}>
                    Messages
                  </Typography>
                  <Typography sx={{ fontSize: '1.1rem', fontWeight: 600 }}>
                    {preview.conversation_message_count}
                  </Typography>
                </Box>
              </Box>

              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                <ScopePill label="Adapters" values={preview.adapter_keys} />
              </Box>

              {supplementalNotice ? (
                <Alert severity="warning">{supplementalNotice}</Alert>
              ) : null}

              {!preview.ready && preview.blocking_reasons.length > 0 && (
                preview.blocking_reasons[0] !== preview.summary_text
                  ? <Alert severity="warning">{preview.blocking_reasons[0]}</Alert>
                  : null
              )}
            </>
          ) : (
            <Typography>Unable to load the current curation scope.</Typography>
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} disabled={submitting} sx={{ color: 'rgba(255, 255, 255, 0.7)' }}>
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          disabled={confirmDisabled}
          sx={{
            backgroundColor: '#2e7d32',
            '&:hover': {
              backgroundColor: '#1b5e20',
            },
            '&:disabled': {
              backgroundColor: 'rgba(255, 255, 255, 0.12)',
            },
          }}
        >
          {submitting ? (
            <>
              <CircularProgress size={18} sx={{ mr: 1, color: 'inherit' }} />
              Preparing...
            </>
          ) : (
            'Start Prep'
          )}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default PrepScopeConfirmationDialog

import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from '@mui/material'

interface RejectReasonDialogProps {
  open: boolean
  reason: string
  submitting: boolean
  onClose: () => void
  onConfirm: () => Promise<void> | void
  onReasonChange: (nextReason: string) => void
}

function RejectReasonDialog({
  open,
  reason,
  submitting,
  onClose,
  onConfirm,
  onReasonChange,
}: RejectReasonDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Reject candidate?</DialogTitle>
      <DialogContent>
        <Typography color="text.secondary" sx={{ mb: 2 }} variant="body2">
          Add an optional reason for the audit log before moving to the next candidate.
        </Typography>
        <TextField
          autoFocus
          fullWidth
          label="Reason (optional)"
          minRows={3}
          multiline
          onChange={(event) => onReasonChange(event.target.value)}
          placeholder="Explain why this candidate was rejected."
          value={reason}
        />
      </DialogContent>
      <DialogActions>
        <Button disabled={submitting} onClick={onClose}>
          Cancel
        </Button>
        <Button
          color="error"
          disabled={submitting}
          onClick={onConfirm}
          variant="contained"
        >
          {submitting ? 'Rejecting...' : 'Reject'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default RejectReasonDialog

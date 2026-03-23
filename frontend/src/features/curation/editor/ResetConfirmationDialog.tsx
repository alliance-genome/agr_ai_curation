import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material'

interface ResetConfirmationDialogProps {
  open: boolean
  submitting: boolean
  onClose: () => void
  onConfirm: () => Promise<void> | void
}

function ResetConfirmationDialog({
  open,
  submitting,
  onClose,
  onConfirm,
}: ResetConfirmationDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      maxWidth="xs"
      fullWidth
    >
      <DialogTitle>Reset candidate?</DialogTitle>
      <DialogContent>
        <Typography color="text.secondary" variant="body2">
          This will restore seeded field values, clear curator notes, remove manual evidence,
          and return the candidate to pending.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button disabled={submitting} onClick={onClose}>
          Cancel
        </Button>
        <Button
          color="inherit"
          disabled={submitting}
          onClick={onConfirm}
          variant="contained"
        >
          {submitting ? 'Resetting...' : 'Reset'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default ResetConfirmationDialog

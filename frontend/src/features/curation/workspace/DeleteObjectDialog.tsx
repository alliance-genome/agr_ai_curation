import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material'

export interface DeleteObjectDialogProps {
  candidateLabel: string
  isDeleting?: boolean
  onCancel: () => void
  onConfirm: () => void
  open: boolean
}

export default function DeleteObjectDialog({
  candidateLabel,
  isDeleting = false,
  onCancel,
  onConfirm,
  open,
}: DeleteObjectDialogProps) {
  return (
    <Dialog
      fullWidth
      maxWidth="xs"
      onClose={isDeleting ? undefined : onCancel}
      open={open}
    >
      <DialogTitle>Delete object?</DialogTitle>
      <DialogContent>
        <Typography sx={{ mt: 0.5 }} variant="body2">
          Delete "{candidateLabel}" from this curation session?
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 1.5 }} variant="body2">
          This permanently removes the candidate, draft, evidence anchors, and validation state for
          this object.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button disabled={isDeleting} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          color="error"
          disabled={isDeleting}
          onClick={onConfirm}
          variant="contained"
        >
          Delete object
        </Button>
      </DialogActions>
    </Dialog>
  )
}

/**
 * Asked when the curator leaves a step that has unapplied edits:
 * Apply, Discard, or Keep editing.
 */

import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from '@mui/material'

interface UnsavedEditsDialogProps {
  open: boolean
  stepNumber: number
  changeSummary: string
  /** Reason Apply is disabled, empty when the draft can be applied. */
  blockingError: string
  onApply: () => void
  onDiscard: () => void
  onKeepEditing: () => void
}

function UnsavedEditsDialog({
  open,
  stepNumber,
  changeSummary,
  blockingError,
  onApply,
  onDiscard,
  onKeepEditing,
}: UnsavedEditsDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onKeepEditing}
      aria-labelledby="node-panel-unsaved-title"
      PaperProps={{ sx: { minWidth: 360, maxWidth: 440, borderRadius: 2 } }}
    >
      <DialogTitle id="node-panel-unsaved-title" sx={{ fontSize: '1rem', pb: 0.5 }}>
        Apply changes to step {stepNumber}?
      </DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ fontSize: 13 }}>
          {changeSummary} Apply them before you leave this step, or discard them.
          {blockingError ? ` ${blockingError}` : ''}
        </DialogContentText>
      </DialogContent>
      <DialogActions sx={{ px: 2.25, pb: 1.75, gap: 0.5 }}>
        <Button onClick={onKeepEditing} size="small" variant="outlined" sx={{ textTransform: 'none' }}>
          Keep editing
        </Button>
        <Button onClick={onDiscard} size="small" variant="outlined" sx={{ textTransform: 'none' }}>
          Discard
        </Button>
        <Button
          onClick={onApply}
          size="small"
          variant="contained"
          disableElevation
          disabled={Boolean(blockingError)}
          sx={{ textTransform: 'none' }}
        >
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default UnsavedEditsDialog

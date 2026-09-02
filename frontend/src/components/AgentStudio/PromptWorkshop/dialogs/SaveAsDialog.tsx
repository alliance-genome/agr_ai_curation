import { useEffect, useState } from 'react'
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from '@mui/material'

export interface SaveAsDialogProps {
  open: boolean
  initialName: string
  saving: boolean
  onConfirm: (name: string) => void
  onClose: () => void
}

export default function SaveAsDialog({ open, initialName, saving, onConfirm, onClose }: SaveAsDialogProps) {
  const [name, setName] = useState(initialName)

  useEffect(() => {
    if (open) setName(initialName)
  }, [open, initialName])

  const trimmed = name.trim()

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="xs" fullWidth aria-labelledby="save-as-title">
      <DialogTitle id="save-as-title" sx={{ pb: 0.5 }}>Save as a new agent</DialogTitle>
      <DialogContent sx={{ pt: 1.5 }}>
        <Typography sx={{ fontSize: 12.5, color: 'text.secondary', mb: 1.5 }}>
          Saves a copy with the current edits. The original agent is not changed.
        </Typography>
        <TextField
          fullWidth
          autoFocus
          size="small"
          label="Agent name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && trimmed && !saving) {
              event.preventDefault()
              onConfirm(trimmed)
            }
          }}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small" disabled={saving}>
          Cancel
        </Button>
        <Button onClick={() => onConfirm(trimmed)} variant="contained" size="small" disabled={saving || !trimmed}>
          Save as
        </Button>
      </DialogActions>
    </Dialog>
  )
}

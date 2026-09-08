import { useEffect, useState } from 'react'
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from '@mui/material'

export interface SaveVersionDialogProps {
  open: boolean
  agentName: string
  /** Version number this save creates. */
  nextVersion: number
  isNewAgent: boolean
  changedSections: string[]
  saving: boolean
  onConfirm: (note: string) => void
  onClose: () => void
}

export default function SaveVersionDialog({
  open,
  agentName,
  nextVersion,
  isNewAgent,
  changedSections,
  saving,
  onConfirm,
  onClose,
}: SaveVersionDialogProps) {
  const [note, setNote] = useState('')

  useEffect(() => {
    if (open) setNote('')
  }, [open])

  const title = isNewAgent ? 'Save new agent' : `Save as version ${nextVersion}`
  const changedLine = isNewAgent
    ? 'Creates version 1 of this agent.'
    : changedSections.length > 0
      ? `Changed since v${nextVersion - 1}: ${changedSections.join(', ')}.`
      : `No changes since v${nextVersion - 1}.`

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="xs" fullWidth aria-labelledby="save-version-title">
      <DialogTitle id="save-version-title" sx={{ pb: 0.5 }}>
        {title}
        <Typography component="div" sx={{ fontSize: 12.5, fontWeight: 400, color: 'text.secondary' }}>
          {agentName.trim() || 'New agent'}
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1.5 }}>
        {!isNewAgent && (
          <TextField
            autoFocus
            size="small"
            label="Note (optional)"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !saving) {
                event.preventDefault()
                onConfirm(note)
              }
            }}
            sx={{ mt: 0.5 }}
          />
        )}
        <Typography sx={{ fontSize: 12.5, color: 'text.secondary' }}>{changedLine}</Typography>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small" disabled={saving}>
          Cancel
        </Button>
        <Button onClick={() => onConfirm(note)} variant="contained" size="small" disabled={saving} autoFocus={isNewAgent}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}

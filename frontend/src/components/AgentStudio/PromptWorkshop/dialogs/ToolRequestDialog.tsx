import { useEffect, useState } from 'react'
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography } from '@mui/material'

export interface ToolRequestDialogProps {
  open: boolean
  submitting: boolean
  onSubmit: (title: string, description: string) => Promise<boolean>
  onClose: () => void
}

export default function ToolRequestDialog({ open, submitting, onSubmit, onClose }: ToolRequestDialogProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (open) {
      setTitle('')
      setDescription('')
    }
  }, [open])

  const handleSubmit = async () => {
    const submitted = await onSubmit(title, description)
    if (submitted) onClose()
  }

  return (
    <Dialog open={open} onClose={submitting ? undefined : onClose} maxWidth="sm" fullWidth aria-labelledby="tool-request-title">
      <DialogTitle id="tool-request-title">New request to developers</DialogTitle>
      <DialogContent sx={{ pt: 1 }}>
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <Typography sx={{ fontSize: 12.5, color: 'text.secondary' }}>
            Describe the tool you need. You can draft it with Claude first.
          </Typography>
          <TextField
            autoFocus
            size="small"
            fullWidth
            label="Title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Example: Add GO synonym expansion tool"
          />
          <TextField
            fullWidth
            multiline
            minRows={6}
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Describe the problem, required inputs, expected output, and one example use case."
          />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small" disabled={submitting}>
          Cancel
        </Button>
        <Button onClick={() => void handleSubmit()} variant="contained" size="small" disabled={submitting}>
          {submitting ? 'Sending…' : 'Send request'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

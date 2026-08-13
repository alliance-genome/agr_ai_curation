import {
  Alert,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
} from '@mui/material'

import { FieldRow } from '@/features/curation/editor'
import type { CurationAdapterEditorPack } from '@/features/curation/adapters'
import type { CurationDraftField } from '@/features/curation/types'

export interface HorizontalGridFieldEditorDialogProps {
  autosaveWarning: string | null
  editorPack: CurationAdapterEditorPack | null
  field: CurationDraftField | null
  onChange: (value: unknown) => void
  onClose: () => void
  onRevert: () => void
  open: boolean
}

export default function HorizontalGridFieldEditorDialog({
  autosaveWarning,
  editorPack,
  field,
  onChange,
  onClose,
  onRevert,
  open,
}: HorizontalGridFieldEditorDialogProps) {
  return (
    <Dialog fullWidth maxWidth="md" onClose={onClose} open={open && field !== null}>
      <DialogTitle>{field ? `Edit ${field.label}` : 'Edit field'}</DialogTitle>
      <DialogContent dividers>
        {autosaveWarning ? (
          <Alert severity="warning" sx={{ mb: 1.5 }}>
            {autosaveWarning}
          </Alert>
        ) : null}
        {field ? (
          <Stack spacing={1}>
            <Stack direction="row" spacing={0.5}>
              {field.required ? <Chip label="Required" size="small" /> : null}
              {field.dirty ? <Chip color="warning" label="Unsaved changes" size="small" /> : null}
            </Stack>
            <FieldRow
              field={field}
              onChange={onChange}
              renderInput={editorPack?.renderFieldInput}
              revertSlot={field.dirty ? (
                <Button onClick={onRevert} size="small" type="button" variant="text">
                  Revert
                </Button>
              ) : null}
              value={field.value}
            />
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}

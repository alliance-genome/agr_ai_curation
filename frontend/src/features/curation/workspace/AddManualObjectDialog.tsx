import { useCallback, useEffect, useState } from 'react'
import type { ChangeEvent } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
} from '@mui/material'

import {
  ENTITY_TYPE_CODES,
  ENTITY_TYPE_LABELS,
} from '../entityTable/literatureEntityTypeCatalog'

export interface ManualObjectDraft {
  entity_name: string
  entity_type: string
  species: string
  topic: string
}

export interface AddManualObjectDialogProps {
  isCreating?: boolean
  onCancel: () => void
  onCreate: (draft: ManualObjectDraft) => void
  open: boolean
}

const EMPTY_DRAFT: ManualObjectDraft = {
  entity_name: '',
  entity_type: '',
  species: '',
  topic: '',
}

export default function AddManualObjectDialog({
  isCreating = false,
  onCancel,
  onCreate,
  open,
}: AddManualObjectDialogProps) {
  const [draft, setDraft] = useState<ManualObjectDraft>(EMPTY_DRAFT)

  useEffect(() => {
    if (open) {
      setDraft(EMPTY_DRAFT)
    }
  }, [open])

  const updateField = useCallback(
    (field: keyof ManualObjectDraft) =>
      (event: ChangeEvent<HTMLInputElement>) => {
        setDraft((currentDraft) => ({
          ...currentDraft,
          [field]: event.target.value,
        }))
      },
    [],
  )

  const canCreate = draft.entity_name.trim() !== '' && draft.entity_type.trim() !== ''

  const handleSubmit = useCallback(() => {
    if (!canCreate || isCreating) {
      return
    }

    onCreate({
      entity_name: draft.entity_name.trim(),
      entity_type: draft.entity_type.trim(),
      species: draft.species.trim(),
      topic: draft.topic.trim(),
    })
  }, [canCreate, draft, isCreating, onCreate])

  return (
    <Dialog
      fullWidth
      maxWidth="xs"
      onClose={isCreating ? undefined : onCancel}
      open={open}
    >
      <DialogTitle>Add object</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <TextField
            autoFocus
            fullWidth
            label="Name"
            onChange={updateField('entity_name')}
            value={draft.entity_name}
          />
          <TextField
            fullWidth
            label="Type"
            onChange={updateField('entity_type')}
            select
            value={draft.entity_type}
          >
            <MenuItem disabled value="">
              Select type
            </MenuItem>
            {ENTITY_TYPE_CODES.map((entityTypeCode) => (
              <MenuItem key={entityTypeCode} value={entityTypeCode}>
                {ENTITY_TYPE_LABELS[entityTypeCode]}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            fullWidth
            label="Species"
            onChange={updateField('species')}
            value={draft.species}
          />
          <TextField
            fullWidth
            label="Topic"
            onChange={updateField('topic')}
            value={draft.topic}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={isCreating} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          disabled={!canCreate || isCreating}
          onClick={handleSubmit}
          variant="contained"
        >
          Add object
        </Button>
      </DialogActions>
    </Dialog>
  )
}

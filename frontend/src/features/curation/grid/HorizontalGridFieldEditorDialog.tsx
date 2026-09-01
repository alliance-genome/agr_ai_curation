import { useEffect, useId, useState } from 'react'

import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  IconButton,
  Stack,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import { FieldRow } from '@/features/curation/editor'
import type { CurationAdapterEditorPack } from '@/features/curation/adapters'
import type { CurationDraftField } from '@/features/curation/types'

export interface HorizontalGridFieldEditorDialogProps {
  autosaveWarning: string | null
  editorPack: CurationAdapterEditorPack | null
  field: CurationDraftField | null
  isSaving: boolean
  onClose: () => void
  onRevert: () => Promise<boolean>
  onSave: (value: unknown) => Promise<boolean>
  open: boolean
}

export default function HorizontalGridFieldEditorDialog({
  autosaveWarning,
  editorPack,
  field,
  isSaving,
  onClose,
  onRevert,
  onSave,
  open,
}: HorizontalGridFieldEditorDialogProps) {
  const titleId = useId()
  const [draftValue, setDraftValue] = useState<unknown>(field?.value ?? null)
  const [revertToSeed, setRevertToSeed] = useState(false)

  useEffect(() => {
    if (!open || !field) {
      return
    }
    setDraftValue(field.value)
    setRevertToSeed(false)
  }, [field, open])

  return (
    <Dialog
      aria-labelledby={titleId}
      fullWidth
      maxWidth={false}
      onClose={onClose}
      open={open && field !== null}
      PaperProps={{
        sx: (theme) => ({
          width: 'min(460px, calc(100vw - 32px))',
          m: 2,
          border: `1px solid ${theme.palette.mode === 'light' ? theme.palette.grey[400] : alpha(theme.palette.common.white, 0.28)}`,
          borderRadius: '8px',
          backgroundImage: 'none',
          boxShadow: theme.palette.mode === 'light'
            ? '0 20px 70px rgba(5, 31, 57, 0.30)'
            : '0 20px 70px rgba(0, 0, 0, 0.62)',
        }),
      }}
      slotProps={{
        backdrop: {
          sx: (theme) => ({
            backgroundColor: theme.palette.mode === 'light'
              ? 'rgba(6, 31, 57, 0.42)'
              : alpha(theme.palette.common.black, 0.68),
          }),
        },
      }}
    >
      <Box sx={{ p: '20px 20px 0' }}>
        <Stack alignItems="flex-start" direction="row" justifyContent="space-between" spacing="12px">
          <Box>
            <Typography
              color="text.secondary"
              display="block"
              sx={{ fontSize: 9, fontWeight: 770, letterSpacing: '0.08em', mb: '3px', textTransform: 'uppercase' }}
            >
              Edit curation value
            </Typography>
            <Typography id={titleId} sx={{ fontSize: 15, fontWeight: 700, lineHeight: 1.25 }}>
              {field ? `Edit ${field.label}` : 'Edit field'}
            </Typography>
          </Box>
          <IconButton aria-label="Close edit dialog" onClick={onClose} size="small" sx={{ height: 28, width: 28 }}>
            <CloseRoundedIcon sx={{ fontSize: 19 }} />
          </IconButton>
        </Stack>
      </Box>
      <DialogContent
        sx={{
          p: '20px 20px 0 !important',
          '& [data-testid^="field-row-"]': {
            gridTemplateColumns: '1fr',
            rowGap: '6px',
          },
          '& .MuiInputBase-root': {
            minHeight: 42,
            borderRadius: '5px',
          },
        }}
      >
        {autosaveWarning ? (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {autosaveWarning}
          </Alert>
        ) : null}
        {field ? (
          <Stack spacing={0}>
            <FieldRow
              field={field}
              onChange={(value) => {
                setDraftValue(value)
                setRevertToSeed(false)
              }}
              renderInput={editorPack?.renderFieldInput}
              value={draftValue}
            />
            <Typography color="text.secondary" sx={{ fontSize: 10, lineHeight: 1.4, m: '7px 0 20px' }}>
              Save value applies this edit to the curation draft.
              {field.dirty ? ' You can also restore the extracted value before saving.' : ''}
            </Typography>
            {field.dirty ? (
              <Button
                disabled={isSaving}
                onClick={() => {
                  setDraftValue(field.seed_value)
                  setRevertToSeed(true)
                }}
                size="small"
                sx={{ alignSelf: 'flex-start', mb: 1, textTransform: 'none' }}
                type="button"
                variant="text"
              >
                Restore extracted value
              </Button>
            ) : null}
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions sx={{ gap: '8px', p: '0 20px 20px' }}>
        <Button disabled={isSaving} onClick={onClose} variant="outlined">Cancel</Button>
        <Button
          disabled={isSaving || !field}
          onClick={() => {
            void (revertToSeed ? onRevert() : onSave(draftValue))
          }}
          variant="contained"
        >
          Save value
        </Button>
      </DialogActions>
    </Dialog>
  )
}

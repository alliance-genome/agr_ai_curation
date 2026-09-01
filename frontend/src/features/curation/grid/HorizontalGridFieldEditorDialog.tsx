import { useId } from 'react'

import CloseRoundedIcon from '@mui/icons-material/CloseRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
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
  const titleId = useId()

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
          <Stack spacing={1.25}>
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
      <DialogActions sx={{ gap: '8px', p: '20px' }}>
        <Button onClick={onClose} variant="contained">Close</Button>
      </DialogActions>
    </Dialog>
  )
}

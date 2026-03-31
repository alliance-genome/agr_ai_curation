import AddIcon from '@mui/icons-material/Add'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'

import type {
  CurationSavedView,
  CurationSessionFilters,
  CurationSessionSortField,
  CurationSortDirection,
} from '../types'
import {
  useCreateCurationSavedView,
  useCurationSavedViews,
  useDeleteCurationSavedView,
} from './curationInventoryService'

interface SavedViewSelectorProps {
  filters: CurationSessionFilters
  sortBy: CurationSessionSortField
  sortDirection: CurationSortDirection
  selectedViewId?: string | null
  onApplyView: (view: CurationSavedView) => void
  onClearSelection: () => void
}

function buildSaveRequestFilters(filters: CurationSessionFilters): CurationSessionFilters {
  return {
    ...filters,
    statuses: [...(filters.statuses ?? [])],
    adapter_keys: [...(filters.adapter_keys ?? [])],
    curator_ids: [...(filters.curator_ids ?? [])],
    tags: [...(filters.tags ?? [])],
    origin_session_id: null,
    saved_view_id: null,
  }
}

function getErrorMessage(error: unknown): string | null {
  return error instanceof Error ? error.message : null
}

export default function SavedViewSelector({
  filters,
  sortBy,
  sortDirection,
  selectedViewId,
  onApplyView,
  onClearSelection,
}: SavedViewSelectorProps) {
  const viewsQuery = useCurationSavedViews()
  const createViewMutation = useCreateCurationSavedView()
  const deleteViewMutation = useDeleteCurationSavedView()
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [nameInput, setNameInput] = useState('')
  const [descriptionInput, setDescriptionInput] = useState('')
  const [isDefault, setIsDefault] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const views = viewsQuery.data?.views ?? []
  const selectedView = views.find((view) => view.view_id === selectedViewId) ?? null
  const errorMessage =
    localError ||
    getErrorMessage(createViewMutation.error) ||
    getErrorMessage(deleteViewMutation.error) ||
    getErrorMessage(viewsQuery.error)

  function resetDialogState() {
    setNameInput('')
    setDescriptionInput('')
    setIsDefault(false)
    setLocalError(null)
  }

  function handleDialogClose() {
    if (createViewMutation.isPending) {
      return
    }

    setIsDialogOpen(false)
    resetDialogState()
  }

  function handleDeleteDialogClose() {
    if (deleteViewMutation.isPending) {
      return
    }

    setIsDeleteDialogOpen(false)
  }

  async function handleSaveCurrentView() {
    const normalizedName = nameInput.trim()
    if (!normalizedName) {
      setLocalError('Saved view name is required.')
      return
    }

    setLocalError(null)

    try {
      const response = await createViewMutation.mutateAsync({
        name: normalizedName,
        description: descriptionInput.trim() || null,
        filters: buildSaveRequestFilters(filters),
        sort_by: sortBy,
        sort_direction: sortDirection,
        is_default: isDefault,
      })
      onApplyView(response.view)
      setIsDialogOpen(false)
      resetDialogState()
    } catch {
      // React Query keeps the mutation error for display.
    }
  }

  async function handleDeleteSelectedView() {
    if (!selectedViewId) {
      return
    }

    setLocalError(null)

    try {
      await deleteViewMutation.mutateAsync(selectedViewId)
      setIsDeleteDialogOpen(false)
      onClearSelection()
    } catch {
      // React Query keeps the mutation error for display.
    }
  }

  return (
    <Stack spacing={1.25}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={1}
        alignItems={{ xs: 'stretch', sm: 'center' }}
        justifyContent="flex-end"
      >
        <TextField
          select
          SelectProps={{ native: true }}
          disabled={viewsQuery.isLoading}
          inputProps={{ 'data-testid': 'saved-view-selector' }}
          label="Saved view"
          onChange={(event) => {
            const nextValue = event.target.value
            if (!nextValue) {
              onClearSelection()
              return
            }

            const nextView = views.find((view) => view.view_id === nextValue)
            if (nextView) {
              onApplyView(nextView)
            }
          }}
          size="small"
          sx={{ minWidth: { sm: 260 } }}
          value={selectedViewId ?? ''}
        >
          <option value="">Live filters</option>
          {views.map((view) => (
            <option key={view.view_id} value={view.view_id}>
              {view.is_default ? `${view.name} (default)` : view.name}
            </option>
          ))}
        </TextField>

        <Button
          onClick={() => {
            setLocalError(null)
            setIsDialogOpen(true)
          }}
          startIcon={<AddIcon />}
          variant="outlined"
        >
          Save current
        </Button>

        <Button
          color="inherit"
          disabled={!selectedViewId || deleteViewMutation.isPending}
          onClick={() => {
            setLocalError(null)
            setIsDeleteDialogOpen(true)
          }}
          startIcon={
            deleteViewMutation.isPending ? <CircularProgress size={14} /> : <DeleteOutlineIcon />
          }
          variant="text"
        >
          Delete
        </Button>

        {viewsQuery.isLoading && <CircularProgress size={16} />}
      </Stack>

      {selectedView && (
        <Box sx={{ alignSelf: { xs: 'stretch', sm: 'flex-end' } }}>
          <Typography color="text.secondary" variant="caption">
            {selectedView.description || 'Applies this saved filter and sort preset to the inventory.'}
          </Typography>
        </Box>
      )}

      {errorMessage && <Alert severity="warning">{errorMessage}</Alert>}

      <Dialog fullWidth maxWidth="sm" onClose={handleDialogClose} open={isDialogOpen}>
        <DialogTitle>Save current filters</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography color="text.secondary" variant="body2">
              Save the current filter and sort state so you can recall it later from the inventory page.
            </Typography>
            <TextField
              autoFocus
              label="View name"
              onChange={(event) => setNameInput(event.target.value)}
              required
              size="small"
              value={nameInput}
            />
            <TextField
              label="Description"
              minRows={2}
              multiline
              onChange={(event) => setDescriptionInput(event.target.value)}
              placeholder="Optional note for teammates or future you"
              size="small"
              value={descriptionInput}
            />
            <FormControlLabel
              control={
                <Checkbox
                  checked={isDefault}
                  onChange={(event) => setIsDefault(event.target.checked)}
                />
              }
              label="Mark as my default saved view"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleDialogClose}>Cancel</Button>
          <Button
            disabled={createViewMutation.isPending}
            onClick={() => {
              void handleSaveCurrentView()
            }}
            variant="contained"
          >
            {createViewMutation.isPending ? 'Saving...' : 'Save view'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        fullWidth
        maxWidth="xs"
        onClose={handleDeleteDialogClose}
        open={isDeleteDialogOpen}
      >
        <DialogTitle>Delete saved view?</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ pt: 1 }}>
            <Typography color="text.secondary" variant="body2">
              {selectedView
                ? `Delete "${selectedView.name}" permanently?`
                : 'Delete this saved view permanently?'}
            </Typography>
            <Typography color="text.secondary" variant="body2">
              This action cannot be undone.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleDeleteDialogClose}>Cancel</Button>
          <Button
            color="error"
            disabled={deleteViewMutation.isPending || !selectedViewId}
            onClick={() => {
              void handleDeleteSelectedView()
            }}
            variant="contained"
          >
            {deleteViewMutation.isPending ? 'Deleting...' : 'Delete saved view'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}

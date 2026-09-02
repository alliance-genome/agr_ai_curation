import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import { Box, Button, Checkbox, Typography } from '@mui/material'

interface SelectionBarProps {
  deleteDisabled: boolean
  onClear: () => void
  onDelete: () => void
  onSelectAll: () => void
  selectedCount: number
  visibleCount: number
}

export default function SelectionBar({
  deleteDisabled,
  onClear,
  onDelete,
  onSelectAll,
  selectedCount,
  visibleCount,
}: SelectionBarProps) {
  if (selectedCount === 0) {
    return null
  }

  const allSelected = selectedCount >= visibleCount

  return (
    <Box
      aria-label="Selection actions"
      role="toolbar"
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        px: '12px',
        py: '6px',
        borderRadius: '6px',
        bgcolor: 'action.selected',
        fontSize: '13px',
      }}
    >
      <Checkbox
        checked={allSelected}
        indeterminate={!allSelected}
        inputProps={{
          'aria-label': 'Select all shown conversations',
          'aria-checked': allSelected ? 'true' : 'mixed',
        }}
        onChange={(event) => (event.target.checked ? onSelectAll() : onClear())}
        size="small"
        sx={{ p: 0 }}
      />
      <Typography component="span" sx={{ fontSize: '13px' }}>
        <Box component="b" sx={{ fontWeight: 500 }}>{selectedCount} selected</Box>
        {' '}of {visibleCount} shown
      </Typography>
      {allSelected ? null : (
        <Button onClick={onSelectAll} size="small" sx={{ textTransform: 'none', minHeight: 26 }}>
          Select all {visibleCount}
        </Button>
      )}
      <Box sx={{ flex: 1 }} />
      <Button color="inherit" onClick={onClear} size="small" sx={{ textTransform: 'none', minHeight: 26 }}>
        Clear
      </Button>
      <Button
        color="error"
        disabled={deleteDisabled}
        onClick={onDelete}
        size="small"
        startIcon={<DeleteOutlineIcon />}
        sx={{ textTransform: 'none', minHeight: 26, bgcolor: 'background.paper' }}
        variant="outlined"
      >
        Delete {selectedCount}
      </Button>
    </Box>
  )
}

import ClearIcon from '@mui/icons-material/Clear'
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep'
import SearchIcon from '@mui/icons-material/Search'
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'

interface HistorySearchBarProps {
  allVisibleSelected: boolean
  bulkDeleteDisabled?: boolean
  hasVisibleSessions: boolean
  isFiltering?: boolean
  onBulkDelete: () => void
  onChange: (value: string) => void
  onToggleSelectAll: (checked: boolean) => void
  searchScopeLabel: string
  selectedCount: number
  totalSessions: number
  value: string
  visibleCount: number
}

function pluralize(count: number, singular: string, plural = `${singular}s`) {
  return count === 1 ? singular : plural
}

export default function HistorySearchBar({
  allVisibleSelected,
  bulkDeleteDisabled = false,
  hasVisibleSessions,
  isFiltering = false,
  onBulkDelete,
  onChange,
  onToggleSelectAll,
  searchScopeLabel,
  selectedCount,
  totalSessions,
  value,
  visibleCount,
}: HistorySearchBarProps) {
  return (
    <Paper
      elevation={0}
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 3,
        p: 2.5,
      }}
    >
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems={{ md: 'center' }}>
        <TextField
          fullWidth
          label="Search chat history"
          placeholder="Search by conversation title"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          inputProps={{ 'aria-label': 'Search chat history' }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon color="action" fontSize="small" />
              </InputAdornment>
            ),
            endAdornment: value ? (
              <InputAdornment position="end">
                <Button
                  aria-label="Clear history search"
                  color="inherit"
                  onClick={() => onChange('')}
                  size="small"
                  startIcon={<ClearIcon fontSize="small" />}
                >
                  Clear
                </Button>
              </InputAdornment>
            ) : undefined,
          }}
        />

        <Button
          color="error"
          disabled={bulkDeleteDisabled}
          onClick={onBulkDelete}
          startIcon={<DeleteSweepIcon />}
          variant="contained"
        >
          Delete selected
        </Button>
      </Stack>

      <Stack
        direction={{ xs: 'column', md: 'row' }}
        spacing={1}
        justifyContent="space-between"
        sx={{ mt: 2 }}
      >
        <FormControlLabel
          control={
            <Checkbox
              checked={allVisibleSelected}
              disabled={!hasVisibleSessions}
              onChange={(event) => onToggleSelectAll(event.target.checked)}
            />
          }
          label="Select all visible conversations"
        />

        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 1.5,
            alignItems: 'center',
            justifyContent: { xs: 'flex-start', md: 'flex-end' },
          }}
        >
          <Typography color="text.secondary" variant="body2">
            Showing {visibleCount} of {totalSessions} {pluralize(totalSessions, 'conversation')}
          </Typography>
          <Typography color="text.secondary" variant="body2">
            {selectedCount} {pluralize(selectedCount, 'conversation')} selected
          </Typography>
          <Typography color="text.secondary" variant="body2">
            Searching within {searchScopeLabel}
          </Typography>
          {isFiltering ? (
            <Typography color="primary" variant="body2">
              Updating results…
            </Typography>
          ) : null}
        </Box>
      </Stack>
    </Paper>
  )
}

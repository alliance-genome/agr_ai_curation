import ClearIcon from '@mui/icons-material/Clear'
import SearchIcon from '@mui/icons-material/Search'
import {
  Box,
  IconButton,
  InputAdornment,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'

import {
  ALL_CHAT_HISTORY_KIND,
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  ASSISTANT_CHAT_HISTORY_KIND,
  type ChatHistoryListKind,
} from '@/services/chatHistoryApi'

interface HistoryToolbarProps {
  isLoading: boolean
  onKindChange: (kind: ChatHistoryListKind) => void
  onSearchChange: (value: string) => void
  searchValue: string
  selectedKind: ChatHistoryListKind
  totalSessions: number
  visibleCount: number
}

const HISTORY_KIND_OPTIONS: Array<{ label: string; value: ChatHistoryListKind }> = [
  { label: 'All', value: ALL_CHAT_HISTORY_KIND },
  { label: 'Assistant', value: ASSISTANT_CHAT_HISTORY_KIND },
  { label: 'Studio', value: AGENT_STUDIO_CHAT_HISTORY_KIND },
]

export default function HistoryToolbar({
  isLoading,
  onKindChange,
  onSearchChange,
  searchValue,
  selectedKind,
  totalSessions,
  visibleCount,
}: HistoryToolbarProps) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
      <ToggleButtonGroup
        aria-label="Chat kind filter"
        exclusive
        onChange={(_event, nextKind: ChatHistoryListKind | null) => {
          if (nextKind && nextKind !== selectedKind) {
            onKindChange(nextKind)
          }
        }}
        size="small"
        value={selectedKind}
        sx={{
          bgcolor: 'background.paper',
          '& .MuiToggleButton-root': {
            textTransform: 'none',
            fontSize: '13px',
            px: '11px',
            py: '4px',
            lineHeight: 1.5,
            color: 'text.secondary',
            '&.Mui-selected': { color: 'text.primary', fontWeight: 500, bgcolor: 'action.selected' },
          },
        }}
      >
        {HISTORY_KIND_OPTIONS.map((option) => (
          <ToggleButton key={option.value} value={option.value}>
            {option.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      <TextField
        inputProps={{ 'aria-label': 'Search titles' }}
        onChange={(event) => onSearchChange(event.target.value)}
        placeholder="Search titles"
        size="small"
        value={searchValue}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SearchIcon fontSize="small" sx={{ color: 'text.secondary' }} />
            </InputAdornment>
          ),
          endAdornment: searchValue ? (
            <InputAdornment position="end">
              <IconButton aria-label="Clear search text" edge="end" onClick={() => onSearchChange('')} size="small">
                <ClearIcon fontSize="small" />
              </IconButton>
            </InputAdornment>
          ) : undefined,
        }}
        sx={{
          flex: 1,
          minWidth: 180,
          maxWidth: 360,
          '& .MuiInputBase-root': { height: 32, fontSize: '13px', bgcolor: 'background.paper' },
        }}
      />

      <Typography
        aria-live="polite"
        color="text.secondary"
        sx={{ ml: 'auto', fontSize: '13px', whiteSpace: 'nowrap' }}
      >
        {isLoading ? 'Loading…' : `Showing ${visibleCount} of ${totalSessions}`}
      </Typography>
    </Box>
  )
}

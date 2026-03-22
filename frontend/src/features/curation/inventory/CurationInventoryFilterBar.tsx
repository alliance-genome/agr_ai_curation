import SearchIcon from '@mui/icons-material/Search'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'

import type {
  CurationSessionStats,
  CurationSessionStatus,
} from '../types'
import {
  getAdapterChipColor,
  getStatusChipColor,
  getStatusCount,
  getStatusLabel,
  STATUS_FILTER_ORDER,
  type InventoryFilterOption,
} from './inventoryPresentation'

interface CurationInventoryFilterBarProps {
  statuses: CurationSessionStatus[]
  adapterKeys: string[]
  profileKeys: string[]
  searchInput: string
  adapterOptions: InventoryFilterOption[]
  profileOptions: InventoryFilterOption[]
  stats?: CurationSessionStats
  isRefreshing: boolean
  onToggleStatus: (status: CurationSessionStatus) => void
  onClearStatuses: () => void
  onToggleAdapterKey: (adapterKey: string) => void
  onClearAdapterKeys: () => void
  onToggleProfileKey: (profileKey: string) => void
  onClearProfileKeys: () => void
  onSearchChange: (value: string) => void
  onClearAllFilters: () => void
  hasActiveFilters: boolean
}

interface ChipGroupProps {
  label: string
  options: InventoryFilterOption[]
  selectedKeys: string[]
  onToggle: (key: string) => void
  onClear: () => void
}

function FilterChipGroup({
  label,
  options,
  selectedKeys,
  onToggle,
  onClear,
}: ChipGroupProps) {
  if (options.length === 0) {
    return null
  }

  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: 72 }}>
          {label}
        </Typography>
        {options.map((option) => {
          const selected = selectedKeys.includes(option.key)
          return (
            <Chip
              key={option.key}
              clickable
              color={getAdapterChipColor({ color_token: option.colorToken, metadata: {} })}
              label={option.label}
              onClick={() => onToggle(option.key)}
              size="small"
              variant={selected ? 'filled' : 'outlined'}
            />
          )
        })}
        {selectedKeys.length > 0 && (
          <Button color="inherit" onClick={onClear} size="small">
            Clear
          </Button>
        )}
      </Stack>
    </Stack>
  )
}

export default function CurationInventoryFilterBar({
  statuses,
  adapterKeys,
  profileKeys,
  searchInput,
  adapterOptions,
  profileOptions,
  stats,
  isRefreshing,
  onToggleStatus,
  onClearStatuses,
  onToggleAdapterKey,
  onClearAdapterKeys,
  onToggleProfileKey,
  onClearProfileKeys,
  onSearchChange,
  onClearAllFilters,
  hasActiveFilters,
}: CurationInventoryFilterBarProps) {
  return (
    <Paper variant="outlined" sx={{ p: 2.5 }}>
      <Stack spacing={2}>
        <Stack
          direction={{ xs: 'column', xl: 'row' }}
          spacing={2}
          alignItems={{ xs: 'stretch', xl: 'flex-start' }}
          justifyContent="space-between"
        >
          <Stack spacing={1.5} sx={{ flex: 1 }}>
            <Stack spacing={1}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <Typography variant="body2" color="text.secondary" sx={{ minWidth: 72 }}>
                  Status
                </Typography>
                <Chip
                  clickable
                  color="default"
                  label={`All${typeof stats?.total_sessions === 'number' ? ` ${stats.total_sessions}` : ''}`}
                  onClick={onClearStatuses}
                  size="small"
                  variant={statuses.length === 0 ? 'filled' : 'outlined'}
                />
                {STATUS_FILTER_ORDER.map((status) => {
                  const selected = statuses.includes(status)
                  const count = getStatusCount(status, stats)
                  return (
                    <Chip
                      key={status}
                      clickable
                      color={getStatusChipColor(status)}
                      label={`${getStatusLabel(status)}${typeof count === 'number' ? ` ${count}` : ''}`}
                      onClick={() => onToggleStatus(status)}
                      size="small"
                      variant={selected ? 'filled' : 'outlined'}
                    />
                  )
                })}
                {statuses.length > 0 && (
                  <Button color="inherit" onClick={onClearStatuses} size="small">
                    Clear
                  </Button>
                )}
              </Stack>
            </Stack>

            <FilterChipGroup
              label="Adapters"
              onClear={onClearAdapterKeys}
              onToggle={onToggleAdapterKey}
              options={adapterOptions}
              selectedKeys={adapterKeys}
            />

            <FilterChipGroup
              label="Profiles"
              onClear={onClearProfileKeys}
              onToggle={onToggleProfileKey}
              options={profileOptions}
              selectedKeys={profileKeys}
            />
          </Stack>

          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1.5}
            alignItems={{ xs: 'stretch', sm: 'center' }}
            sx={{ minWidth: { xl: 340 } }}
          >
            <TextField
              fullWidth
              label="Search sessions"
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder="Title, PMID, DOI, or notes"
              size="small"
              value={searchInput}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />
            {hasActiveFilters && (
              <Button onClick={onClearAllFilters} variant="outlined">
                Clear filters
              </Button>
            )}
          </Stack>
        </Stack>

        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          alignItems={{ xs: 'flex-start', sm: 'center' }}
          justifyContent="space-between"
        >
          <Stack direction="row" spacing={1} alignItems="center">
            {isRefreshing && <CircularProgress size={14} />}
            <Typography variant="caption" color="text.secondary">
              {isRefreshing
                ? 'Refreshing inventory results...'
                : 'Status counts stay in sync with the current non-status filters.'}
            </Typography>
          </Stack>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Box data-testid="curation-saved-views-slot" />
            <Box data-testid="curation-queue-actions-slot" />
          </Box>
        </Stack>
      </Stack>
    </Paper>
  )
}

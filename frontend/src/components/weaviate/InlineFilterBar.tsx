import React, { useState } from 'react';
import {
  Box,
  TextField,
  InputAdornment,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Checkbox,
  ListItemText,
  Chip,
  IconButton,
  Popover,
  Stack,
  Typography,
  Button,
  OutlinedInput,
  Tooltip,
} from '@mui/material';
import {
  Search,
  Clear,
  DateRange,
  Numbers,
} from '@mui/icons-material';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { DocumentFilter } from '../../services/weaviate';

interface InlineFilterBarProps {
  filters: DocumentFilter;
  onFilterChange: (filters: DocumentFilter) => void;
  onClear: () => void;
}

const EMBEDDING_STATUSES = [
  { value: 'pending', label: 'Pending' },
  { value: 'processing', label: 'Processing' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'partial', label: 'Partial' },
];

const InlineFilterBar: React.FC<InlineFilterBarProps> = ({
  filters,
  onFilterChange,
  onClear,
}) => {
  const [dateAnchorEl, setDateAnchorEl] = useState<HTMLElement | null>(null);
  const [vectorAnchorEl, setVectorAnchorEl] = useState<HTMLElement | null>(null);

  const handleSearchChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onFilterChange({ ...filters, searchTerm: event.target.value || undefined });
  };

  const handleStatusChange = (event: any) => {
    const value = event.target.value as string[];
    onFilterChange({
      ...filters,
      embeddingStatus: value.length > 0 ? value : undefined,
    });
  };

  const handleDateChange = (field: 'dateFrom' | 'dateTo', value: Date | null) => {
    onFilterChange({ ...filters, [field]: value || undefined });
  };

  const handleVectorChange = (field: 'minVectorCount' | 'maxVectorCount', value: string) => {
    const numValue = value === '' ? undefined : parseInt(value);
    onFilterChange({ ...filters, [field]: isNaN(numValue as number) ? undefined : numValue });
  };

  const clearSearch = () => {
    onFilterChange({ ...filters, searchTerm: undefined });
  };

  const clearDates = () => {
    onFilterChange({ ...filters, dateFrom: undefined, dateTo: undefined });
    setDateAnchorEl(null);
  };

  const clearVectors = () => {
    onFilterChange({ ...filters, minVectorCount: undefined, maxVectorCount: undefined });
    setVectorAnchorEl(null);
  };

  // Count active filters
  const activeFilterCount = [
    filters.searchTerm,
    filters.embeddingStatus?.length,
    filters.dateFrom || filters.dateTo,
    filters.minVectorCount !== undefined || filters.maxVectorCount !== undefined,
  ].filter(Boolean).length;

  const hasDateFilter = filters.dateFrom || filters.dateTo;
  const hasVectorFilter = filters.minVectorCount !== undefined || filters.maxVectorCount !== undefined;

  const formatDateLabel = () => {
    if (filters.dateFrom && filters.dateTo) {
      return `${filters.dateFrom.toLocaleDateString()} - ${filters.dateTo.toLocaleDateString()}`;
    }
    if (filters.dateFrom) return `From ${filters.dateFrom.toLocaleDateString()}`;
    if (filters.dateTo) return `Until ${filters.dateTo.toLocaleDateString()}`;
    return 'Any';
  };

  const formatVectorLabel = () => {
    if (filters.minVectorCount !== undefined && filters.maxVectorCount !== undefined) {
      return `${filters.minVectorCount} - ${filters.maxVectorCount}`;
    }
    if (filters.minVectorCount !== undefined) return `≥ ${filters.minVectorCount}`;
    if (filters.maxVectorCount !== undefined) return `≤ ${filters.maxVectorCount}`;
    return 'Any';
  };

  return (
    <LocalizationProvider dateAdapter={AdapterDateFns}>
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 1.5,
          py: 1.5,
          px: 0,
        }}
      >
        {/* Search Field */}
        <TextField
          size="small"
          placeholder="Search documents..."
          value={filters.searchTerm || ''}
          onChange={handleSearchChange}
          sx={{ minWidth: 200, maxWidth: 280 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Search fontSize="small" color="action" />
              </InputAdornment>
            ),
            endAdornment: filters.searchTerm && (
              <InputAdornment position="end">
                <IconButton size="small" onClick={clearSearch} edge="end">
                  <Clear fontSize="small" />
                </IconButton>
              </InputAdornment>
            ),
          }}
        />

        {/* Status Multi-Select */}
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel id="status-filter-label">Status</InputLabel>
          <Select
            labelId="status-filter-label"
            multiple
            value={filters.embeddingStatus || []}
            onChange={handleStatusChange}
            input={<OutlinedInput label="Status" />}
            renderValue={(selected) => {
              if (selected.length === 0) return 'All';
              if (selected.length === 1) {
                const status = EMBEDDING_STATUSES.find(s => s.value === selected[0]);
                return status?.label || selected[0];
              }
              return `${selected.length} selected`;
            }}
            MenuProps={{
              PaperProps: {
                style: { maxHeight: 280 },
              },
            }}
          >
            {EMBEDDING_STATUSES.map((status) => (
              <MenuItem key={status.value} value={status.value} dense>
                <Checkbox
                  checked={(filters.embeddingStatus || []).includes(status.value)}
                  size="small"
                />
                <ListItemText primary={status.label} />
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* Date Range Button/Popover */}
        <Tooltip title="Filter by date">
          <Button
            variant={hasDateFilter ? 'contained' : 'outlined'}
            color={hasDateFilter ? 'primary' : 'inherit'}
            startIcon={<DateRange fontSize="small" />}
            onClick={(e) => setDateAnchorEl(e.currentTarget)}
            sx={{
              textTransform: 'none',
              minWidth: 100,
              height: 40,
              borderColor: hasDateFilter ? undefined : 'divider',
              color: hasDateFilter ? undefined : 'text.secondary',
            }}
          >
            {hasDateFilter ? formatDateLabel() : 'Date'}
          </Button>
        </Tooltip>
        <Popover
          open={Boolean(dateAnchorEl)}
          anchorEl={dateAnchorEl}
          onClose={() => setDateAnchorEl(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <Box sx={{ p: 2, minWidth: 280 }}>
            <Typography variant="subtitle2" gutterBottom>
              Date Range
            </Typography>
            <Stack spacing={2}>
              <DatePicker
                label="From"
                value={filters.dateFrom || null}
                onChange={(value) => handleDateChange('dateFrom', value)}
                slotProps={{
                  textField: { size: 'small', fullWidth: true },
                }}
              />
              <DatePicker
                label="To"
                value={filters.dateTo || null}
                onChange={(value) => handleDateChange('dateTo', value)}
                slotProps={{
                  textField: { size: 'small', fullWidth: true },
                }}
              />
              <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1 }}>
                <Button size="small" onClick={clearDates}>
                  Clear
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  onClick={() => setDateAnchorEl(null)}
                >
                  Done
                </Button>
              </Box>
            </Stack>
          </Box>
        </Popover>

        {/* Chunk Count Button/Popover */}
        <Tooltip title="Filter by chunk count">
          <Button
            variant={hasVectorFilter ? 'contained' : 'outlined'}
            color={hasVectorFilter ? 'primary' : 'inherit'}
            startIcon={<Numbers fontSize="small" />}
            onClick={(e) => setVectorAnchorEl(e.currentTarget)}
            sx={{
              textTransform: 'none',
              minWidth: 100,
              height: 40,
              borderColor: hasVectorFilter ? undefined : 'divider',
              color: hasVectorFilter ? undefined : 'text.secondary',
            }}
          >
            {hasVectorFilter ? formatVectorLabel() : 'Chunks'}
          </Button>
        </Tooltip>
        <Popover
          open={Boolean(vectorAnchorEl)}
          anchorEl={vectorAnchorEl}
          onClose={() => setVectorAnchorEl(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        >
          <Box sx={{ p: 2, minWidth: 240 }}>
            <Typography variant="subtitle2" gutterBottom>
              Chunk Count Range
            </Typography>
            <Stack spacing={2}>
              <TextField
                label="Minimum"
                type="number"
                size="small"
                fullWidth
                value={filters.minVectorCount ?? ''}
                onChange={(e) => handleVectorChange('minVectorCount', e.target.value)}
                InputProps={{ inputProps: { min: 0 } }}
              />
              <TextField
                label="Maximum"
                type="number"
                size="small"
                fullWidth
                value={filters.maxVectorCount ?? ''}
                onChange={(e) => handleVectorChange('maxVectorCount', e.target.value)}
                InputProps={{ inputProps: { min: 0 } }}
              />
              <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1 }}>
                <Button size="small" onClick={clearVectors}>
                  Clear
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  onClick={() => setVectorAnchorEl(null)}
                >
                  Done
                </Button>
              </Box>
            </Stack>
          </Box>
        </Popover>

        {/* Spacer */}
        <Box sx={{ flex: 1 }} />

        {/* Active Filters Summary & Clear All */}
        {activeFilterCount > 0 && (
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              label={`${activeFilterCount} filter${activeFilterCount > 1 ? 's' : ''} active`}
              size="small"
              color="primary"
              variant="outlined"
            />
            <Button
              size="small"
              startIcon={<Clear fontSize="small" />}
              onClick={onClear}
              sx={{ textTransform: 'none' }}
            >
              Clear all
            </Button>
          </Stack>
        )}
      </Box>
    </LocalizationProvider>
  );
};

export default InlineFilterBar;

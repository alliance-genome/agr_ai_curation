import React, { useState } from 'react';
import {
  Box,
  Paper,
  TextField,
  FormGroup,
  FormControlLabel,
  Checkbox,
  Button,
  IconButton,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Stack,
  Chip,
  InputAdornment,
} from '@mui/material';
import {
  Search,
  FilterList,
  Clear,
  ExpandMore,
  DateRange,
  Numbers,
} from '@mui/icons-material';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { DocumentFilter } from '../../services/weaviate';

interface DocumentFiltersProps {
  filters?: DocumentFilter;
  onFilterChange?: (filters: DocumentFilter) => void;
  onClear?: () => void;
}

const DocumentFilters: React.FC<DocumentFiltersProps> = ({
  filters = {},
  onFilterChange,
  onClear,
}) => {
  const [localFilters, setLocalFilters] = useState<DocumentFilter>(filters);
  const [expanded, setExpanded] = useState<string | false>('status');

  const embeddingStatuses = [
    { value: 'pending', label: 'Pending', color: 'default' as const },
    { value: 'processing', label: 'Processing', color: 'primary' as const },
    { value: 'completed', label: 'Completed', color: 'success' as const },
    { value: 'failed', label: 'Failed', color: 'error' as const },
    { value: 'partial', label: 'Partial', color: 'warning' as const },
  ];

  const handleSearchChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newFilters = { ...localFilters, searchTerm: event.target.value };
    setLocalFilters(newFilters);
    onFilterChange?.(newFilters);
  };

  const handleStatusChange = (status: string) => {
    const currentStatuses = localFilters.embeddingStatus || [];
    const newStatuses = currentStatuses.includes(status)
      ? currentStatuses.filter((s) => s !== status)
      : [...currentStatuses, status];

    const newFilters = {
      ...localFilters,
      embeddingStatus: newStatuses.length > 0 ? newStatuses : undefined,
    };
    setLocalFilters(newFilters);
    onFilterChange?.(newFilters);
  };

  const handleDateChange = (field: 'dateFrom' | 'dateTo', value: Date | null) => {
    const newFilters = { ...localFilters, [field]: value };
    setLocalFilters(newFilters);
    onFilterChange?.(newFilters);
  };

  const handleVectorCountChange = (
    field: 'minVectorCount' | 'maxVectorCount',
    value: string
  ) => {
    const numValue = value === '' ? undefined : parseInt(value);
    const newFilters = { ...localFilters, [field]: numValue };
    setLocalFilters(newFilters);
    onFilterChange?.(newFilters);
  };

  const handleClearAll = () => {
    const clearedFilters = {};
    setLocalFilters(clearedFilters);
    onFilterChange?.(clearedFilters);
    onClear?.();
  };

  const activeFilterCount = () => {
    let count = 0;
    if (localFilters.searchTerm) count++;
    if (localFilters.embeddingStatus && localFilters.embeddingStatus.length > 0) count++;
    if (localFilters.dateFrom || localFilters.dateTo) count++;
    if (localFilters.minVectorCount || localFilters.maxVectorCount) count++;
    return count;
  };

  const handleAccordionChange = (panel: string) => (
    _event: React.SyntheticEvent,
    isExpanded: boolean
  ) => {
    setExpanded(isExpanded ? panel : false);
  };

  return (
    <LocalizationProvider dateAdapter={AdapterDateFns}>
      <Paper elevation={0} sx={{ p: 2, border: 1, borderColor: 'divider' }}>
        <Box sx={{ mb: 2 }}>
          <Stack direction="row" spacing={2} alignItems="center">
            <FilterList />
            <Typography variant="h6" sx={{ flex: 1 }}>
              Filters
            </Typography>
            {activeFilterCount() > 0 && (
              <Stack direction="row" spacing={1} alignItems="center">
                <Chip
                  label={`${activeFilterCount()} active`}
                  size="small"
                  color="primary"
                />
                <Button
                  startIcon={<Clear />}
                  onClick={handleClearAll}
                  size="small"
                >
                  Clear All
                </Button>
              </Stack>
            )}
          </Stack>
        </Box>

        <TextField
          fullWidth
          variant="outlined"
          placeholder="Search by filename..."
          value={localFilters.searchTerm || ''}
          onChange={handleSearchChange}
          sx={{ mb: 2 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Search />
              </InputAdornment>
            ),
            endAdornment: localFilters.searchTerm && (
              <InputAdornment position="end">
                <IconButton
                  size="small"
                  onClick={() =>
                    handleSearchChange({ target: { value: '' } } as any)
                  }
                >
                  <Clear />
                </IconButton>
              </InputAdornment>
            ),
          }}
        />

        <Accordion
          expanded={expanded === 'status'}
          onChange={handleAccordionChange('status')}
          elevation={0}
          sx={{ '&:before': { display: 'none' } }}
        >
          <AccordionSummary expandIcon={<ExpandMore />}>
            <Typography>Embedding Status</Typography>
            {localFilters.embeddingStatus &&
              localFilters.embeddingStatus.length > 0 && (
                <Chip
                  label={localFilters.embeddingStatus.length}
                  size="small"
                  sx={{ ml: 2 }}
                />
              )}
          </AccordionSummary>
          <AccordionDetails>
            <FormGroup>
              {embeddingStatuses.map((status) => (
                <FormControlLabel
                  key={status.value}
                  control={
                    <Checkbox
                      checked={
                        localFilters.embeddingStatus?.includes(status.value) ||
                        false
                      }
                      onChange={() => handleStatusChange(status.value)}
                      size="small"
                    />
                  }
                  label={
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="body2">{status.label}</Typography>
                      <Chip
                        size="small"
                        label={status.value}
                        color={status.color}
                        variant="outlined"
                      />
                    </Stack>
                  }
                />
              ))}
            </FormGroup>
          </AccordionDetails>
        </Accordion>

        <Accordion
          expanded={expanded === 'date'}
          onChange={handleAccordionChange('date')}
          elevation={0}
          sx={{ '&:before': { display: 'none' } }}
        >
          <AccordionSummary expandIcon={<ExpandMore />}>
            <Stack direction="row" spacing={1} alignItems="center">
              <DateRange />
              <Typography>Date Range</Typography>
            </Stack>
            {(localFilters.dateFrom || localFilters.dateTo) && (
              <Chip label="Active" size="small" sx={{ ml: 2 }} />
            )}
          </AccordionSummary>
          <AccordionDetails>
            <Stack spacing={2}>
              <DatePicker
                label="From Date"
                value={localFilters.dateFrom || null}
                onChange={(value) => handleDateChange('dateFrom', value)}
                slotProps={{
                  textField: {
                    fullWidth: true,
                    size: 'small',
                  },
                }}
              />
              <DatePicker
                label="To Date"
                value={localFilters.dateTo || null}
                onChange={(value) => handleDateChange('dateTo', value)}
                slotProps={{
                  textField: {
                    fullWidth: true,
                    size: 'small',
                  },
                }}
              />
            </Stack>
          </AccordionDetails>
        </Accordion>

        <Accordion
          expanded={expanded === 'chunks'}
          onChange={handleAccordionChange('chunks')}
          elevation={0}
          sx={{ '&:before': { display: 'none' } }}
        >
          <AccordionSummary expandIcon={<ExpandMore />}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Numbers />
              <Typography>Chunk Count</Typography>
            </Stack>
            {(localFilters.minVectorCount || localFilters.maxVectorCount) && (
              <Chip label="Active" size="small" sx={{ ml: 2 }} />
            )}
          </AccordionSummary>
          <AccordionDetails>
            <Stack spacing={2}>
              <TextField
                fullWidth
                label="Minimum Chunks"
                type="number"
                size="small"
                value={localFilters.minVectorCount || ''}
                onChange={(e) =>
                  handleVectorCountChange('minVectorCount', e.target.value)
                }
                InputProps={{
                  inputProps: { min: 0 },
                }}
              />
              <TextField
                fullWidth
                label="Maximum Chunks"
                type="number"
                size="small"
                value={localFilters.maxVectorCount || ''}
                onChange={(e) =>
                  handleVectorCountChange('maxVectorCount', e.target.value)
                }
                InputProps={{
                  inputProps: { min: 0 },
                }}
              />
            </Stack>
          </AccordionDetails>
        </Accordion>

        {activeFilterCount() > 0 && (
          <Box sx={{ mt: 2, p: 2, bgcolor: 'background.default', borderRadius: 1 }}>
            <Typography variant="body2" color="text.secondary" gutterBottom>
              Active Filters:
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {localFilters.searchTerm && (
                <Chip
                  label={`Search: ${localFilters.searchTerm}`}
                  onDelete={() =>
                    handleSearchChange({ target: { value: '' } } as any)
                  }
                  size="small"
                />
              )}
              {localFilters.embeddingStatus?.map((status) => (
                <Chip
                  key={status}
                  label={`Status: ${status}`}
                  onDelete={() => handleStatusChange(status)}
                  size="small"
                />
              ))}
              {localFilters.dateFrom && (
                <Chip
                  label={`From: ${localFilters.dateFrom.toLocaleDateString()}`}
                  onDelete={() => handleDateChange('dateFrom', null)}
                  size="small"
                />
              )}
              {localFilters.dateTo && (
                <Chip
                  label={`To: ${localFilters.dateTo.toLocaleDateString()}`}
                  onDelete={() => handleDateChange('dateTo', null)}
                  size="small"
                />
              )}
              {localFilters.minVectorCount && (
                <Chip
                  label={`Min Chunks: ${localFilters.minVectorCount}`}
                  onDelete={() => handleVectorCountChange('minVectorCount', '')}
                  size="small"
                />
              )}
              {localFilters.maxVectorCount && (
                <Chip
                  label={`Max Chunks: ${localFilters.maxVectorCount}`}
                  onDelete={() => handleVectorCountChange('maxVectorCount', '')}
                  size="small"
                />
              )}
            </Stack>
          </Box>
        )}
      </Paper>
    </LocalizationProvider>
  );
};

export default DocumentFilters;
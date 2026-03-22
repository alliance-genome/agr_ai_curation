import { Alert, Box, LinearProgress, Stack, Typography } from '@mui/material'
import { useNavigate } from 'react-router-dom'

import {
  CurationInventoryFilterBar,
  CurationInventoryTable,
  useCurationInventory,
} from '../features/curation/inventory'

export default function CurationInventoryPage() {
  const navigate = useNavigate()
  const inventory = useCurationInventory()
  const sessionErrorMessage = inventory.listQuery.error instanceof Error
    ? inventory.listQuery.error.message
    : undefined
  const statsErrorMessage = inventory.statsQuery.error instanceof Error
    ? inventory.statsQuery.error.message
    : undefined

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        overflow: 'auto',
        px: { xs: 2, md: 3 },
        py: 3,
      }}
    >
      <Stack spacing={3}>
        <Stack spacing={0.75}>
          <Typography variant="h4">Curation Inventory</Typography>
          <Typography color="text.secondary" variant="body1">
            Browse prepared sessions, refine the queue, and open a workspace right where curation left off.
          </Typography>
        </Stack>

        {/* Reserved for ALL-109 dashboard cards once that lane lands. */}
        <Box data-testid="curation-inventory-stats-slot" />

        {statsErrorMessage && (
          <Alert severity="warning">
            Status counts are temporarily unavailable. Session filters still work normally.
          </Alert>
        )}

        <CurationInventoryFilterBar
          adapterKeys={inventory.adapterKeys}
          adapterOptions={inventory.adapterOptions}
          hasActiveFilters={inventory.hasActiveFilters}
          isRefreshing={inventory.listQuery.isFetching || inventory.statsQuery.isFetching}
          onClearAdapterKeys={inventory.clearAdapterKeys}
          onClearAllFilters={inventory.clearAllFilters}
          onClearProfileKeys={inventory.clearProfileKeys}
          onClearStatuses={inventory.clearStatuses}
          onSearchChange={inventory.handleSearchChange}
          onToggleAdapterKey={inventory.toggleAdapterKey}
          onToggleProfileKey={inventory.toggleProfileKey}
          onToggleStatus={inventory.toggleStatus}
          profileKeys={inventory.profileKeys}
          profileOptions={inventory.profileOptions}
          searchInput={inventory.searchInput}
          stats={inventory.statsQuery.data?.stats}
          statuses={inventory.statuses}
        />

        <Box sx={{ position: 'relative' }}>
          {inventory.listQuery.isLoading && (
            <LinearProgress
              sx={{
                position: 'absolute',
                top: -1,
                left: 0,
                right: 0,
                zIndex: 1,
              }}
            />
          )}
          <CurationInventoryTable
            errorMessage={sessionErrorMessage}
            hasActiveFilters={inventory.hasActiveFilters}
            isLoading={inventory.listQuery.isLoading}
            isRefreshing={inventory.listQuery.isFetching}
            onClearFilters={inventory.clearAllFilters}
            onPageChange={inventory.handlePageChange}
            onPageSizeChange={inventory.handlePageSizeChange}
            onRetry={() => {
              void inventory.listQuery.refetch()
              void inventory.statsQuery.refetch()
            }}
            onRowClick={(sessionId) => navigate(`/curation/${sessionId}`)}
            onSortChange={inventory.handleSortChange}
            pageInfo={inventory.pageInfo}
            sessions={inventory.listQuery.data?.sessions ?? []}
            sortBy={inventory.sortBy}
            sortDirection={inventory.sortDirection}
          />
        </Box>
      </Stack>
    </Box>
  )
}

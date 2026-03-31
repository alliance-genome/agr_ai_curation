import { useMemo } from 'react'
import { Box, LinearProgress, Stack, Typography } from '@mui/material'
import { useNavigate } from 'react-router-dom'

import {
  CurationInventoryFilterBar,
  CurationInventoryTable,
  InventoryStatsCards,
  QueueNavigationButton,
  SavedViewSelector,
  useCurationInventory,
} from '../features/curation/inventory'
import { buildCurationQueueNavigationState } from '../features/curation/services/curationQueueNavigationService'

export default function CurationInventoryPage() {
  const navigate = useNavigate()
  const inventory = useCurationInventory()
  const queueRequest = useMemo(
    () => ({
      filters: inventory.filters,
      sort_by: inventory.sortBy,
      sort_direction: inventory.sortDirection,
    }),
    [inventory.filters, inventory.sortBy, inventory.sortDirection],
  )
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

        <Box data-testid="curation-inventory-stats-slot">
          <InventoryStatsCards
            errorMessage={statsErrorMessage}
            isPending={inventory.statsQuery.isFetching}
            onRetry={() => {
              void inventory.statsQuery.refetch()
            }}
            stats={inventory.statsQuery.data?.stats}
          />
        </Box>

        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <SavedViewSelector
            filters={inventory.filters}
            onApplyView={inventory.applySavedView}
            onClearSelection={inventory.clearSavedViewSelection}
            selectedViewId={inventory.savedViewId}
            sortBy={inventory.sortBy}
            sortDirection={inventory.sortDirection}
          />
        </Box>

        <CurationInventoryFilterBar
          adapterKeys={inventory.adapterKeys}
          adapterOptions={inventory.adapterOptions}
          hasActiveFilters={inventory.hasActiveFilters}
          isRefreshing={inventory.listQuery.isFetching || inventory.statsQuery.isFetching}
          onClearAdapterKeys={inventory.clearAdapterKeys}
          onClearAllFilters={inventory.clearAllFilters}
          onClearStatuses={inventory.clearStatuses}
          onSearchChange={inventory.handleSearchChange}
          onToggleAdapterKey={inventory.toggleAdapterKey}
          onToggleStatus={inventory.toggleStatus}
          queueActions={<QueueNavigationButton request={queueRequest} />}
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
            filters={inventory.filters}
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
            onRowClick={(sessionId) => {
              navigate(`/curation/${sessionId}`, {
                state: buildCurationQueueNavigationState(queueRequest),
              })
            }}
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

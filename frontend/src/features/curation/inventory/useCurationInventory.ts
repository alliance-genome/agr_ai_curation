import { useDeferredValue, useEffect, useState } from 'react'

import type {
  CurationSavedView,
  CurationSessionFilters,
  CurationSessionSortField,
  CurationSessionStatus,
  CurationSessionSummary,
  CurationSortDirection,
} from '../types'
import { type InventoryFilterOption } from './inventoryPresentation'
import {
  useCurationSessionList,
  useCurationSessionStats,
} from './curationInventoryService'

const DEFAULT_SORT_BY: CurationSessionSortField = 'prepared_at'
const DEFAULT_SORT_DIRECTION: CurationSortDirection = 'desc'
const DEFAULT_PAGE_SIZE = 25
const EMPTY_FILTERS: CurationSessionFilters = {
  statuses: [],
  adapter_keys: [],
  curator_ids: [],
  tags: [],
  flow_run_id: null,
  origin_session_id: null,
  document_id: null,
  search: null,
  prepared_between: null,
  last_worked_between: null,
  saved_view_id: null,
}

function toggleValue<T extends string>(values: T[], value: T): T[] {
  return values.includes(value)
    ? values.filter((currentValue) => currentValue !== value)
    : [...values, value]
}

function defaultSortDirectionForField(field: CurationSessionSortField): CurationSortDirection {
  if (field === 'prepared_at' || field === 'last_worked_at' || field === 'validation' || field === 'evidence') {
    return 'desc'
  }

  return 'asc'
}

function normalizeFilters(filters?: CurationSessionFilters): CurationSessionFilters {
  return {
    statuses: [...(filters?.statuses ?? [])],
    adapter_keys: [...(filters?.adapter_keys ?? [])],
    curator_ids: [...(filters?.curator_ids ?? [])],
    tags: [...(filters?.tags ?? [])],
    flow_run_id: filters?.flow_run_id ?? null,
    origin_session_id: null,
    document_id: filters?.document_id ?? null,
    search: filters?.search?.trim() || null,
    prepared_between: filters?.prepared_between
      ? {
          from_at: filters.prepared_between.from_at ?? null,
          to_at: filters.prepared_between.to_at ?? null,
        }
      : null,
    last_worked_between: filters?.last_worked_between
      ? {
          from_at: filters.last_worked_between.from_at ?? null,
          to_at: filters.last_worked_between.to_at ?? null,
        }
      : null,
    saved_view_id: null,
  }
}

function buildAdapterOptions(sessions: CurationSessionSummary[]): InventoryFilterOption[] {
  const optionsByKey = new Map<string, InventoryFilterOption>()

  sessions.forEach((session) => {
    if (!optionsByKey.has(session.adapter.adapter_key)) {
      optionsByKey.set(session.adapter.adapter_key, {
        key: session.adapter.adapter_key,
        label: session.adapter.display_label || session.adapter.adapter_key,
        colorToken: session.adapter.color_token,
      })
    }
  })

  return Array.from(optionsByKey.values())
}

function mergeOptions(
  existingOptions: InventoryFilterOption[],
  nextOptions: InventoryFilterOption[]
): InventoryFilterOption[] {
  const merged = new Map(existingOptions.map((option) => [option.key, option]))

  nextOptions.forEach((option) => {
    merged.set(option.key, merged.get(option.key) || option)
  })

  return Array.from(merged.values()).sort((left, right) => left.label.localeCompare(right.label))
}

function ensureSelectedOptions(
  options: InventoryFilterOption[],
  selectedKeys: string[]
): InventoryFilterOption[] {
  const merged = new Map(options.map((option) => [option.key, option]))

  selectedKeys.forEach((selectedKey) => {
    if (!merged.has(selectedKey)) {
      merged.set(selectedKey, {
        key: selectedKey,
        label: selectedKey,
        colorToken: null,
      })
    }
  })

  return Array.from(merged.values()).sort((left, right) => left.label.localeCompare(right.label))
}

function hasActiveFilters(filters: CurationSessionFilters, searchInput: string): boolean {
  return (
    (filters.statuses?.length ?? 0) > 0 ||
    (filters.adapter_keys?.length ?? 0) > 0 ||
    (filters.curator_ids?.length ?? 0) > 0 ||
    (filters.tags?.length ?? 0) > 0 ||
    Boolean(filters.flow_run_id) ||
    Boolean(filters.origin_session_id) ||
    Boolean(filters.document_id) ||
    Boolean(filters.prepared_between?.from_at || filters.prepared_between?.to_at) ||
    Boolean(filters.last_worked_between?.from_at || filters.last_worked_between?.to_at) ||
    searchInput.trim().length > 0
  )
}

export function useCurationInventory() {
  const [baseFilters, setBaseFilters] = useState<CurationSessionFilters>(EMPTY_FILTERS)
  const [savedViewId, setSavedViewId] = useState<string | null>(null)
  const [searchInput, setSearchInput] = useState('')
  const deferredSearchInput = useDeferredValue(searchInput)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [sortBy, setSortBy] = useState<CurationSessionSortField>(DEFAULT_SORT_BY)
  const [sortDirection, setSortDirection] = useState<CurationSortDirection>(DEFAULT_SORT_DIRECTION)
  const [knownAdapterOptions, setKnownAdapterOptions] = useState<InventoryFilterOption[]>([])

  const search = deferredSearchInput.trim()
  const filters: CurationSessionFilters = {
    ...baseFilters,
    search: search || null,
    saved_view_id: savedViewId,
  }
  const statsFilters: CurationSessionFilters = {
    ...filters,
    statuses: [],
  }

  const listQuery = useCurationSessionList({
    filters,
    sort_by: sortBy,
    sort_direction: sortDirection,
    page,
    page_size: pageSize,
  })

  const statsQuery = useCurationSessionStats({
    filters: statsFilters,
  })

  useEffect(() => {
    const sessions = listQuery.data?.sessions ?? []
    if (sessions.length === 0) {
      return
    }

    setKnownAdapterOptions((currentOptions) => mergeOptions(currentOptions, buildAdapterOptions(sessions)))
  }, [listQuery.data?.sessions])

  const statuses = baseFilters.statuses ?? []
  const adapterKeys = baseFilters.adapter_keys ?? []
  const adapterOptions = ensureSelectedOptions(knownAdapterOptions, adapterKeys)
  const pageInfo = listQuery.data?.page_info

  function applyManualFilterChange(
    updater: (currentFilters: CurationSessionFilters) => CurationSessionFilters
  ) {
    setPage(1)
    setSavedViewId(null)
    setBaseFilters((currentFilters) => updater(currentFilters))
  }

  function toggleStatus(status: CurationSessionStatus) {
    applyManualFilterChange((currentFilters) => ({
      ...currentFilters,
      statuses: toggleValue(currentFilters.statuses ?? [], status),
    }))
  }

  function clearStatuses() {
    applyManualFilterChange((currentFilters) => ({
      ...currentFilters,
      statuses: [],
    }))
  }

  function toggleAdapterKey(adapterKey: string) {
    applyManualFilterChange((currentFilters) => ({
      ...currentFilters,
      adapter_keys: toggleValue(currentFilters.adapter_keys ?? [], adapterKey),
    }))
  }

  function clearAdapterKeys() {
    applyManualFilterChange((currentFilters) => ({
      ...currentFilters,
      adapter_keys: [],
    }))
  }

  function clearAllFilters() {
    setPage(1)
    setSavedViewId(null)
    setBaseFilters(EMPTY_FILTERS)
    setSearchInput('')
  }

  function clearSavedViewSelection() {
    setSavedViewId(null)
  }

  function handleSearchChange(nextValue: string) {
    setPage(1)
    setSavedViewId(null)
    setSearchInput(nextValue)
    setBaseFilters((currentFilters) => ({
      ...currentFilters,
      search: nextValue.trim() || null,
    }))
  }

  function handleSortChange(field: CurationSessionSortField) {
    setPage(1)
    setSavedViewId(null)

    if (sortBy === field) {
      setSortDirection((currentDirection) => currentDirection === 'asc' ? 'desc' : 'asc')
      return
    }

    setSortBy(field)
    setSortDirection(defaultSortDirectionForField(field))
  }

  function handlePageChange(nextPage: number) {
    setPage(nextPage)
  }

  function handlePageSizeChange(nextPageSize: number) {
    setPage(1)
    setPageSize(nextPageSize)
  }

  function applySavedView(savedView: CurationSavedView) {
    const nextFilters = normalizeFilters(savedView.filters)
    setPage(1)
    setSavedViewId(savedView.view_id)
    setBaseFilters(nextFilters)
    setSearchInput(nextFilters.search ?? '')
    setSortBy(savedView.sort_by)
    setSortDirection(savedView.sort_direction)
  }

  return {
    statuses,
    adapterKeys,
    searchInput,
    page,
    pageSize,
    sortBy,
    sortDirection,
    pageInfo,
    adapterOptions,
    filters,
    savedViewId,
    listQuery,
    statsQuery,
    hasActiveFilters: hasActiveFilters(baseFilters, searchInput),
    toggleStatus,
    clearStatuses,
    toggleAdapterKey,
    clearAdapterKeys,
    clearAllFilters,
    clearSavedViewSelection,
    applySavedView,
    handleSearchChange,
    handleSortChange,
    handlePageChange,
    handlePageSizeChange,
  }
}

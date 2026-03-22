import { useDeferredValue, useEffect, useState } from 'react'

import type {
  CurationSessionFilters,
  CurationSessionSortField,
  CurationSessionStatus,
  CurationSortDirection,
  CurationSessionSummary,
} from '../types'
import { getAdapterLabel, type InventoryFilterOption } from './inventoryPresentation'
import {
  useCurationSessionList,
  useCurationSessionStats,
} from './curationInventoryService'

const DEFAULT_SORT_BY: CurationSessionSortField = 'prepared_at'
const DEFAULT_SORT_DIRECTION: CurationSortDirection = 'desc'
const DEFAULT_PAGE_SIZE = 25

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

function buildProfileOptions(sessions: CurationSessionSummary[]): InventoryFilterOption[] {
  const optionsByKey = new Map<string, InventoryFilterOption>()

  sessions.forEach((session) => {
    if (!session.adapter.profile_key) {
      return
    }

    if (!optionsByKey.has(session.adapter.profile_key)) {
      optionsByKey.set(session.adapter.profile_key, {
        key: session.adapter.profile_key,
        label: session.adapter.profile_label || getAdapterLabel(session.adapter),
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

function buildFilters(
  statuses: CurationSessionStatus[],
  adapterKeys: string[],
  profileKeys: string[],
  search: string
): CurationSessionFilters {
  return {
    statuses,
    adapter_keys: adapterKeys,
    profile_keys: profileKeys,
    domain_keys: [],
    curator_ids: [],
    tags: [],
    flow_run_id: null,
    document_id: null,
    search: search || null,
    prepared_between: null,
    last_worked_between: null,
    saved_view_id: null,
  }
}

export function useCurationInventory() {
  const [statuses, setStatuses] = useState<CurationSessionStatus[]>([])
  const [adapterKeys, setAdapterKeys] = useState<string[]>([])
  const [profileKeys, setProfileKeys] = useState<string[]>([])
  const [searchInput, setSearchInput] = useState('')
  const deferredSearchInput = useDeferredValue(searchInput)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [sortBy, setSortBy] = useState<CurationSessionSortField>(DEFAULT_SORT_BY)
  const [sortDirection, setSortDirection] = useState<CurationSortDirection>(DEFAULT_SORT_DIRECTION)
  const [knownAdapterOptions, setKnownAdapterOptions] = useState<InventoryFilterOption[]>([])
  const [knownProfileOptions, setKnownProfileOptions] = useState<InventoryFilterOption[]>([])

  const search = deferredSearchInput.trim()
  const filters = buildFilters(statuses, adapterKeys, profileKeys, search)
  const statsFilters = {
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
    setKnownProfileOptions((currentOptions) => mergeOptions(currentOptions, buildProfileOptions(sessions)))
  }, [listQuery.data?.sessions])

  const adapterOptions = knownAdapterOptions
  const profileOptions = knownProfileOptions
  const pageInfo = listQuery.data?.page_info
  const hasActiveFilters =
    statuses.length > 0 ||
    adapterKeys.length > 0 ||
    profileKeys.length > 0 ||
    searchInput.trim().length > 0

  function toggleStatus(status: CurationSessionStatus) {
    setPage(1)
    setStatuses((currentValues) => toggleValue(currentValues, status))
  }

  function clearStatuses() {
    setPage(1)
    setStatuses([])
  }

  function toggleAdapterKey(adapterKey: string) {
    setPage(1)
    setAdapterKeys((currentValues) => toggleValue(currentValues, adapterKey))
  }

  function clearAdapterKeys() {
    setPage(1)
    setAdapterKeys([])
  }

  function toggleProfileKey(profileKey: string) {
    setPage(1)
    setProfileKeys((currentValues) => toggleValue(currentValues, profileKey))
  }

  function clearProfileKeys() {
    setPage(1)
    setProfileKeys([])
  }

  function clearAllFilters() {
    setPage(1)
    setStatuses([])
    setAdapterKeys([])
    setProfileKeys([])
    setSearchInput('')
  }

  function handleSearchChange(nextValue: string) {
    setPage(1)
    setSearchInput(nextValue)
  }

  function handleSortChange(field: CurationSessionSortField) {
    setPage(1)

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

  return {
    statuses,
    adapterKeys,
    profileKeys,
    searchInput,
    page,
    pageSize,
    sortBy,
    sortDirection,
    pageInfo,
    adapterOptions,
    profileOptions,
    filters,
    listQuery,
    statsQuery,
    hasActiveFilters,
    toggleStatus,
    clearStatuses,
    toggleAdapterKey,
    clearAdapterKeys,
    toggleProfileKey,
    clearProfileKeys,
    clearAllFilters,
    handleSearchChange,
    handleSortChange,
    handlePageChange,
    handlePageSizeChange,
  }
}

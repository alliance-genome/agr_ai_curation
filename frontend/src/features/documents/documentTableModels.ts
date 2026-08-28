export interface DocumentTablePaginationModel {
  page: number
  pageSize: number
}

export interface DocumentTableSortItem {
  field: string
  sort: 'asc' | 'desc'
}

export type DocumentTableSortModel = DocumentTableSortItem[]

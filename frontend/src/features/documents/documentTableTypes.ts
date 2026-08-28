export interface DocumentPaginationModel {
  page: number
  pageSize: number
}

export type DocumentSortDirection = 'asc' | 'desc'

export interface DocumentSortItem {
  field: string
  sort: DocumentSortDirection
}

export type DocumentSortModel = DocumentSortItem[]

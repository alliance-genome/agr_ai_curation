import type { ReactNode } from 'react'

import type { FieldRowInputProps } from '@/features/curation/editor'

export interface CurationAdapterFieldLayoutEntry {
  fieldKey: string
  label: string
  groupKey: string
  groupLabel: string
  order: number
  widget?: string
}

export interface CurationAdapterEditorPack {
  adapterKey: string
  fieldLayout: readonly CurationAdapterFieldLayoutEntry[]
  renderFieldInput: (props: FieldRowInputProps) => ReactNode
}


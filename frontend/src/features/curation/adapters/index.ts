import type { CurationAdapterEditorPack } from './types'

import { referenceEditorPack } from './reference'

const CURATION_ADAPTER_EDITOR_PACKS = new Map<string, CurationAdapterEditorPack>([
  [referenceEditorPack.adapterKey, referenceEditorPack],
])

export function getCurationAdapterEditorPack(
  adapterKey?: string | null,
): CurationAdapterEditorPack | null {
  if (!adapterKey) {
    return null
  }

  return CURATION_ADAPTER_EDITOR_PACKS.get(adapterKey) ?? null
}

export { REFERENCE_ADAPTER_KEY } from './reference'
export type {
  CurationAdapterEditorPack,
  CurationAdapterFieldLayoutEntry,
} from './types'


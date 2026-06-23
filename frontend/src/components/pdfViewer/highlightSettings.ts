import type { Theme } from '@mui/material/styles'
import { safeGetJson } from '@/lib/browserStorage'

export interface HighlightSettings {
  highlightColor: string
  highlightOpacity: number
  clearOnNewQuery: boolean
}

export const PDF_VIEWER_SETTINGS_STORAGE_KEY = 'pdf-viewer-settings'

const DEFAULT_HIGHLIGHT_OPACITY = 0.35
const DEFAULT_CLEAR_ON_NEW_QUERY = true

export function buildDefaultHighlightSettings(highlightColor: string): HighlightSettings {
  return {
    highlightColor,
    highlightOpacity: DEFAULT_HIGHLIGHT_OPACITY,
    clearOnNewQuery: DEFAULT_CLEAR_ON_NEW_QUERY,
  }
}

export function getDefaultHighlightSettings(theme: Theme): HighlightSettings {
  return buildDefaultHighlightSettings(theme.palette.success.main)
}

export function normalizeHighlightSettings(
  settings: Partial<HighlightSettings> | null | undefined,
  defaults: HighlightSettings,
): HighlightSettings {
  return {
    highlightColor: settings?.highlightColor ?? defaults.highlightColor,
    highlightOpacity: typeof settings?.highlightOpacity === 'number'
      ? settings.highlightOpacity
      : defaults.highlightOpacity,
    clearOnNewQuery: typeof settings?.clearOnNewQuery === 'boolean'
      ? settings.clearOnNewQuery
      : defaults.clearOnNewQuery,
  }
}

export function loadStoredHighlightSettings(defaults: HighlightSettings): HighlightSettings {
  const stored = safeGetJson<Partial<HighlightSettings>>(
    () => window.localStorage,
    PDF_VIEWER_SETTINGS_STORAGE_KEY,
    {
      owner: 'preferences',
      key: PDF_VIEWER_SETTINGS_STORAGE_KEY,
    },
  )
  if (!stored.ok || !stored.value) return defaults
  return normalizeHighlightSettings(stored.value, defaults)
}

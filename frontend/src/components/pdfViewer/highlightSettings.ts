import type { Theme } from '@mui/material/styles'

export interface HighlightSettings {
  highlightColor: string
  highlightOpacity: number
  clearOnNewQuery: boolean
}

export const PDF_VIEWER_SETTINGS_STORAGE_KEY = 'pdf-viewer-settings'

const DEFAULT_HIGHLIGHT_OPACITY = 0.35
const DEFAULT_CLEAR_ON_NEW_QUERY = true

export function getDefaultHighlightSettings(theme: Theme): HighlightSettings {
  return {
    highlightColor: theme.palette.success.main,
    highlightOpacity: DEFAULT_HIGHLIGHT_OPACITY,
    clearOnNewQuery: DEFAULT_CLEAR_ON_NEW_QUERY,
  }
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
  try {
    const raw = localStorage.getItem(PDF_VIEWER_SETTINGS_STORAGE_KEY)
    if (!raw) return defaults
    return normalizeHighlightSettings(JSON.parse(raw) as Partial<HighlightSettings>, defaults)
  } catch (error) {
    console.warn('Failed to load viewer settings', error)
    return defaults
  }
}

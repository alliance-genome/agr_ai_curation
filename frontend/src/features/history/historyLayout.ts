// Shared layout constants for the compact history list (ALL-1013 mockup values).
export const HISTORY_ROW_GRID_COLUMNS = '36px minmax(0, 1fr) 96px 84px'
export const HISTORY_ROW_MIN_HEIGHT = 44
export const HISTORY_LIST_HEADER_HEIGHT = 34
export const HISTORY_KIND_TAG_WIDTH = 68
export const HISTORY_TRANSCRIPT_MAX_HEIGHT = 320
export const HISTORY_PANEL_INDENT = 46
export const HISTORY_MONO_FONT_FAMILY = '"Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace'

// Screen-reader-only text (MUI visuallyHidden equivalent, kept local to avoid a transitive import).
export const HISTORY_VISUALLY_HIDDEN = {
  border: 0,
  clip: 'rect(0 0 0 0)',
  height: '1px',
  margin: '-1px',
  overflow: 'hidden',
  padding: 0,
  position: 'absolute',
  whiteSpace: 'nowrap',
  width: '1px',
} as const

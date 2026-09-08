/**
 * Geometry of the docked node panel.
 *
 * Docked and resizable inside the canvas area: 440px by default, never
 * narrower than 380, and no wider than half the canvas area while that half
 * is at least 380; below that the dock holds 380 and the canvas takes the
 * rest. It collapses to a 44px rail. Docked is the normal desktop mode; only
 * below 760px of builder width (palette, canvas, and dock together) does it
 * become a full-height drawer inside the builder instead.
 */

export const NODE_PANEL_DEFAULT_WIDTH = 440
export const NODE_PANEL_MIN_WIDTH = 380
export const NODE_PANEL_MAX_FRACTION = 0.5
export const NODE_PANEL_RAIL_WIDTH = 44
export const NODE_PANEL_DRAWER_BELOW = 760
export const NODE_PANEL_WIDTH_STORAGE_KEY = 'agent-studio-flow-node-panel-width'

export type NodePanelMode = 'docked' | 'drawer'

/** Docked until the builder root is measured narrower than the drawer breakpoint. */
export function nodePanelMode(builderWidth: number | null): NodePanelMode {
  if (builderWidth !== null && builderWidth > 0 && builderWidth < NODE_PANEL_DRAWER_BELOW) {
    return 'drawer'
  }
  return 'docked'
}

/**
 * Clamp a requested panel width to the allowed range for a canvas area.
 * The 380 minimum wins: the half-area cap applies only while half the area
 * is at least 380, so the panel content never squeezes below its minimum.
 */
export function clampNodePanelWidth(width: number, areaWidth: number | null): number {
  const rounded = Math.round(width)
  if (areaWidth === null || areaWidth <= 0) {
    return Math.max(NODE_PANEL_MIN_WIDTH, rounded)
  }
  const max = Math.max(NODE_PANEL_MIN_WIDTH, Math.floor(areaWidth * NODE_PANEL_MAX_FRACTION))
  return Math.min(max, Math.max(NODE_PANEL_MIN_WIDTH, rounded))
}

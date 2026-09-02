import { describe, expect, it } from 'vitest'

import {
  NODE_PANEL_DEFAULT_WIDTH,
  NODE_PANEL_MIN_WIDTH,
  clampNodePanelWidth,
  nodePanelMode,
} from './nodePanelLayout'

describe('nodePanelMode', () => {
  it('docks on the desktop and only becomes a drawer below 760px of builder width', () => {
    expect(nodePanelMode(null)).toBe('docked')
    expect(nodePanelMode(0)).toBe('docked')
    expect(nodePanelMode(1200)).toBe('docked')
    // 1440px viewport with the Claude pane open leaves the builder about 989px wide.
    expect(nodePanelMode(989)).toBe('docked')
    expect(nodePanelMode(760)).toBe('docked')
    expect(nodePanelMode(759)).toBe('drawer')
  })
})

describe('clampNodePanelWidth', () => {
  it('keeps the default inside a wide canvas area', () => {
    expect(clampNodePanelWidth(NODE_PANEL_DEFAULT_WIDTH, 1200)).toBe(440)
  })

  it('caps the panel at half the canvas area', () => {
    expect(clampNodePanelWidth(900, 1200)).toBe(600)
  })

  it('applies the 380 minimum while half the area allows it', () => {
    expect(clampNodePanelWidth(100, 1200)).toBe(NODE_PANEL_MIN_WIDTH)
    expect(clampNodePanelWidth(300, 800)).toBe(NODE_PANEL_MIN_WIDTH)
    expect(clampNodePanelWidth(440, 800)).toBe(400)
  })

  it('caps at half the area even below the minimum so the canvas stays usable', () => {
    expect(clampNodePanelWidth(440, 600)).toBe(300)
    expect(clampNodePanelWidth(380, 480)).toBe(240)
  })

  it('only applies the minimum before the area is measured', () => {
    expect(clampNodePanelWidth(900, null)).toBe(900)
    expect(clampNodePanelWidth(200, null)).toBe(NODE_PANEL_MIN_WIDTH)
  })
})

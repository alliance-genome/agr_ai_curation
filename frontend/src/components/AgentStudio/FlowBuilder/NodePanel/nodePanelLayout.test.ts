import { describe, expect, it } from 'vitest'

import {
  NODE_PANEL_DEFAULT_WIDTH,
  NODE_PANEL_MIN_WIDTH,
  clampNodePanelWidth,
  nodePanelMode,
} from './nodePanelLayout'

describe('nodePanelMode', () => {
  it('docks until the builder is measured narrower than 1100px', () => {
    expect(nodePanelMode(null)).toBe('docked')
    expect(nodePanelMode(0)).toBe('docked')
    expect(nodePanelMode(1100)).toBe('docked')
    expect(nodePanelMode(1440)).toBe('docked')
    expect(nodePanelMode(1099)).toBe('drawer')
    expect(nodePanelMode(720)).toBe('drawer')
  })
})

describe('clampNodePanelWidth', () => {
  it('keeps the default inside a wide canvas area', () => {
    expect(clampNodePanelWidth(NODE_PANEL_DEFAULT_WIDTH, 1200)).toBe(440)
  })

  it('caps the panel at half the canvas area', () => {
    expect(clampNodePanelWidth(900, 1200)).toBe(600)
  })

  it('never goes below the minimum, even when half the area is smaller', () => {
    expect(clampNodePanelWidth(100, 1200)).toBe(NODE_PANEL_MIN_WIDTH)
    expect(clampNodePanelWidth(440, 600)).toBe(NODE_PANEL_MIN_WIDTH)
  })

  it('only applies the minimum before the area is measured', () => {
    expect(clampNodePanelWidth(900, null)).toBe(900)
    expect(clampNodePanelWidth(200, null)).toBe(NODE_PANEL_MIN_WIDTH)
  })
})

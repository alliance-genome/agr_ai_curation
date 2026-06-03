import { describe, it, expect } from 'vitest'
import { createAppTheme } from './theme'

describe('app theme (dark)', () => {
  const t = createAppTheme('dark')
  it('uses the Geist font stack', () => {
    expect(t.typography.fontFamily).toMatch(/^"Geist"/)
  })
  it('uses the vivid (not neon, not washed-out) blue accent', () => {
    expect(t.palette.primary.main.toLowerCase()).toBe('#3b82f6')
  })
  it('keeps layered surfaces with real contrast', () => {
    expect(t.palette.background.default.toLowerCase()).toBe('#0f1217')
    expect(t.palette.background.paper.toLowerCase()).toBe('#1b212b')
    expect(t.palette.background.default).not.toBe(t.palette.background.paper)
  })
  it('renders a colored AppBar (not flat dark)', () => {
    const appBar = (t.components?.MuiAppBar?.styleOverrides?.root ?? {}) as Record<string, unknown>
    expect(String(appBar.backgroundColor).toLowerCase()).toBe('#1c5fb8')
  })
  it('enables tabular figures on the body', () => {
    const body = (t.components?.MuiCssBaseline?.styleOverrides as { body?: Record<string, unknown> } | undefined)?.body ?? {}
    expect(body.fontVariantNumeric).toBe('tabular-nums')
  })
})

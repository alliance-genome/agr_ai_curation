import { afterEach, describe, expect, it, vi } from 'vitest'

import { LatestIntent } from './latestIntent'

describe('LatestIntent', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.resetModules()
  })

  it('orders a fresh click in an older tab after a document change in a newer tab', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(2_000_000_000_000)
    vi.resetModules()
    const { LatestIntent: OlderTab } = await import('./latestIntent')
    const olderTab = new OlderTab()
    olderTab.begin()

    clock.mockReturnValue(2_000_000_060_000)
    vi.resetModules()
    const { LatestIntent: NewerTab } = await import('./latestIntent')
    const newerDocumentLoad = new NewerTab().begin()

    clock.mockReturnValue(2_000_000_120_000)
    const unloadFromOlderTab = olderTab.begin()
    expect(unloadFromOlderTab.generation).toBeGreaterThan(newerDocumentLoad.generation)

    // Keep local operation ordering even if the clock moves backwards.
    clock.mockReturnValue(2_000_000_100_000)
    expect(olderTab.begin().generation).toBeGreaterThan(unloadFromOlderTab.generation)
    expect(unloadFromOlderTab.signal.aborted).toBe(true)
  })
  it('supersedes and aborts the previous operation with a newer generation', () => {
    const intents = new LatestIntent()
    const first = intents.begin()
    const second = intents.begin()

    expect(second.generation).toBeGreaterThan(first.generation)
    expect(second.owner).toBe(first.owner)
    expect(first.signal.aborted).toBe(true)
    expect(first.ownsLatest()).toBe(false)
    expect(second.signal.aborted).toBe(false)
    expect(second.ownsLatest()).toBe(true)
  })

  it('invalidates and aborts the current operation', () => {
    const intents = new LatestIntent()
    const operation = intents.begin()

    intents.invalidate()

    expect(operation.signal.aborted).toBe(true)
    expect(operation.ownsLatest()).toBe(false)
  })

  it('uses monotonic generations across operation families', () => {
    const first = new LatestIntent().begin()
    const second = new LatestIntent().begin()

    expect(second.generation).toBeGreaterThan(first.generation)
  })
})

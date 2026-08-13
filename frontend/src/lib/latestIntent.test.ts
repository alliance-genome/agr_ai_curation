import { describe, expect, it } from 'vitest'

import { LatestIntent } from './latestIntent'

describe('LatestIntent', () => {
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

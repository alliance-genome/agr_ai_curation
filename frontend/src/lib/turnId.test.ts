import { beforeEach, describe, expect, it, vi } from 'vitest'

import { buildTurnId } from './turnId'

describe('buildTurnId', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('uses crypto.randomUUID when available', () => {
    const randomUUID = vi.spyOn(globalThis.crypto, 'randomUUID')
      .mockReturnValue('11111111-2222-3333-4444-555555555555')

    expect(buildTurnId()).toBe('11111111-2222-3333-4444-555555555555')
    expect(randomUUID).toHaveBeenCalledOnce()
  })

  it('builds a usable turn ID when crypto.randomUUID throws', () => {
    vi.spyOn(globalThis.crypto, 'randomUUID')
      .mockImplementation(() => { throw new TypeError('crypto.randomUUID is not a function') })

    expect(buildTurnId()).toMatch(/^turn-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+$/)
  })
})

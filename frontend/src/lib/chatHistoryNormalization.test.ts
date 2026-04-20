import { describe, expect, it } from 'vitest'

import { normalizeChatHistoryValue } from './chatHistoryNormalization'

describe('normalizeChatHistoryValue', () => {
  it('trims non-empty history values', () => {
    expect(normalizeChatHistoryValue('  TP53  ')).toBe('TP53')
  })

  it('collapses blank and nullish history values to null', () => {
    expect(normalizeChatHistoryValue('   ')).toBeNull()
    expect(normalizeChatHistoryValue(null)).toBeNull()
    expect(normalizeChatHistoryValue(undefined)).toBeNull()
  })
})

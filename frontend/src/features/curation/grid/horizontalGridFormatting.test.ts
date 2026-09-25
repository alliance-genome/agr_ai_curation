import { describe, expect, it } from 'vitest'

import type { DomainEnvelopeReviewResolvedValue } from '@/features/curation/types'
import { formatHorizontalGridValue, horizontalGridOverrideText } from './horizontalGridFormatting'

describe('horizontal grid formatting', () => {
  it('never shows a curator override record as part of a value', () => {
    expect(formatHorizontalGridValue({
      abbreviation: 'XB',
      mention: 'Xenbase',
      resolution_state: 'resolved',
      lookup_outcome: 'curator_override',
      curator_override: { actor_id: 'sub-1', at: '2026-09-23T20:00:00+00:00' },
    })).toBe('abbreviation: XB')
  })

  it('names the curator by display name, never by account id', () => {
    const value = {
      curator_override: { actor_id: 'sub-1', at: '2026-09-23T20:00:00+00:00' },
    } as DomainEnvelopeReviewResolvedValue
    expect(horizontalGridOverrideText(value)).toBe('Curator override on 2026-09-23 20:00 UTC')
    expect(horizontalGridOverrideText({
      ...value,
      curator_override: { ...value.curator_override!, actor_display_name: 'Pat Curator' },
    })).toBe('Curator override by Pat Curator on 2026-09-23 20:00 UTC')
  })
})

import { describe, expect, it } from 'vitest'

import type { CurationCandidate, DomainEnvelopeReviewRow } from '@/features/curation/types'
import { objectSelectorLabel } from './objectSelector'

describe('objectSelector', () => {
  it('prefers the projected review label over candidate fallbacks', () => {
    expect(objectSelectorLabel({
      candidate: {
        candidate_id: 'candidate-1',
        display_label: 'Candidate label',
        draft: { title: 'Draft title' },
        projection_ref: { object_id: 'object-1' },
      } as CurationCandidate,
      reviewRow: { display_label: 'Projected label' } as DomainEnvelopeReviewRow,
    })).toBe('Projected label')
  })
})

import { describe, expect, it } from 'vitest'

import { progressSegments, selectorPosition } from './objectSelector'

const candidate = (id: string, status: 'accepted' | 'pending' | 'rejected') => ({
  candidate_id: id,
  status,
})

describe('objectSelector', () => {
  it('maps candidates to progress segments by status', () => {
    const segments = progressSegments(
      [
        candidate('a', 'accepted'),
        candidate('b', 'pending'),
        candidate('c', 'rejected'),
        candidate('d', 'pending'),
      ],
      'b',
    )

    expect(segments.map((segment) => segment.kind)).toEqual([
      'done',
      'current',
      'rejected',
      'pending',
    ])
  })

  it('reports 1-based position and total', () => {
    expect(
      selectorPosition(
        [candidate('a', 'pending'), candidate('b', 'pending')],
        'b',
      ),
    ).toEqual({ position: 2, total: 2 })
  })
})

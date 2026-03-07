import { describe, expect, it } from 'vitest'

import { normalizeOverlayDocItems, reduceOverlayUpdate } from './PdfViewer'

describe('normalizeOverlayDocItems', () => {
  it('returns an empty list for missing or unusable items', () => {
    expect(normalizeOverlayDocItems(undefined)).toEqual([])
    expect(
      normalizeOverlayDocItems([
        { page: 2 },
        { page_no: 4, bbox: undefined },
      ]),
    ).toEqual([])
  })

  it('normalizes page_no to page when bbox data is present', () => {
    expect(
      normalizeOverlayDocItems([
        {
          page_no: 5,
          bbox: { left: 1, top: 2, right: 3, bottom: 0, coord_origin: 'BOTTOMLEFT' },
        },
      ]),
    ).toEqual([
      {
        page_no: 5,
        page: 5,
        bbox: { left: 1, top: 2, right: 3, bottom: 0, coord_origin: 'BOTTOMLEFT' },
      },
    ])
  })
})

describe('reduceOverlayUpdate', () => {
  it('replaces prior overlays with the newest chunk highlight', () => {
    const next = reduceOverlayUpdate(
      {
        chunkId: 'chunk-2',
        documentId: 'doc-1',
        docItems: [
          {
            page_no: 3,
            bbox: { left: 40, top: 50, right: 70, bottom: 25, coord_origin: 'BOTTOMLEFT' },
          },
        ],
      },
      'doc-1',
    )

    expect(next).toEqual([
      {
        chunkId: 'chunk-2',
        documentId: 'doc-1',
        docItems: [
          {
            page_no: 3,
            page: 3,
            bbox: { left: 40, top: 50, right: 70, bottom: 25, coord_origin: 'BOTTOMLEFT' },
          },
        ],
      },
    ])
  })

  it('clears stale overlays when the next payload has no usable bbox data', () => {
    const next = reduceOverlayUpdate(
      {
        chunkId: 'chunk-2',
        documentId: 'doc-1',
        docItems: [
          {
            page: 2,
          },
        ],
      },
      'doc-1',
    )

    expect(next).toEqual([])
  })
})

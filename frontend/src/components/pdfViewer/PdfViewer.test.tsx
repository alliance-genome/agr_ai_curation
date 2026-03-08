import { describe, expect, it } from 'vitest'

import { inspectOverlayDocItems, normalizeOverlayDocItems, reduceOverlayUpdate } from './PdfViewer'

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

describe('inspectOverlayDocItems', () => {
  it('reports dropped doc items with actionable reasons', () => {
    const inspection = inspectOverlayDocItems([
      {
        page_no: 5,
        bbox: { left: 1, top: 2, right: 3, bottom: 0, coord_origin: 'BOTTOMLEFT' },
      },
      {
        page: 2,
      } as any,
      {
        page: 3,
        bbox: { left: 4, top: 4, right: 4, bottom: 1, coord_origin: 'BOTTOMLEFT' },
      },
      {
        page: Number.NaN,
        bbox: { left: 4, top: 6, right: 7, bottom: 1, coord_origin: 'BOTTOMLEFT' },
      } as any,
    ])

    expect(inspection.normalizedDocItems).toEqual([
      {
        page_no: 5,
        page: 5,
        bbox: { left: 1, top: 2, right: 3, bottom: 0, coord_origin: 'BOTTOMLEFT' },
      },
    ])
    expect(inspection.droppedItems).toEqual([
      {
        index: 1,
        reason: 'missing-bbox',
        page: 2,
        page_no: undefined,
      },
      {
        index: 2,
        reason: 'invalid-bbox',
        page: 3,
        page_no: undefined,
        bbox: { left: 4, top: 4, right: 4, bottom: 1, coord_origin: 'BOTTOMLEFT' },
        invalidFields: ['zero-width'],
      },
      {
        index: 3,
        reason: 'missing-page',
        page: Number.NaN,
        page_no: undefined,
        bbox: { left: 4, top: 6, right: 7, bottom: 1, coord_origin: 'BOTTOMLEFT' },
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

  it('clears stale overlays when the next payload has only invalid bbox coordinates', () => {
    const next = reduceOverlayUpdate(
      {
        chunkId: 'chunk-3',
        documentId: 'doc-1',
        docItems: [
          {
            page_no: 7,
            bbox: { left: 20, top: 40, right: 20, bottom: 10, coord_origin: 'BOTTOMLEFT' },
          },
        ],
      },
      'doc-1',
    )

    expect(next).toEqual([])
  })
})

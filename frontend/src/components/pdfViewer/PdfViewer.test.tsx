import { describe, expect, it } from 'vitest'

import { reduceOverlayUpdate } from './PdfViewer'

describe('reduceOverlayUpdate', () => {
  it('replaces prior overlays with the newest chunk highlight', () => {
    const existing = [
      {
        chunkId: 'chunk-1',
        documentId: 'doc-1',
        docItems: [
          {
            page: 1,
            bbox: { left: 10, top: 20, right: 30, bottom: 5, coord_origin: 'BOTTOMLEFT' },
          },
        ],
      },
    ]

    const next = reduceOverlayUpdate(
      existing,
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
    const existing = [
      {
        chunkId: 'chunk-1',
        documentId: 'doc-1',
        docItems: [
          {
            page: 1,
            bbox: { left: 10, top: 20, right: 30, bottom: 5, coord_origin: 'BOTTOMLEFT' },
          },
        ],
      },
    ]

    const next = reduceOverlayUpdate(
      existing,
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

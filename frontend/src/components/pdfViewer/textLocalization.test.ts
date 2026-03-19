import { describe, expect, it } from 'vitest'

import {
  buildRenderedTextSearchIndex,
  createRangeForRenderedTextMatch,
  findRenderedTextMatches,
  getRenderedTextLayerReferences,
  groupClientRectsByPage,
  normalizeSearchSnippet,
} from './textLocalization'

const createRenderedPage = (pageNumber: number, parts: string[]): HTMLElement => {
  const pageElement = document.createElement('div')
  pageElement.className = 'page'
  pageElement.dataset.pageNumber = String(pageNumber)

  const textLayer = document.createElement('div')
  textLayer.className = 'textLayer'

  parts.forEach((part) => {
    const span = document.createElement('span')
    span.textContent = part
    textLayer.appendChild(span)
  })

  pageElement.appendChild(textLayer)
  document.body.appendChild(pageElement)
  return pageElement
}

describe('normalizeSearchSnippet', () => {
  it('collapses whitespace and zero-width characters', () => {
    expect(normalizeSearchSnippet('  Alpha\u200b \n\tBe\u00ADta\u00A0Gamma  ')).toBe('Alpha Beta Gamma')
  })
})

describe('rendered text localization helpers', () => {
  it('finds snippets that span multiple text nodes on the same page', () => {
    createRenderedPage(1, ['Evidence ', 'anchors ', 'need exact matches.'])

    const textLayers = getRenderedTextLayerReferences(document)
    const index = buildRenderedTextSearchIndex(textLayers)
    const matches = findRenderedTextMatches(index, 'anchors need exact')

    expect(matches).toHaveLength(1)
    expect(matches[0].pages).toEqual([1])

    const range = createRangeForRenderedTextMatch(matches[0])
    expect(range.toString().replace(/\s+/g, ' ').trim()).toContain('anchors need exact')
  })

  it('can match snippets that span rendered page boundaries', () => {
    createRenderedPage(1, ['Sentence level localization spans'])
    createRenderedPage(2, [' multiple rendered pages.'])

    const textLayers = getRenderedTextLayerReferences(document)
    const index = buildRenderedTextSearchIndex(textLayers)
    const matches = findRenderedTextMatches(index, 'spans multiple rendered')

    expect(matches).toHaveLength(1)
    expect(matches[0].pages).toEqual([1, 2])
    expect(matches[0].excerpt).toBe('spans multiple rendered')
  })

  it('maps client rects back to page-relative coordinates', () => {
    const firstPage = createRenderedPage(1, ['Page one'])
    const secondPage = createRenderedPage(2, ['Page two'])

    Object.defineProperty(firstPage, 'getBoundingClientRect', {
      value: () => ({
        left: 100,
        top: 200,
        right: 500,
        bottom: 900,
        width: 400,
        height: 700,
      }),
    })

    Object.defineProperty(secondPage, 'getBoundingClientRect', {
      value: () => ({
        left: 100,
        top: 950,
        right: 500,
        bottom: 1650,
        width: 400,
        height: 700,
      }),
    })

    const textLayers = getRenderedTextLayerReferences(document)
    const rects = groupClientRectsByPage(textLayers, [
      { left: 140, top: 260, width: 120, height: 18 },
      { left: 150, top: 1010, width: 140, height: 20 },
    ])

    expect(rects).toEqual([
      {
        pageNumber: 1,
        left: 40,
        top: 60,
        width: 120,
        height: 18,
      },
      {
        pageNumber: 2,
        left: 50,
        top: 60,
        width: 140,
        height: 20,
      },
    ])
  })
})

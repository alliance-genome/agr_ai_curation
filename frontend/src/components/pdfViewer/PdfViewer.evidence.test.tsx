import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fireEvent, render, screen, waitFor } from '../../test/test-utils'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import {
  fuzzyMatchPdfEvidenceQuote,
  type PdfEvidenceFuzzyMatchRequest,
  type PdfEvidenceFuzzyMatchResult,
} from '@/features/curation/services/pdfEvidenceMatcherService'
import PdfViewer, {
  buildEvidenceSpikeQuoteCandidates,
  buildEvidenceSpikeSectionCandidates,
  findExpandedEvidenceQueryFromPageText,
  normalizeEvidenceSpikeText,
  normalizeEvidenceSpikePageHints,
  type PdfEvidenceSpikeInput,
} from './PdfViewer'
import {
  dispatchPDFViewerNavigateEvidence,
  dispatchPDFDocumentChanged,
  onPDFViewerEvidenceAnchorSelected,
} from './pdfEvents'
import {
  buildNormalizedTextSourceMap,
  sanitizeEvidenceSearchText,
} from './textNormalization'

vi.mock('@/features/curation/services/pdfEvidenceMatcherService', () => ({
  fuzzyMatchPdfEvidenceQuote: vi.fn(),
}))

interface MockFindResponse {
  state: number
  total: number
  current: number
  pageIdx: number | null
  dispatchDelayMs?: number
  pageMatches?: number[]
  pageMatchesLength?: number[]
  viewerPageNumber?: number | null
  delayedSelection?: {
    pageIdx?: number | null
    viewerPageNumber?: number | null
    delayMs?: number
  }
  lateCount?: {
    total: number
    current: number
    delayMs?: number
  }
}

interface MockPageSpec {
  pageNumber: number
  textSegments: string[]
  pageContents?: string
}

class MockPdfEventBus {
  listeners = new Map<string, Set<(event: any) => void>>()
  findQueries: string[] = []
  findDispatches: Array<{ query: string; pageBeforeDispatch: number }> = []
  findbarCloseCount = 0

  constructor(
    private readonly onFind: (query: string) => MockFindResponse,
    private readonly getCurrentPage: () => number,
    private readonly setCurrentPage: (pageNumber: number) => void,
    private readonly findController: {
      selected: { pageIdx: number; matchIdx: number }
      pageMatches: number[][]
      pageMatchesLength: number[][]
      _pageContents: string[]
    },
    private readonly getPageContents: (pageIdx: number) => string,
    private readonly renderNativeSelection: (pageIdx: number | null, matchIdx: number | null) => void,
    private readonly clearNativeSelection: () => void,
  ) {}

  on(eventName: string, handler: (event: any) => void) {
    const handlers = this.listeners.get(eventName) ?? new Set()
    handlers.add(handler)
    this.listeners.set(eventName, handlers)
  }

  off(eventName: string, handler: (event: any) => void) {
    this.listeners.get(eventName)?.delete(handler)
  }

  dispatch(eventName: string, payload: any) {
    if (eventName === 'findbarclose') {
      this.findbarCloseCount += 1
      this.findController.selected.pageIdx = -1
      this.findController.selected.matchIdx = -1
      this.clearNativeSelection()
      return
    }

    if (eventName !== 'find') {
      const eventPayload = eventName === 'updatetextlayermatches'
        ? {
            source: payload?.source ?? this.findController,
            ...payload,
          }
        : payload
      for (const handler of this.listeners.get(eventName) ?? []) {
        handler(eventPayload)
      }
      return
    }

    this.findDispatches.push({
      query: payload.query,
      pageBeforeDispatch: this.getCurrentPage(),
    })

    const response = this.onFind(payload.query)
    this.findQueries.push(payload.query)

    if (response.pageIdx !== null && response.pageIdx >= 0) {
      const inferredRanges = inferMockQueryMatchRanges(
        this.getPageContents(response.pageIdx),
        payload.query,
      )
      this.findController.pageMatches[response.pageIdx] = response.pageMatches ?? inferredRanges.pageMatches
      this.findController.pageMatchesLength[response.pageIdx] = response.pageMatchesLength ?? inferredRanges.pageMatchesLength
    }

    const applySelection = (selection: {
      pageIdx?: number | null
      viewerPageNumber?: number | null
    }) => {
      const pageIdx = selection.pageIdx ?? null
      const pageMatchCount = pageIdx !== null
        ? (this.findController.pageMatches[pageIdx]?.length ?? 0)
        : 0
      this.findController.selected.pageIdx = pageIdx ?? -1
      this.findController.selected.matchIdx = pageIdx !== null
        ? Math.min(Math.max(response.current - 1, 0), Math.max(pageMatchCount - 1, 0))
        : -1
      if (pageIdx !== null) {
        this.setCurrentPage(pageIdx + 1)
      } else if (typeof selection.viewerPageNumber === 'number' && selection.viewerPageNumber >= 1) {
        this.setCurrentPage(selection.viewerPageNumber)
      }

      this.renderNativeSelection(
        pageIdx,
        pageIdx !== null ? this.findController.selected.matchIdx : null,
      )

      for (const handler of this.listeners.get('updatetextlayermatches') ?? []) {
        handler({
          source: this.findController,
          pageIndex: pageIdx ?? -1,
        })
      }
    }

    const emitCount = (current: number, total: number) => {
      for (const handler of this.listeners.get('updatefindmatchescount') ?? []) {
        handler({
          source: this.findController,
          matchesCount: {
            current,
            total,
          },
        })
      }
    }

    const emitFindResponse = () => {
      applySelection({
        pageIdx: response.pageIdx,
        viewerPageNumber: response.viewerPageNumber,
      })

      if (response.delayedSelection) {
        window.setTimeout(() => {
          applySelection(response.delayedSelection ?? {})
        }, response.delayedSelection.delayMs ?? 25)
      }

      for (const handler of this.listeners.get('updatefindcontrolstate') ?? []) {
        handler({
          source: this.findController,
          state: response.state,
          matchesCount: {
            current: response.current,
            total: response.total,
          },
          rawQuery: payload.query,
        })
      }

      if (response.lateCount) {
        window.setTimeout(() => {
          emitCount(response.lateCount?.current ?? response.current, response.lateCount?.total ?? response.total)
        }, response.lateCount.delayMs ?? 25)
        return
      }

      emitCount(response.current, response.total)
    }

    if (typeof response.dispatchDelayMs === 'number' && response.dispatchDelayMs > 0) {
      window.setTimeout(emitFindResponse, response.dispatchDelayMs)
      return
    }

    emitFindResponse()
  }
}

const createMockRect = (left: number, top: number, width: number, height: number): DOMRect => {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect
}

const getSourceCodeUnitLength = (value: string, index: number): number => {
  const codePoint = value.codePointAt(index)
  return codePoint !== undefined && codePoint > 0xffff ? 2 : 1
}

const stripCombiningMarks = (value: string): string => value.replace(/\p{M}+/gu, '')

const buildFoldedTextSourceMap = (
  value: string,
): {
  text: string
  sourceIndices: number[]
} => {
  const baseSourceMap = buildNormalizedTextSourceMap(value)
  const text: string[] = []
  const sourceIndices: number[] = []

  for (let index = 0; index < baseSourceMap.text.length; index += 1) {
    const sourceIndex = baseSourceMap.sourceIndices[index]
    if (sourceIndex === undefined) {
      continue
    }

    const foldedChunk = stripCombiningMarks(baseSourceMap.text[index]?.normalize('NFD') ?? '')
    for (const character of foldedChunk) {
      text.push(character)
      sourceIndices.push(sourceIndex)
    }
  }

  return {
    text: text.join(''),
    sourceIndices,
  }
}

const inferMockQueryMatchRanges = (
  rawText: string,
  query: string,
): {
  pageMatches: number[]
  pageMatchesLength: number[]
} => {
  const normalizedQuery = normalizeEvidenceSpikeText(query)
  const sourceMap = buildNormalizedTextSourceMap(rawText)
  const normalizedPageText = sourceMap.text.toLocaleLowerCase()
  const normalizedCandidate = normalizedQuery.toLocaleLowerCase()

  if (!normalizedPageText || !normalizedCandidate) {
    return {
      pageMatches: [],
      pageMatchesLength: [],
    }
  }

  const pageMatches: number[] = []
  const pageMatchesLength: number[] = []
  let searchStart = 0

  while (searchStart <= normalizedPageText.length - normalizedCandidate.length) {
    const matchIndex = normalizedPageText.indexOf(normalizedCandidate, searchStart)
    if (matchIndex < 0) {
      break
    }

    const rawStart = sourceMap.sourceIndices[matchIndex]
    const rawEnd = sourceMap.sourceIndices[matchIndex + normalizedCandidate.length - 1]
    if (rawStart !== undefined && rawEnd !== undefined) {
      pageMatches.push(rawStart)
      pageMatchesLength.push((rawEnd + getSourceCodeUnitLength(rawText, rawEnd)) - rawStart)
    }

    searchStart = matchIndex + normalizedCandidate.length
  }

  return {
    pageMatches,
    pageMatchesLength,
  }
}

const joinMockPageRawText = (page: MockPageSpec): string => {
  if (typeof page.pageContents === 'string') {
    return page.pageContents
  }

  return page.textSegments.join('')
}

const findMockPageRange = (
  rawText: string,
  query: string,
): {
  rawStart: number
  rawEndExclusive: number
  query: string
} | null => {
  const normalizedQuery = normalizeEvidenceSpikeText(sanitizeEvidenceSearchText(query)).toLocaleLowerCase()
  if (!normalizedQuery) {
    return null
  }

  const sourceMaps = [
    buildNormalizedTextSourceMap(rawText),
    buildFoldedTextSourceMap(rawText),
  ]
  const normalizedQueries = [
    normalizedQuery,
    stripCombiningMarks(normalizedQuery.normalize('NFD')),
  ]

  for (let index = 0; index < sourceMaps.length; index += 1) {
    const sourceMap = sourceMaps[index]
    const candidateQuery = normalizedQueries[index]
    const normalizedPageText = sourceMap.text.toLocaleLowerCase()
    const normalizedMatchIndex = normalizedPageText.indexOf(candidateQuery)
    if (normalizedMatchIndex < 0) {
      continue
    }

    const rawStart = sourceMap.sourceIndices[normalizedMatchIndex]
    const rawEndIndex = sourceMap.sourceIndices[normalizedMatchIndex + candidateQuery.length - 1]
    if (rawStart === undefined || rawEndIndex === undefined) {
      continue
    }

    const rawEndExclusive = rawEndIndex + getSourceCodeUnitLength(rawText, rawEndIndex)
    return {
      rawStart,
      rawEndExclusive,
      query: rawText.slice(rawStart, rawEndExclusive),
    }
  }

  return null
}

const buildMockFuzzyMatchResponse = (
  request: PdfEvidenceFuzzyMatchRequest,
): PdfEvidenceFuzzyMatchResult => {
  const normalizedQuote = normalizeEvidenceSpikeText(
    sanitizeEvidenceSearchText(request.quote),
  ).toLocaleLowerCase()

  if (!normalizedQuote) {
    return {
      found: false,
      strategy: 'none',
      score: 0,
      matchedPage: null,
      matchedQuery: null,
      matchedRange: null,
      fullQuery: null,
      pageRanges: [],
      crossPage: false,
      note: 'No quote text was provided for fuzzy PDF evidence matching.',
    }
  }

  const hintedPages = new Set(request.pageHints ?? [])
  const rankedPages = [...request.pages].sort((left, right) => {
    const leftHint = hintedPages.has(left.pageNumber) ? 1 : 0
    const rightHint = hintedPages.has(right.pageNumber) ? 1 : 0
    return rightHint - leftHint
  })
  const candidateQueries = buildEvidenceSpikeQuoteCandidates(request.quote, {
    searchText: request.quote,
  }).map((candidate) => candidate.query)

  for (const page of rankedPages) {
    for (const candidateQuery of candidateQueries) {
      const matchedRange = findMockPageRange(page.text, candidateQuery)
      if (!matchedRange) {
        continue
      }

      const expandedQuery = findExpandedEvidenceQueryFromPageText(
        page.text,
        request.quote,
        candidateQuery,
      )?.query
      const expandedRange = expandedQuery
        ? findMockPageRange(page.text, expandedQuery)
        : null
      const effectiveRange = expandedRange ?? matchedRange
      const effectiveQuery = effectiveRange.query
      return {
        found: true,
        strategy: 'rapidfuzz-single-page',
        score: effectiveQuery.toLocaleLowerCase() === normalizedQuote ? 100 : 96,
        matchedPage: page.pageNumber,
        matchedQuery: effectiveQuery,
        matchedRange: {
          pageNumber: page.pageNumber,
          rawStart: effectiveRange.rawStart,
          rawEndExclusive: effectiveRange.rawEndExclusive,
          query: effectiveRange.query,
        },
        fullQuery: effectiveQuery,
        pageRanges: [{
          pageNumber: page.pageNumber,
          rawStart: effectiveRange.rawStart,
          rawEndExclusive: effectiveRange.rawEndExclusive,
          query: effectiveRange.query,
        }],
        crossPage: false,
        note: 'Localized quote text against PDF.js page text using RapidFuzz.',
      }
    }
  }

  for (const page of rankedPages) {
    const matchedRange = findMockPageRange(page.text, request.quote)
    if (matchedRange) {
      return {
        found: true,
        strategy: 'rapidfuzz-single-page',
        score: 100,
        matchedPage: page.pageNumber,
        matchedQuery: request.quote,
        matchedRange: {
          pageNumber: page.pageNumber,
          rawStart: matchedRange.rawStart,
          rawEndExclusive: matchedRange.rawEndExclusive,
          query: matchedRange.query,
        },
        fullQuery: request.quote,
        pageRanges: [{
          pageNumber: page.pageNumber,
          rawStart: matchedRange.rawStart,
          rawEndExclusive: matchedRange.rawEndExclusive,
          query: matchedRange.query,
        }],
        crossPage: false,
        note: 'Localized quote text against PDF.js page text using RapidFuzz.',
      }
    }
  }

  for (let pageIndex = 0; pageIndex < rankedPages.length - 1; pageIndex += 1) {
    const currentPage = rankedPages[pageIndex]
    const nextPage = rankedPages[pageIndex + 1]
    if (nextPage.pageNumber !== currentPage.pageNumber + 1) {
      continue
    }

    const stitchedText = `${currentPage.text} ${nextPage.text}`.trim()
    const stitchedRange = findMockPageRange(stitchedText, request.quote)
    if (!stitchedRange) {
      continue
    }

    const anchorLength = currentPage.text.length
    const anchorRawStart = Math.min(stitchedRange.rawStart, anchorLength)
    const anchorRawEndExclusive = Math.min(stitchedRange.rawEndExclusive, anchorLength)
    const nextRawStart = Math.max(0, stitchedRange.rawStart - (anchorLength + 1))
    const nextRawEndExclusive = Math.max(0, stitchedRange.rawEndExclusive - (anchorLength + 1))
    const pageRanges = [
      {
        pageNumber: currentPage.pageNumber,
        rawStart: anchorRawStart,
        rawEndExclusive: anchorRawEndExclusive,
        query: currentPage.text.slice(anchorRawStart, anchorRawEndExclusive),
      },
      {
        pageNumber: nextPage.pageNumber,
        rawStart: nextRawStart,
        rawEndExclusive: nextRawEndExclusive,
        query: nextPage.text.slice(nextRawStart, nextRawEndExclusive),
      },
    ].filter((range) => range.rawEndExclusive > range.rawStart)

    if (pageRanges.length === 0) {
      continue
    }

    return {
      found: true,
      strategy: 'rapidfuzz-stitched-page',
      score: 92,
      matchedPage: currentPage.pageNumber,
      matchedQuery: pageRanges[0]?.query ?? null,
      matchedRange: pageRanges[0]
        ? {
            pageNumber: pageRanges[0].pageNumber,
            rawStart: pageRanges[0].rawStart,
            rawEndExclusive: pageRanges[0].rawEndExclusive,
            query: pageRanges[0].query,
          }
        : null,
      fullQuery: stitchedRange.query,
      pageRanges,
      crossPage: pageRanges.length > 1,
      note: 'Localized quote text against stitched PDF.js page text using RapidFuzz.',
    }
  }

  return {
    found: false,
    strategy: 'rapidfuzz-single-page',
    score: 0,
    matchedPage: null,
    matchedQuery: null,
    matchedRange: null,
    fullQuery: null,
    pageRanges: [],
    crossPage: false,
    note: 'No fuzzy quote match was found in the mock PDF.js page text corpus.',
  }
}

const installMockPdfViewer = ({
  onFind,
  pages,
}: {
  onFind: (query: string) => MockFindResponse
  pages: MockPageSpec[]
}) => {
  const iframe = screen.getByTitle('PDF Viewer') as HTMLIFrameElement
  const iframeDocument = document.implementation.createHTMLDocument('pdf-viewer-iframe')
  const findController = {
    selected: {
      pageIdx: -1,
      matchIdx: -1,
    },
    pageMatches: [] as number[][],
    pageMatchesLength: [] as number[][],
    _pageContents: [] as string[],
  }

  let currentPageNumber = 1
  const pageViews = new Map<number, {
    div: HTMLElement
    viewport: { convertToViewportRectangle: (coords: number[]) => number[] }
    textLayer: {
      textDivs: Array<HTMLElement | Text>
      textContentItemsStr: string[]
    }
  }>()
  const textLayers = new Map<number, HTMLElement>()
  const textNodeLayouts = new WeakMap<Text, { rect: DOMRect; charWidth: number }>()
  const pageTextDivs = new Map<number, Array<HTMLElement | Text>>()
  const pageTextContentItems = new Map<number, string[]>()

  const appendTextSegment = (pageNumber: number, segment: string) => {
    const textLayer = textLayers.get(pageNumber)
    if (!textLayer) {
      throw new Error(`Missing text layer for page ${pageNumber}`)
    }

    const pageIndex = pageNumber - 1
    const segmentIndex = pageTextDivs.get(pageNumber)?.length ?? 0
    const span = iframeDocument.createElement('span')
    span.textContent = segment
    const spanRect = createMockRect(
      32,
      (pageIndex * 900) + 48 + (segmentIndex * 28),
      Math.max(60, segment.length * 5),
      20,
    )
    span.getBoundingClientRect = () => spanRect
    const textNode = span.firstChild as Text | null
    if (textNode) {
      textNodeLayouts.set(textNode, {
        rect: spanRect,
        charWidth: segment.length > 0 ? spanRect.width / segment.length : spanRect.width,
      })
    }
    textLayer.appendChild(span)
    const textDivs = pageTextDivs.get(pageNumber) ?? []
    textDivs.push(span)
    pageTextDivs.set(pageNumber, textDivs)
    const textItems = pageTextContentItems.get(pageNumber) ?? []
    textItems.push(segment)
    pageTextContentItems.set(pageNumber, textItems)
  }

  const clearNativeSelection = () => {
    iframeDocument.querySelectorAll('.highlight.selected').forEach((node) => node.remove())
  }

  const renderNativeSelection = (pageIdx: number | null, matchIdx: number | null) => {
    clearNativeSelection()
    if (pageIdx === null || matchIdx === null || matchIdx < 0) {
      return
    }

    const pageNumber = pageIdx + 1
    const textLayer = textLayers.get(pageNumber)
    const textDivs = pageTextDivs.get(pageNumber) ?? []
    const textItems = pageTextContentItems.get(pageNumber) ?? []
    const rawStart = findController.pageMatches[pageIdx]?.[matchIdx]
    const rawLength = findController.pageMatchesLength[pageIdx]?.[matchIdx]
    if (!textLayer || typeof rawStart !== 'number' || typeof rawLength !== 'number' || rawLength <= 0) {
      return
    }

    const rawEndExclusive = rawStart + rawLength
    let cumulativeOffset = 0

    textDivs.forEach((container, index) => {
      const itemText = textItems[index] ?? ''
      const itemStart = cumulativeOffset
      const itemEndExclusive = itemStart + itemText.length
      cumulativeOffset = itemEndExclusive
      if (itemText.length === 0 || rawStart >= itemEndExclusive || rawEndExclusive <= itemStart) {
        return
      }

      const localStart = Math.max(0, rawStart - itemStart)
      const localEndExclusive = Math.min(itemText.length, rawEndExclusive - itemStart)
      const textNode = container.firstChild as Text | null
      const layout = textNode ? textNodeLayouts.get(textNode) : null
      if (!layout || localStart >= localEndExclusive) {
        return
      }

      const highlight = iframeDocument.createElement('span')
      highlight.className = 'highlight selected'
      highlight.textContent = itemText.slice(localStart, localEndExclusive)
      highlight.getBoundingClientRect = () => createMockRect(
        layout.rect.left + (localStart * layout.charWidth),
        layout.rect.top,
        (localEndExclusive - localStart) * layout.charWidth,
        layout.rect.height,
      )
      textLayer.appendChild(highlight)
    })
  }

  pages.forEach((page, pageIndex) => {
    pageTextDivs.set(page.pageNumber, [])
    pageTextContentItems.set(page.pageNumber, [])
    const pageTop = pageIndex * 900
    const pageDiv = iframeDocument.createElement('div')
    pageDiv.className = 'page'
    pageDiv.dataset.pageNumber = String(page.pageNumber)
    pageDiv.getBoundingClientRect = () => createMockRect(0, pageTop, 640, 840)

    const textLayer = iframeDocument.createElement('div')
    textLayer.className = 'textLayer'
    textLayers.set(page.pageNumber, textLayer)

    page.textSegments.forEach((segment) => {
      appendTextSegment(page.pageNumber, segment)
    })

    pageDiv.appendChild(textLayer)
    iframeDocument.body.appendChild(pageDiv)

    pageViews.set(page.pageNumber, {
      div: pageDiv,
      viewport: {
        convertToViewportRectangle: (coords: number[]) => coords,
      },
      textLayer: {
        textDivs: pageTextDivs.get(page.pageNumber) ?? [],
        textContentItemsStr: pageTextContentItems.get(page.pageNumber) ?? [],
      },
    })
  })

  const highestPageNumber = pages.reduce((max, page) => Math.max(max, page.pageNumber), 0)
  findController._pageContents = Array.from(
    { length: highestPageNumber },
    (_, pageIndex) => {
      const page = pages.find((candidate) => candidate.pageNumber === pageIndex + 1)
      return page ? joinMockPageRawText(page) : ''
    },
  )

  iframeDocument.createRange = (() => {
    let startNode: Text | null = null
    let endNode: Text | null = null
    let startOffset = 0
    let endOffset = 0

    const buildRangeRect = (): DOMRect | null => {
      if (!startNode || !endNode || startNode !== endNode || endOffset <= startOffset) {
        return null
      }

      const layout = textNodeLayouts.get(startNode)
      if (!layout) {
        return null
      }

      return createMockRect(
        layout.rect.left + (startOffset * layout.charWidth),
        layout.rect.top,
        (endOffset - startOffset) * layout.charWidth,
        layout.rect.height,
      )
    }

    return () => ({
      setStart(node: Node, offset: number) {
        startNode = node as Text
        startOffset = offset
      },
      setEnd(node: Node, offset: number) {
        endNode = node as Text
        endOffset = offset
      },
      getClientRects() {
        const rect = buildRangeRect()
        return rect ? [rect] : []
      },
      getBoundingClientRect() {
        return buildRangeRect() ?? createMockRect(0, 0, 0, 0)
      },
    } as unknown as Range)
  })()

  const pdfDocument = {
    numPages: highestPageNumber,
    async getPage(pageNumber: number) {
      const page = pages.find((candidate) => candidate.pageNumber === pageNumber)
      if (!page) {
        throw new Error(`Missing mock PDF page ${pageNumber}`)
      }

      return {
        async getTextContent() {
          return {
            items: page.textSegments.map((segment) => ({
              str: segment,
              hasEOL: false,
            })),
          }
        },
      }
    },
  }

  const pdfViewer = {
    get currentPageNumber() {
      return currentPageNumber
    },
    set currentPageNumber(value: number) {
      currentPageNumber = value
    },
    currentScaleValue: 'auto',
    pdfDocument,
    getPageView(pageIndex: number) {
      return pageViews.get(pageIndex + 1)
    },
  }

  const eventBus = new MockPdfEventBus(
    onFind,
    () => currentPageNumber,
    (nextPage) => {
      currentPageNumber = nextPage
    },
    findController,
    (pageIdx) => findController._pageContents[pageIdx] ?? '',
    renderNativeSelection,
    clearNativeSelection,
  )

  const pdfApp = {
    eventBus,
    findController,
    pdfViewer,
    pdfDocument,
    appConfig: {
      viewerContainer: iframeDocument.createElement('div'),
    },
  }

  Object.defineProperty(iframe, 'contentWindow', {
    configurable: true,
    value: {
      document: iframeDocument,
      Mark: class {
        unmark(): void {}
        mark(): void {}
      },
      PDFViewerApplication: pdfApp,
    },
  })

  return {
    iframe,
    eventBus,
    pdfViewer,
    appendTextSegment,
    findController,
    rerenderNativeSelection: (pageIdx: number | null, matchIdx: number | null) => {
      renderNativeSelection(pageIdx, matchIdx)
    },
  }
}

const getEvidenceHighlightRects = (iframe: HTMLIFrameElement): HTMLElement[] => {
  return Array.from(
    iframe.contentWindow?.document.querySelectorAll<HTMLElement>('.pdf-evidence-highlight-rect') ?? [],
  )
}

const getNativeSelectedHighlights = (iframe: HTMLIFrameElement): HTMLElement[] => {
  return Array.from(
    iframe.contentWindow?.document.querySelectorAll<HTMLElement>('.highlight.selected') ?? [],
  )
}

const buildNavigationCommand = (
  overrides: Partial<EvidenceNavigationCommand> = {},
): EvidenceNavigationCommand => ({
  anchorId: 'anchor-1',
  anchor: {
    anchor_kind: 'snippet',
    locator_quality: 'exact_quote',
    supports_decision: 'supports',
    snippet_text: 'Exact quote from PDFX markdown',
    normalized_text: 'Exact quote from PDFX markdown',
    viewer_search_text: 'Exact quote from PDFX markdown',
    page_number: 3,
    section_title: 'Results',
    subsection_title: 'Quantification',
    chunk_ids: ['chunk-1'],
    ...overrides.anchor,
  },
  searchText: 'Exact quote from PDFX markdown',
  pageNumber: 3,
  sectionTitle: 'Results',
  mode: 'select',
  ...overrides,
})

const createSinglePageFuzzyMatchResult = (
  pageNumber: number,
  rawText: string,
  matchText: string,
  options?: {
    strategy?: PdfEvidenceFuzzyMatchResult['strategy']
    score?: number
    note?: string
    fullQuery?: string | null
    crossPage?: boolean
  },
): PdfEvidenceFuzzyMatchResult => {
  const range = findMockPageRange(rawText, matchText)
  if (!range) {
    throw new Error(`Unable to localize "${matchText}" in mock page ${pageNumber}`)
  }

  return {
    found: true,
    strategy: options?.strategy ?? 'rapidfuzz-single-page',
    score: options?.score ?? 96,
    matchedPage: pageNumber,
    matchedQuery: range.query,
    matchedRange: {
      pageNumber,
      rawStart: range.rawStart,
      rawEndExclusive: range.rawEndExclusive,
      query: range.query,
    },
    fullQuery: options?.fullQuery ?? range.query,
    pageRanges: [{
      pageNumber,
      rawStart: range.rawStart,
      rawEndExclusive: range.rawEndExclusive,
      query: range.query,
    }],
    crossPage: options?.crossPage ?? false,
    note: options?.note ?? 'Localized quote text against PDF.js page text using RapidFuzz.',
  }
}

const createCrossPageFuzzyMatchResult = (
  anchorPageNumber: number,
  anchorText: string,
  nextPageNumber: number,
  nextPageText: string,
  fullMatchText: string,
): PdfEvidenceFuzzyMatchResult => {
  const anchorRange = findMockPageRange(anchorText, anchorText)
  const nextRange = findMockPageRange(nextPageText, nextPageText)
  if (!anchorRange || !nextRange) {
    throw new Error('Unable to build mock cross-page fuzzy match result')
  }

  return {
    found: true,
    strategy: 'rapidfuzz-stitched-page',
    score: 92,
    matchedPage: anchorPageNumber,
    matchedQuery: anchorRange.query,
    matchedRange: {
      pageNumber: anchorPageNumber,
      rawStart: anchorRange.rawStart,
      rawEndExclusive: anchorRange.rawEndExclusive,
      query: anchorRange.query,
    },
    fullQuery: fullMatchText,
    pageRanges: [
      {
        pageNumber: anchorPageNumber,
        rawStart: anchorRange.rawStart,
        rawEndExclusive: anchorRange.rawEndExclusive,
        query: anchorRange.query,
      },
      {
        pageNumber: nextPageNumber,
        rawStart: nextRange.rawStart,
        rawEndExclusive: nextRange.rawEndExclusive,
        query: nextRange.query,
      },
    ],
    crossPage: true,
    note: 'Localized quote text against stitched PDF.js page text using RapidFuzz.',
  }
}

const buildDefaultPages = (): MockPageSpec[] => [
  { pageNumber: 1, textSegments: ['Introduction'] },
  { pageNumber: 2, textSegments: ['Already normalized quote text'] },
  { pageNumber: 3, textSegments: ['Exact quote from PDFX markdown', 'Results'] },
  { pageNumber: 4, textSegments: ['Results'] },
  { pageNumber: 5, textSegments: ['Raw quote with “smart” punctuation'] },
  { pageNumber: 6, textSegments: ['Discussion'] },
]

describe('PdfViewer evidence navigation', () => {
  beforeEach(() => {
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockImplementation(async (request) => buildMockFuzzyMatchResponse(request))
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.useRealTimers()
    vi.mocked(global.fetch).mockReset()
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockReset()
    delete window.__pdfViewerEvidenceSpike
    delete window.__pdfViewerEvidenceSpikeLastResult
  })

  it('builds quote and section candidates with deterministic fallbacks', () => {
    const reasons = buildEvidenceSpikeQuoteCandidates(
      '“Quoted” text with enough words to trigger fragment generation because the production chain needs a shorter excerpt for page break fallback behavior,\n' +
        'and it keeps going with several extra words for a later fragment match after the page break simulation. A second sentence makes the sentence fallback distinct.',
    ).map((candidate) => candidate.reason)

    expect(reasons).toEqual(expect.arrayContaining([
      'exact-quote',
      'normalized-quote',
      'window-fragment',
    ]))
    expect(reasons).not.toEqual(expect.arrayContaining([
      'first-sentence-fragment',
      'leading-fragment',
      'trailing-fragment',
    ]))

    expect(
      buildEvidenceSpikeSectionCandidates('Results', 'Quantification').map((candidate) => candidate.query),
    ).toEqual(['Quantification', 'Results'])

    expect(normalizeEvidenceSpikePageHints({ pageNumbers: [4, 4, 0, 9], pageNumber: 2 })).toEqual([4, 9, 2])
  })

  it('prefers a sanitized quote candidate before raw markdown-formatted evidence text', () => {
    const query = 'all proteins changed in the allele lacking the *crb_C* isoform constitute interesting candidates.'
    const candidates = buildEvidenceSpikeQuoteCandidates(query)

    expect(candidates[0]).toEqual({
      query,
      reason: 'exact-quote',
    })
    expect(candidates[1]).toEqual({
      query: 'all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates.',
      reason: 'sanitized-quote',
    })
  })

  it('expands a matched fragment back to the longest contiguous quote available on the page', () => {
    const pageText = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton.',
    ].join(' ')
    const desiredQuote = [
      'In summary, all proteins changed in the allele lacking the *crb_C* isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton and should be prioritized for follow-up experiments.',
    ].join(' ')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(desiredQuote)
      .find((candidate) => candidate.reason === 'window-fragment' && pageText.includes(candidate.query))
      ?.query

    expect(fragmentCandidate).toBeTruthy()
    expect(
      findExpandedEvidenceQueryFromPageText(pageText, desiredQuote, fragmentCandidate ?? ''),
    ).toEqual(expect.objectContaining({
      query: 'all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton',
      startWordIndex: 2,
    }))
  })

  it('uses the dev harness to localize a normalized quote with native PDF.js highlighting', async () => {
    vi.stubEnv('VITE_DEV_MODE', 'true')
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const pages = buildDefaultPages()
    const pageFiveText = joinMockPageRawText(pages[4]!)
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createSinglePageFuzzyMatchResult(5, pageFiveText, pageFiveText),
    )

    render(<PdfViewer />)

    dispatchPDFDocumentChanged('doc-1', '/fixtures/sample.pdf', 'sample.pdf', 12)
    await waitFor(() => {
      expect(screen.getByText('sample.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => {
        if (query === 'Raw quote with “smart” punctuation') {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 4,
            pageMatches: [0],
            pageMatchesLength: [pageFiveText.length],
          }
        }

        return {
          state: 1,
          total: 0,
          current: 0,
          pageIdx: null,
        }
      },
      pages,
    })

    fireEvent.load(iframe)
    await waitFor(() => {
      expect(window.__pdfViewerEvidenceSpike).toBeTypeOf('function')
    })

    const result = await window.__pdfViewerEvidenceSpike?.({
      quote: 'Raw   quote\nwith “smart” punctuation',
      pageNumber: 4,
      sectionTitle: 'Results',
    } satisfies PdfEvidenceSpikeInput)

    expect(result).toMatchObject({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      matchedQuery: 'Raw quote with “smart” punctuation',
      matchedPage: 5,
      matchesTotal: 1,
      currentMatch: 1,
    })
    expect(fuzzyMatchPdfEvidenceQuote).toHaveBeenCalledWith(expect.objectContaining({
      quote: 'Raw   quote\nwith “smart” punctuation',
      pageHints: [4],
      pages: expect.arrayContaining([
        expect.objectContaining({
          pageNumber: 5,
          text: 'Raw quote with “smart” punctuation',
        }),
      ]),
    }))
    expect(eventBus.findQueries).toEqual(['Raw quote with “smart” punctuation'])
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)[0].getAttribute('data-kind')).toBe('quote')
    })
  })

  it('keeps the canonical PDF.js page substring when a normalized quote lands natively', async () => {
    vi.stubEnv('VITE_DEV_MODE', 'true')
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'naive approach among proteins'
    const pageText = 'naïve approach among proteins'
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createSinglePageFuzzyMatchResult(3, pageText, pageText, {
        score: 94,
      }),
    )

    render(<PdfViewer />)

    dispatchPDFDocumentChanged('doc-diacritics', '/fixtures/sample.pdf', 'diacritics.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('diacritics.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (candidateQuery) => ({
        state: candidateQuery === pageText ? 0 : 1,
        total: candidateQuery === pageText ? 1 : 0,
        current: candidateQuery === pageText ? 1 : 0,
        pageIdx: candidateQuery === pageText ? 2 : null,
        pageMatches: candidateQuery === pageText ? [0] : [],
        pageMatchesLength: candidateQuery === pageText ? [pageText.length] : [],
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [pageText] },
        { pageNumber: 4, textSegments: ['Results'] },
      ],
    })

    fireEvent.load(iframe)
    await waitFor(() => {
      expect(window.__pdfViewerEvidenceSpike).toBeTypeOf('function')
    })

    const result = await window.__pdfViewerEvidenceSpike?.({
      quote: query,
      pageNumber: 3,
      sectionTitle: 'Results',
    } satisfies PdfEvidenceSpikeInput)

    expect(result).toMatchObject({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      matchedQuery: pageText,
      matchedPage: 3,
      matchesTotal: 1,
      currentMatch: 1,
    })
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
  })

  it('acknowledges select navigation and keeps quote highlighting native-only on the matched page', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand()

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-2', '/fixtures/sample.pdf', 'typed.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('typed.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => {
        if (query === 'Exact quote from PDFX markdown') {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 2,
          }
        }

        return {
          state: 1,
          total: 0,
          current: 0,
          pageIdx: null,
        }
      },
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual(['Exact quote from PDFX markdown'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 3,
    }))
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe)[0].getAttribute('data-mode')).toBe('select')
    expect(screen.getByText('Exact quote')).toBeInTheDocument()
  })

  it('accepts chat-dispatched evidence navigation without rendering custom overlay boxes', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-chat', '/fixtures/sample.pdf', 'chat-evidence.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('chat-evidence.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Exact quote from PDFX markdown' ? 0 : 1,
        total: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        current: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        pageIdx: query === 'Exact quote from PDFX markdown' ? 2 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)
    window.setTimeout(() => {
      const iframeDocument = iframe.contentWindow?.document
      const textLayer = iframeDocument?.querySelector<HTMLElement>(
        '.page[data-page-number="3"] .textLayer',
      )
      if (!iframeDocument || !textLayer) {
        return
      }

      const nativeHighlight = iframeDocument.createElement('span')
      nativeHighlight.className = 'highlight selected'
      nativeHighlight.textContent = 'Exact quote from PDFX markdown'
      nativeHighlight.getBoundingClientRect = () => createMockRect(48, (2 * 900) + 72, 190, 20)
      textLayer.appendChild(nativeHighlight)
      eventBus.dispatch('updatetextlayermatches', { pageIndex: 2 })
    }, 150)

    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({ anchorId: 'chat-anchor-1' }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual(['Exact quote from PDFX markdown'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 3,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    expect(eventBus.findbarCloseCount).toBe(1)
  })

  it('reports document-global match counts when reusing an existing native PDF.js highlight', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const query = 'Exact quote from PDFX markdown'

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-chat-global-counts', '/fixtures/sample.pdf', 'chat-global-counts.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('chat-global-counts.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus, findController } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 3 : 0,
        current: candidate === query ? 3 : 0,
        pageIdx: candidate === query ? 2 : null,
        pageMatches: candidate === query ? [0] : [],
        pageMatchesLength: candidate === query ? [query.length] : [],
      }),
      pages: buildDefaultPages(),
    })

    findController.pageMatches[1] = [0, 32]
    findController.pageMatchesLength[1] = [query.length, query.length]

    fireEvent.load(iframe)
    window.setTimeout(() => {
      const iframeDocument = iframe.contentWindow?.document
      const textLayer = iframeDocument?.querySelector<HTMLElement>(
        '.page[data-page-number="3"] .textLayer',
      )
      if (!iframeDocument || !textLayer) {
        return
      }

      const nativeHighlight = iframeDocument.createElement('span')
      nativeHighlight.className = 'highlight selected'
      nativeHighlight.textContent = query
      nativeHighlight.getBoundingClientRect = () => createMockRect(48, (2 * 900) + 72, 190, 20)
      textLayer.appendChild(nativeHighlight)
      eventBus.dispatch('updatetextlayermatches', { pageIndex: 2 })
    }, 150)

    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({ anchorId: 'chat-global-counts-anchor' }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      matchedPage: 3,
      matchedQuery: query,
      matchesTotal: 3,
      currentMatch: 3,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
  })

  it('tries section context after chat quote localization resolves only to page context', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce({
      found: true,
      strategy: 'rapidfuzz-single-page',
      score: 88,
      matchedPage: 3,
      matchedQuery: 'Exact quote from PDFX markdown',
      matchedRange: {
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: 'Exact quote from PDFX markdown'.length,
        query: 'Exact quote from PDFX markdown',
      },
      fullQuery: 'Exact quote from PDFX markdown',
      pageRanges: [{
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: 'Exact quote from PDFX markdown'.length,
        query: 'Exact quote from PDFX markdown',
      }],
      crossPage: false,
      note: 'Localized quote text against PDF.js page text using RapidFuzz.',
    })

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-chat-page-context', '/fixtures/sample.pdf', 'chat-page-context.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('chat-page-context.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => {
        if (query === 'Results') {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 3,
          }
        }

        return {
          state: 1,
          total: 0,
          current: 0,
          pageIdx: null,
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text'] },
        { pageNumber: 4, textSegments: ['Results'] },
      ],
    })

    fireEvent.load(iframe)
    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({ anchorId: 'chat-anchor-page-context' }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 3000 })

    expect(eventBus.findQueries).toEqual([
      'Exact quote from PDFX markdown',
      'Quantification',
      'Results',
    ])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'section-fallback',
      locatorQuality: 'section_only',
      degraded: true,
      matchedQuery: 'Results',
      matchedPage: 4,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(screen.getByText('Section fallback')).toBeInTheDocument()
  })

  it('waits for a delayed visible native PDF.js quote match during chat navigation', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const query = 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createSinglePageFuzzyMatchResult(6, query, query),
    )

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-chat-native', '/fixtures/sample.pdf', 'chat-native.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('chat-native.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: candidate === query ? 5 : null,
        pageMatches: candidate === query ? [0] : [],
        pageMatchesLength: candidate === query ? [query.length] : [],
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text', 'Results'] },
        { pageNumber: 4, textSegments: ['More text'] },
        { pageNumber: 5, textSegments: ['Discussion'] },
        { pageNumber: 6, textSegments: [], pageContents: query },
      ],
    })

    fireEvent.load(iframe)
    window.setTimeout(() => {
      const iframeDocument = iframe.contentWindow?.document
      const textLayer = iframeDocument?.querySelector<HTMLElement>(
        '.page[data-page-number="6"] .textLayer',
      )
      if (!iframeDocument || !textLayer) {
        return
      }

      const nativeHighlight = iframeDocument.createElement('span')
      nativeHighlight.className = 'highlight selected'
      nativeHighlight.textContent = query
      nativeHighlight.getBoundingClientRect = () => createMockRect(48, (5 * 900) + 72, 190, 20)
      textLayer.appendChild(nativeHighlight)
      eventBus.dispatch('updatetextlayermatches', { pageIndex: 5 })
    }, 350)

    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({
        anchorId: 'chat-anchor-native',
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'exact_quote',
          supports_decision: 'supports',
          snippet_text: query,
          normalized_text: query,
          viewer_search_text: query,
          page_number: 1,
          section_title: 'Results and Discussion',
          subsection_title: query,
          chunk_ids: ['chunk-chat-native'],
        },
        searchText: query,
        pageNumber: 1,
        sectionTitle: 'Results and Discussion',
      }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 1200 })

    expect(eventBus.findQueries.every((entry) => entry === query)).toBe(true)
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 6,
      matchedQuery: query,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    expect(eventBus.findbarCloseCount).toBe(1)
    expect(
      screen.queryByText('Evidence on this page. Quote text was not matched reliably enough to highlight.'),
    ).not.toBeInTheDocument()
    expect(screen.getByText('Page 6')).toBeInTheDocument()
  })

  it('keeps the native fragment highlighted when a longer PDF.js upgrade cannot be verified', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const pageText = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton.',
    ].join(' ')
    const query = [
      'In summary, all proteins changed in the allele lacking the *crb_C* isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton and should be prioritized for follow-up experiments.',
    ].join(' ')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason === 'window-fragment' && pageText.includes(candidate.query))
      ?.query

    expect(fragmentCandidate).toBeTruthy()
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createSinglePageFuzzyMatchResult(3, pageText, fragmentCandidate ?? '', {
        score: 84,
      }),
    )

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-fragment-preserved', '/fixtures/sample.pdf', 'fragment-preserved.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('fragment-preserved.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => {
        const isFragment = candidate === fragmentCandidate
        return {
          state: isFragment ? 0 : 1,
          total: isFragment ? 1 : 0,
          current: isFragment ? 1 : 0,
          pageIdx: isFragment ? 2 : null,
          pageMatches: isFragment && fragmentCandidate ? [pageText.indexOf(fragmentCandidate)] : [],
          pageMatchesLength: isFragment && fragmentCandidate ? [fragmentCandidate.length] : [],
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [pageText, 'Results and Discussion'] },
      ],
    })

    fireEvent.load(iframe)

    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({
        anchorId: 'chat-fragment-preserved-anchor',
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'exact_quote',
          supports_decision: 'supports',
          snippet_text: query,
          normalized_text: query,
          viewer_search_text: query,
          page_number: 1,
          section_title: 'Results and Discussion',
          subsection_title: null,
          chunk_ids: ['chunk-chat-fragment-preserved'],
        },
        searchText: query,
        pageNumber: 1,
        sectionTitle: 'Results and Discussion',
      }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 4000 })

    expect(eventBus.findQueries).toContain(fragmentCandidate)
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedPage: 3,
      matchedQuery: fragmentCandidate,
      note: 'Localized the quote with RapidFuzz and highlighted the verified native PDF.js span.',
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
  })

  it('does not render quote overlay boxes when quote navigation degrades to page context', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const pageText = 'naïve approach among proteins'
    const query = 'naive approach among proteins'
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createSinglePageFuzzyMatchResult(3, `${pageText}Results`, pageText, {
        score: 94,
      }),
    )

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-native-no-overlay', '/fixtures/sample.pdf', 'native-no-overlay.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('native-no-overlay.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: () => ({
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [pageText, 'Results'] },
      ],
    })

    fireEvent.load(iframe)
    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({
        anchorId: 'chat-native-no-overlay-anchor',
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'normalized_quote',
          supports_decision: 'supports',
          snippet_text: query,
          normalized_text: query,
          viewer_search_text: query,
          page_number: 3,
          section_title: 'Results',
          subsection_title: null,
          chunk_ids: ['chunk-chat-native-no-overlay'],
        },
        searchText: query,
        pageNumber: 3,
        sectionTitle: 'Results',
      }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 4000 })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'page-fallback',
      strategy: 'page-hint',
      locatorQuality: 'page_only',
      matchedPage: 3,
    }))
    expect(vi.mocked(fuzzyMatchPdfEvidenceQuote)).toHaveBeenCalledWith(expect.objectContaining({
      quote: query,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe)).toHaveLength(0)
  })

  it('dispatches the selected anchor id when a native quote highlight is clicked', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onAnchorSelected = vi.fn()
    const unsubscribe = onPDFViewerEvidenceAnchorSelected(onAnchorSelected)

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ anchorId: 'anchor-click' })} />)

    dispatchPDFDocumentChanged('doc-click', '/fixtures/sample.pdf', 'clickable.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('clickable.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Exact quote from PDFX markdown' ? 0 : 1,
        total: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        current: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        pageIdx: query === 'Exact quote from PDFX markdown' ? 2 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    const nativeHighlight = await waitFor(() => {
      const [highlight] = getNativeSelectedHighlights(iframe)
      expect(highlight).toBeDefined()
      return highlight
    })
    await waitFor(() => {
      expect(nativeHighlight.getAttribute('data-kind')).toBe('quote')
    })

    nativeHighlight.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    expect(onAnchorSelected).toHaveBeenCalledTimes(1)
    expect(onAnchorSelected.mock.calls[0][0].detail.anchorId).toBe('anchor-click')

    unsubscribe()
  })

  it('keeps one keyboard focus target for a native quote and reattaches it after PDF.js rerenders the match', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onAnchorSelected = vi.fn()
    const unsubscribe = onPDFViewerEvidenceAnchorSelected(onAnchorSelected)
    const query = 'Exact quote from PDFX markdown'
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createSinglePageFuzzyMatchResult(3, `${query} Results`, query),
    )

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ anchorId: 'anchor-rerender' })} />)

    dispatchPDFDocumentChanged('doc-rerender', '/fixtures/sample.pdf', 'rerender.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('rerender.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus, rerenderNativeSelection } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: candidate === query ? 2 : null,
        pageMatches: candidate === query ? [0] : [],
        pageMatchesLength: candidate === query ? [query.length] : [],
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: ['Exact quote from PDFX ', 'markdown', ' Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(2)
      expect(getNativeSelectedHighlights(iframe)[0].getAttribute('data-kind')).toBe('quote')
    })

    let nativeHighlights = getNativeSelectedHighlights(iframe)
    expect(nativeHighlights.filter((node) => node.getAttribute('tabindex') === '0')).toHaveLength(1)
    expect(nativeHighlights.filter((node) => node.getAttribute('role') === 'button')).toHaveLength(1)
    expect(nativeHighlights[1].hasAttribute('tabindex')).toBe(false)

    nativeHighlights[1].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(onAnchorSelected).toHaveBeenCalledTimes(1)
    expect(onAnchorSelected.mock.calls[0][0].detail.anchorId).toBe('anchor-rerender')

    rerenderNativeSelection(2, 0)
    eventBus.dispatch('updatetextlayermatches', { pageIndex: 2 })

    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(2)
      expect(getNativeSelectedHighlights(iframe)[0].getAttribute('data-kind')).toBe('quote')
    })

    nativeHighlights = getNativeSelectedHighlights(iframe)
    expect(nativeHighlights.filter((node) => node.getAttribute('tabindex') === '0')).toHaveLength(1)
    nativeHighlights[1].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    nativeHighlights[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    expect(onAnchorSelected).toHaveBeenCalledTimes(3)

    unsubscribe()
  })

  it('derives highlight rects from the matched substring instead of the whole text span', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'Exact quote from PDFX markdown'
    const prefix = 'Prefix '
    const suffix = ' suffix'

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ searchText: query })} />)

    dispatchPDFDocumentChanged('doc-2b', '/fixtures/sample.pdf', 'substring.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('substring.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: candidate === query ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [`${prefix}${query}${suffix}`, 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })

    const highlightRect = getNativeSelectedHighlights(iframe)[0].getBoundingClientRect()
    expect(highlightRect.left).toBe(32 + (prefix.length * 5))
    expect(highlightRect.width).toBe(query.length * 5)
  })

  it('uses the selected repeated same-page match index for text-layer highlighting', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'Exact quote from PDFX markdown'
    const pageText = ['Header', query, 'gap', query, 'Results'].join('')
    const firstMatchIndex = pageText.indexOf(query)
    const secondMatchIndex = pageText.indexOf(query, firstMatchIndex + 1)
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce({
      found: true,
      strategy: 'rapidfuzz-single-page',
      score: 100,
      matchedPage: 3,
      matchedQuery: query,
      matchedRange: {
        pageNumber: 3,
        rawStart: secondMatchIndex,
        rawEndExclusive: secondMatchIndex + query.length,
        query,
      },
      fullQuery: query,
      pageRanges: [{
        pageNumber: 3,
        rawStart: secondMatchIndex,
        rawEndExclusive: secondMatchIndex + query.length,
        query,
      }],
      crossPage: false,
      note: 'Localized quote text against PDF.js page text using RapidFuzz.',
    })

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ searchText: query })} />)

    dispatchPDFDocumentChanged('doc-2c', '/fixtures/sample.pdf', 'repeated.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('repeated.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 2 : 0,
        current: candidate === query ? 2 : 0,
        pageIdx: candidate === query ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Header', query, 'gap', query, 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })

    expect(getNativeSelectedHighlights(iframe)[0].getBoundingClientRect().top).toBe((2 * 900) + 48 + (3 * 28))
  })

  it('waits for delayed text-layer rendering before degrading a successful quote match', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'Exact quote from PDFX markdown'
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createSinglePageFuzzyMatchResult(3, query, query),
    )

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ searchText: query })} />)

    dispatchPDFDocumentChanged('doc-2d', '/fixtures/sample.pdf', 'delayed-text-layer.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('delayed-text-layer.pdf')).toBeInTheDocument()
    })

    const { iframe, appendTextSegment, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: candidate === query ? 2 : null,
        pageMatches: candidate === query ? [0] : [],
        pageMatchesLength: candidate === query ? [query.length] : [],
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [], pageContents: query },
      ],
    })

    fireEvent.load(iframe)

    window.setTimeout(() => {
      appendTextSegment(3, query)
      const iframeDocument = iframe.contentWindow?.document
      const textLayer = iframeDocument?.querySelector<HTMLElement>(
        '.page[data-page-number="3"] .textLayer',
      )
      if (!iframeDocument || !textLayer) {
        return
      }

      const nativeHighlight = iframeDocument.createElement('span')
      nativeHighlight.className = 'highlight selected'
      nativeHighlight.textContent = query
      nativeHighlight.getBoundingClientRect = () => createMockRect(48, (2 * 900) + 72, 190, 20)
      textLayer.appendChild(nativeHighlight)
      eventBus.dispatch('updatetextlayermatches', { pageIndex: 2 })
    }, 150)

    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })

    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)[0].getAttribute('data-kind')).toBe('quote')
    })
  })

  it('degrades to page context when a quote resolves to the page but not to a visible native highlight', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'crumbs ( crb ) mutant eyes'
    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 3,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-native-match'],
      },
      searchText: query,
      pageNumber: 3,
      sectionTitle: 'Results',
    })
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce({
      found: true,
      strategy: 'rapidfuzz-single-page',
      score: 82,
      matchedPage: 3,
      matchedQuery: query,
      matchedRange: {
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: query.length,
        query,
      },
      fullQuery: query,
      pageRanges: [{
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: query.length,
        query,
      }],
      crossPage: false,
      note: 'Localized quote text against PDF.js page text using RapidFuzz.',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-native-match', '/fixtures/sample.pdf', 'native-match.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('native-match.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: () => ({
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text'] },
      ],
    })

    fireEvent.load(iframe)

    window.setTimeout(() => {
      const iframeDocument = iframe.contentWindow?.document
      const textLayer = iframeDocument?.querySelector<HTMLElement>(
        '.page[data-page-number="3"] .textLayer',
      )
      if (!iframeDocument || !textLayer) {
        return
      }

      const nativeHighlight = iframeDocument.createElement('span')
      nativeHighlight.className = 'highlight selected'
      nativeHighlight.textContent = query
      nativeHighlight.getBoundingClientRect = () => createMockRect(48, (2 * 900) + 72, 190, 20)
      textLayer.appendChild(nativeHighlight)
      eventBus.dispatch('updatetextlayermatches', { pageIndex: 2 })
    }, 150)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual([query, 'Results'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'page-fallback',
      locatorQuality: 'page_only',
      degraded: true,
      matchedPage: 3,
      matchedQuery: query,
    }))
    expect(getNativeSelectedHighlights(iframe)).toHaveLength(0)
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
  })

  it('matches a contiguous quote fragment before degrading to page context', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const query = [
      'Nevertheless, all proteins changed in the allele lacking',
      'the crb_C isoform constitute interesting candidates in the con-',
      'nection of the Crumbs function in organizing the cytoskeleton.',
    ].join('\n')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason === 'window-fragment')
      ?.query

    expect(fragmentCandidate).toBeTruthy()

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 3,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-window-fragment'],
      },
      searchText: query,
      pageNumber: 3,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-window-fragment', '/fixtures/sample.pdf', 'window-fragment.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('window-fragment.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === fragmentCandidate ? 0 : 1,
        total: candidate === fragmentCandidate ? 1 : 0,
        current: candidate === fragmentCandidate ? 1 : 0,
        pageIdx: candidate === fragmentCandidate ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [fragmentCandidate ?? '', 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toContain(fragmentCandidate)
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedPage: 3,
      matchedQuery: fragmentCandidate,
      note: 'Localized the quote with RapidFuzz and highlighted the verified native PDF.js span.',
    }))
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(screen.getByText('Approximate quote')).toBeInTheDocument()
  })

  it('recovers a longer quote span after a fragment match on the resolved page', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const pageText = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton.',
    ].join(' ')
    const query = [
      'In summary, all proteins changed in the allele lacking the *crb_C* isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton and should be prioritized for follow-up experiments.',
    ].join(' ')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason === 'window-fragment' && pageText.includes(candidate.query))
      ?.query
    const expandedQuery = findExpandedEvidenceQueryFromPageText(pageText, query, fragmentCandidate ?? '')?.query

    expect(fragmentCandidate).toBeTruthy()
    expect(expandedQuery).toBe(
      'all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton',
    )

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 1,
        section_title: 'Results and Discussion',
        subsection_title: null,
        chunk_ids: ['chunk-expanded-fragment'],
      },
      searchText: query,
      pageNumber: 1,
      sectionTitle: 'Results and Discussion',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-expanded-fragment', '/fixtures/sample.pdf', 'expanded-fragment.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('expanded-fragment.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => {
        const inferredRanges = inferMockQueryMatchRanges(pageText, candidate)
        const matched = candidate === fragmentCandidate || inferredRanges.pageMatches.length > 0
        return {
          state: matched ? 0 : 1,
          total: matched ? Math.max(inferredRanges.pageMatches.length, 1) : 0,
          current: matched ? 1 : 0,
          pageIdx: matched ? 2 : null,
          pageMatches: matched ? inferredRanges.pageMatches : [],
          pageMatchesLength: matched ? inferredRanges.pageMatchesLength : [],
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [pageText, 'Results and Discussion'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual([expandedQuery])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedPage: 3,
      matchedQuery: expect.stringContaining('proteins changed in the allele lacking the crb_C isoform'),
      note: 'Localized the quote with RapidFuzz and highlighted the verified native PDF.js span.',
    }))
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
  })

  it('recovers the best matching PDF quote span when the stored quote drifts from the page text', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const pageText = [
      'Actin 5C at 344 +/- 23 fmoles/eye is the most abundant among all actins,',
      'followed by Actin 87E (80 +/- 51 fmoles/eye) and Actin 57B (81 +/- 19 fmoles/eye).',
      'Higher abundance of Actin 5C in comparison to Actin 87E and Actin 57B corroborates',
      'genetic evidence indicating that amongst the six actin genes in the Drosophila genome,',
      'actin 5C is critical for photoreceptor',
    ].join(' ')
    const query = [
      'Actin 5C at 344 ± 23 fmoles/eye is the most abundant among all actins,',
      'followed by Actin 87E (80 ± 51 fmoles/eye) and Actin 57B (81 ± 19 fmoles/eye).',
      'Higher abundance of Actin 5C in comparison to Actin 87E and Actin 57B corroborates',
      'genetic evidence indicating that amongst the six *actin* genes in the *Drosophila* genome,',
      '*actin* 5C is critical for photoreceptor',
    ].join(' ')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason.includes('fragment') && pageText.includes(candidate.query))
      ?.query

    expect(fragmentCandidate).toBeTruthy()

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 1,
        section_title: 'Results',
        subsection_title: '2.3. The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes',
        chunk_ids: ['chunk-fuzzy-anchor'],
      },
      searchText: query,
      pageNumber: 1,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-fuzzy-anchor', '/fixtures/sample.pdf', 'fuzzy-anchor.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('fuzzy-anchor.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => {
        const inferredRanges = inferMockQueryMatchRanges(pageText, candidate)
        const matched = candidate === fragmentCandidate || inferredRanges.pageMatches.length > 0
        return {
          state: matched ? 0 : 1,
          total: matched ? Math.max(inferredRanges.pageMatches.length, 1) : 0,
          current: matched ? 1 : 0,
          pageIdx: matched ? 2 : null,
          pageMatches: matched ? inferredRanges.pageMatches : [],
          pageMatchesLength: matched ? inferredRanges.pageMatchesLength : [],
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [pageText, 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toHaveLength(1)
    expect(eventBus.findQueries[0]).toContain('Higher abundance of Actin 5C')
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedPage: 3,
      matchedQuery: expect.stringContaining('Higher abundance of Actin 5C'),
      note: 'Localized the quote with RapidFuzz and highlighted the verified native PDF.js span.',
    }))
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
  })

  it('keeps fuzzy anchoring aligned to the selected repeated match on the same page', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const firstSpan = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton.',
    ].join(' ')
    const secondSpan = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton and should be prioritized for the selected occurrence.',
    ].join(' ')
    const pageText = [
      'First occurrence.',
      firstSpan,
      'Bridge text.',
      secondSpan,
      'Results and Discussion',
    ].join(' ')
    const query = [
      'In summary, all proteins changed in the allele lacking the *crb_C* isoform',
      'constitute interesting candidates in the connection of the Crumbs function',
      'in organizing the cytoskeleton and should be prioritized for the selected occurrence.',
    ].join(' ')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason.includes('fragment') && firstSpan.includes(candidate.query) && secondSpan.includes(candidate.query))
      ?.query

    expect(fragmentCandidate).toBeTruthy()

    const firstMatchIndex = pageText.indexOf(fragmentCandidate ?? '')
    const secondMatchIndex = pageText.indexOf(fragmentCandidate ?? '', firstMatchIndex + 1)
    expect(firstMatchIndex).toBeGreaterThanOrEqual(0)
    expect(secondMatchIndex).toBeGreaterThan(firstMatchIndex)
    const secondSpanIndex = pageText.indexOf(secondSpan)
    expect(secondSpanIndex).toBeGreaterThanOrEqual(0)
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce({
      found: true,
      strategy: 'rapidfuzz-single-page',
      score: 96,
      matchedPage: 3,
      matchedQuery: secondSpan,
      matchedRange: {
        pageNumber: 3,
        rawStart: secondSpanIndex,
        rawEndExclusive: secondSpanIndex + secondSpan.length,
        query: secondSpan,
      },
      fullQuery: secondSpan,
      pageRanges: [{
        pageNumber: 3,
        rawStart: secondSpanIndex,
        rawEndExclusive: secondSpanIndex + secondSpan.length,
        query: secondSpan,
      }],
      crossPage: false,
      note: 'Localized quote text against PDF.js page text using RapidFuzz.',
    })

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 1,
        section_title: 'Results and Discussion',
        subsection_title: null,
        chunk_ids: ['chunk-repeated-fuzzy-anchor'],
      },
      searchText: query,
      pageNumber: 1,
      sectionTitle: 'Results and Discussion',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-repeated-fuzzy-anchor', '/fixtures/sample.pdf', 'repeated-fuzzy-anchor.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('repeated-fuzzy-anchor.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => {
        const inferredRanges = inferMockQueryMatchRanges(pageText, candidate)
        const matched = inferredRanges.pageMatches.length > 0
        return {
          state: matched ? 0 : 1,
          total: matched ? inferredRanges.pageMatches.length : 0,
          current: matched ? Math.min(2, inferredRanges.pageMatches.length) : 0,
          pageIdx: matched ? 2 : null,
          pageMatches: inferredRanges.pageMatches,
          pageMatchesLength: inferredRanges.pageMatchesLength,
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [pageText] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual([secondSpan])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      matchedPage: 3,
      matchedQuery: secondSpan,
      note: 'Localized the quote with RapidFuzz and highlighted the verified native PDF.js span.',
    }))
  })

  it('keeps exact repeated quote recovery aligned to the selected repeated fragment occurrence', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const repeatedQuote = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton.',
    ].join(' ')
    const query = `In summary, ${repeatedQuote}`
    const textSegments = ['Header ', repeatedQuote, ' Bridge ', repeatedQuote, ' Results']
    const pageText = textSegments.join('')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason.includes('fragment') && repeatedQuote.includes(candidate.query))
      ?.query

    expect(fragmentCandidate).toBeTruthy()

    const firstMatchIndex = pageText.indexOf(fragmentCandidate ?? '')
    const secondMatchIndex = pageText.indexOf(fragmentCandidate ?? '', firstMatchIndex + 1)
    expect(firstMatchIndex).toBeGreaterThanOrEqual(0)
    expect(secondMatchIndex).toBeGreaterThan(firstMatchIndex)
    const secondQuoteIndex = pageText.indexOf(repeatedQuote, pageText.indexOf(repeatedQuote) + 1)
    expect(secondQuoteIndex).toBeGreaterThanOrEqual(0)
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce({
      found: true,
      strategy: 'rapidfuzz-single-page',
      score: 96,
      matchedPage: 3,
      matchedQuery: repeatedQuote,
      matchedRange: {
        pageNumber: 3,
        rawStart: secondQuoteIndex,
        rawEndExclusive: secondQuoteIndex + repeatedQuote.length,
        query: repeatedQuote,
      },
      fullQuery: repeatedQuote,
      pageRanges: [{
        pageNumber: 3,
        rawStart: secondQuoteIndex,
        rawEndExclusive: secondQuoteIndex + repeatedQuote.length,
        query: repeatedQuote,
      }],
      crossPage: false,
      note: 'Localized quote text against PDF.js page text using RapidFuzz.',
    })

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 1,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-repeated-exact-anchor'],
      },
      searchText: query,
      pageNumber: 1,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-repeated-exact-anchor', '/fixtures/sample.pdf', 'repeated-exact-anchor.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('repeated-exact-anchor.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => {
        const inferredRanges = inferMockQueryMatchRanges(pageText, candidate)
        const matched = inferredRanges.pageMatches.length > 0
        return {
          state: matched ? 0 : 1,
          total: matched ? inferredRanges.pageMatches.length : 0,
          current: matched ? Math.min(2, inferredRanges.pageMatches.length) : 0,
          pageIdx: matched ? 2 : null,
          pageMatches: inferredRanges.pageMatches,
          pageMatchesLength: inferredRanges.pageMatchesLength,
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toContain(repeatedQuote)
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'rapidfuzz-single-page',
      matchedPage: 3,
      matchedQuery: repeatedQuote,
      note: 'Localized the quote with RapidFuzz and highlighted the verified native PDF.js span.',
    }))
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe)[0].getBoundingClientRect().top).toBe((2 * 900) + 48 + (3 * 28))
  })

  it('falls back to section context when normalized quote selection cannot be reverified natively', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const pageQuote = 'Actin 87E (80 +/- 51 fmoles/eye) is critical for photoreceptor maintenance.'
    const query = 'Actin 87E (80 ± 51 fmoles/eye) is critical for photoreceptor maintenance.'
    const textSegments = ['Header ', pageQuote, ' Bridge ', pageQuote, ' Results']
    const pageText = textSegments.join('')
    const firstMatchIndex = pageText.indexOf(pageQuote)
    const secondMatchIndex = pageText.indexOf(pageQuote, firstMatchIndex + 1)

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 1,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-pdfjs-offset-anchor'],
      },
      searchText: query,
      pageNumber: 1,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-pdfjs-offset-anchor', '/fixtures/sample.pdf', 'pdfjs-offset-anchor.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('pdfjs-offset-anchor.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (candidate) => {
        const inferredRanges = inferMockQueryMatchRanges(pageText, candidate)
        const matched = candidate === query || candidate === pageQuote || inferredRanges.pageMatches.length > 0
        return {
          state: matched ? 0 : 1,
          total: matched ? Math.max(inferredRanges.pageMatches.length, 1) : 0,
          current: matched ? Math.min(2, Math.max(inferredRanges.pageMatches.length, 1)) : 0,
          pageIdx: matched ? 2 : null,
          pageMatches: inferredRanges.pageMatches,
          pageMatchesLength: inferredRanges.pageMatchesLength,
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'section-fallback',
      locatorQuality: 'section_only',
      matchedPage: 3,
      matchedQuery: 'Results',
    }))
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-kind')).toBe('section')
  })

  it('renders hover previews differently from selected evidence highlights', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const hoverCommand = buildNavigationCommand({ mode: 'hover' })
    const selectCommand = buildNavigationCommand({ mode: 'select' })

    const { rerender } = render(<PdfViewer pendingNavigation={hoverCommand} />)

    dispatchPDFDocumentChanged('doc-3', '/fixtures/sample.pdf', 'hover-select.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('hover-select.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Exact quote from PDFX markdown' ? 0 : 1,
        total: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        current: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        pageIdx: query === 'Exact quote from PDFX markdown' ? 2 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    })
    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)[0].getAttribute('data-mode')).toBe('hover')
    })

    rerender(<PdfViewer pendingNavigation={selectCommand} />)

    await waitFor(() => {
      expect(getNativeSelectedHighlights(iframe)[0].getAttribute('data-mode')).toBe('select')
    })
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
  })

  it('highlights a section heading when quote matching fails but section metadata resolves', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'section',
        locator_quality: 'section_only',
        supports_decision: 'supports',
        snippet_text: 'Snippet text exists but should not drive quote search.',
        normalized_text: 'Snippet text exists but should not drive quote search.',
        viewer_search_text: null,
        page_number: 4,
        section_title: 'Results',
        subsection_title: 'Quantification',
        chunk_ids: ['chunk-section'],
      },
      searchText: null,
      pageNumber: 4,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-4', '/fixtures/sample.pdf', 'section.pdf', 12)
    await waitFor(() => {
      expect(screen.getByText('section.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Results' ? 0 : 1,
        total: query === 'Results' ? 1 : 0,
        current: query === 'Results' ? 1 : 0,
        pageIdx: query === 'Results' ? 3 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual(['Quantification', 'Results'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'section-fallback',
      locatorQuality: 'section_only',
      degraded: true,
      matchedQuery: 'Results',
      matchedPage: 4,
    }))
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-kind')).toBe('section')
    expect(screen.getByText('Section fallback')).toBeInTheDocument()
  })

  it('uses hinted pages for RapidFuzz localization and degrades to a page banner without quote retry loops', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: 'Repeated   quote\nwith “smart” punctuation and enough extra words to keep the retry chain moving before page fallback.',
        normalized_text: 'Repeated quote with "smart" punctuation and enough extra words to keep the retry chain moving before page fallback.',
        viewer_search_text: 'Repeated   quote\nwith “smart” punctuation and enough extra words to keep the retry chain moving before page fallback.',
        page_number: 3,
        section_title: null,
        subsection_title: null,
        chunk_ids: ['chunk-page-fallback'],
      },
      searchText: 'Repeated   quote\nwith “smart” punctuation and enough extra words to keep the retry chain moving before page fallback.',
      pageNumber: 3,
      sectionTitle: null,
    })
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce({
      found: true,
      strategy: 'rapidfuzz-single-page',
      score: 78,
      matchedPage: 3,
      matchedQuery: 'Repeated quote with "smart" punctuation',
      matchedRange: {
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: 'Repeated quote with "smart" punctuation'.length,
        query: 'Repeated quote with "smart" punctuation',
      },
      fullQuery: 'Repeated quote with "smart" punctuation',
      pageRanges: [{
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: 'Repeated quote with "smart" punctuation'.length,
        query: 'Repeated quote with "smart" punctuation',
      }],
      crossPage: false,
      note: 'Localized quote text against PDF.js page text using RapidFuzz.',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-5', '/fixtures/sample.pdf', 'page-fallback.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('page-fallback.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: () => ({
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(fuzzyMatchPdfEvidenceQuote).toHaveBeenCalledWith(expect.objectContaining({
      pageHints: [3],
    }))
    expect(eventBus.findDispatches).toEqual([{
      query: 'Repeated quote with "smart" punctuation',
      pageBeforeDispatch: 3,
    }])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'page-fallback',
      locatorQuality: 'page_only',
      degraded: true,
      matchedPage: 3,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(
      screen.getAllByText('Evidence on this page. Quote text was not matched reliably enough to highlight.'),
    ).toHaveLength(2)
  })

  it('degrades to page fallback when PDF.js finds a quote but no reliable text-layer rects can be derived', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: 'Raw quote with “smart” punctuation',
        normalized_text: 'Raw quote with "smart" punctuation',
        viewer_search_text: 'Raw quote with “smart” punctuation',
        page_number: 3,
        section_title: null,
        subsection_title: null,
        chunk_ids: ['chunk-strict-gate'],
      },
      searchText: 'Raw quote with “smart” punctuation',
      pageNumber: 3,
      sectionTitle: null,
    })
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce({
      found: true,
      strategy: 'rapidfuzz-single-page',
      score: 90,
      matchedPage: 3,
      matchedQuery: 'Raw quote with “smart” punctuation',
      matchedRange: {
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: 'Raw quote with “smart” punctuation'.length,
        query: 'Raw quote with “smart” punctuation',
      },
      fullQuery: 'Raw quote with “smart” punctuation',
      pageRanges: [{
        pageNumber: 3,
        rawStart: 0,
        rawEndExclusive: 'Raw quote with “smart” punctuation'.length,
        query: 'Raw quote with “smart” punctuation',
      }],
      crossPage: false,
      note: 'Localized quote text against PDF.js page text using RapidFuzz.',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-5b', '/fixtures/sample.pdf', 'strict-gate.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('strict-gate.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => ({
        state: query.includes('quote') ? 0 : 1,
        total: query.includes('quote') ? 1 : 0,
        current: query.includes('quote') ? 1 : 0,
        pageIdx: query.includes('quote') ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text', 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 4000 })

    expect(onNavigationStateChange).toHaveBeenCalledWith(expect.objectContaining({
      status: 'page-fallback',
      locatorQuality: 'page_only',
      degraded: true,
      matchedPage: 3,
    }))

    expect(eventBus.findQueries).toEqual(['Raw quote with “smart” punctuation'])
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(
      screen.getAllByText('Evidence on this page. Quote text was not matched reliably enough to highlight.'),
    ).toHaveLength(2)
  })

  it('does not treat a zero-count PDF.js FOUND state as a real quote match', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const ghostQuote = 'Ghost quote that PDF.js should not pretend to find'
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: ghostQuote,
        normalized_text: ghostQuote,
        viewer_search_text: ghostQuote,
        page_number: null,
        section_title: null,
        subsection_title: null,
        chunk_ids: ['chunk-zero-count-found'],
      },
      searchText: ghostQuote,
      pageNumber: null,
      sectionTitle: null,
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-zero-count-found', '/fixtures/sample.pdf', 'zero-count-found.pdf', 6)
    await waitFor(() => {
      expect(screen.getByText('zero-count-found.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: () => ({
        state: 0,
        total: 0,
        current: 0,
        pageIdx: null,
        viewerPageNumber: 1,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'not-found',
      locatorQuality: 'unresolved',
      degraded: true,
      matchedPage: null,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
  })

  it('degrades to the native anchor-page highlight for a cross-page quote without rendering quote overlays', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const pageThreeText = 'Cross-page quote start appears near the bottom of the anchor page and continues'
    const pageFourText = 'onto the next page where the remaining evidence text finishes cleanly.'
    const query = `${pageThreeText} ${pageFourText}`
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValueOnce(
      createCrossPageFuzzyMatchResult(3, pageThreeText, 4, pageFourText, query),
    )

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 3,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-cross-page-quote'],
      },
      searchText: query,
      pageNumber: 3,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-cross-page-quote', '/fixtures/sample.pdf', 'cross-page-quote.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('cross-page-quote.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === pageThreeText ? 0 : 1,
        total: candidate === pageThreeText ? 1 : 0,
        current: candidate === pageThreeText ? 1 : 0,
        pageIdx: candidate === pageThreeText ? 2 : null,
        pageMatches: candidate === pageThreeText ? [0] : [],
        pageMatchesLength: candidate === pageThreeText ? [pageThreeText.length] : [],
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [pageThreeText] },
        { pageNumber: 4, textSegments: [pageFourText] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 4000 })

    expect(eventBus.findQueries).toEqual([pageThreeText])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'rapidfuzz-stitched-page',
      locatorQuality: 'normalized_quote',
      degraded: true,
      matchedPage: 3,
      matchedQuery: pageThreeText,
      note: 'Localized the quote with RapidFuzz and kept the best native anchor-page PDF.js highlight because the recovered span crosses pages.',
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe)).toHaveLength(1)
    expect(screen.getByText('Page 3')).toBeInTheDocument()
  })

  it('falls back to subsection highlighting before a plain page banner when quote rects are unavailable', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.',
        normalized_text: 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.',
        viewer_search_text: 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.',
        page_number: 1,
        section_title: 'Results and Discussion',
        subsection_title: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        chunk_ids: ['chunk-live-repro'],
      },
      searchText: 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.',
      pageNumber: 1,
      sectionTitle: 'Results and Discussion',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-live-repro', '/fixtures/sample.pdf', 'live-repro.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('live-repro.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.'
          || query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'
          ? 1
          : 1,
        total: query === 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.'
          || query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'
          ? 1
          : 0,
        current: query === 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.'
          || query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'
          ? 1
          : 0,
        pageIdx: null,
        viewerPageNumber: query === 'Actin 87E accumulated to a higher molar abundance in mutant fly eyes.'
          || query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'
          ? 6
          : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: ['Methods'] },
        { pageNumber: 4, textSegments: ['Figure legends'] },
        { pageNumber: 5, textSegments: ['Discussion'] },
        { pageNumber: 6, textSegments: ['2.6. Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 3000 })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'section-fallback',
      locatorQuality: 'section_only',
      degraded: true,
      matchedPage: 6,
    }))
    expect(screen.getByText('Page 6')).toBeInTheDocument()
    expect(screen.getByText('Section fallback')).toBeInTheDocument()
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-kind')).toBe('section')
  })

  it('ignores stale async quote results when a newer evidence click wins', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const slowQuery = 'Slow quote from page six'
    const fastQuery = 'Fast quote from page three'

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-stale-async', '/fixtures/sample.pdf', 'stale-async.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('stale-async.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === slowQuery || query === fastQuery ? 0 : 1,
        total: query === slowQuery || query === fastQuery ? 1 : 0,
        current: query === slowQuery || query === fastQuery ? 1 : 0,
        pageIdx: query === fastQuery ? 2 : 5,
        delayedSelection: query === slowQuery
          ? {
              pageIdx: 5,
              viewerPageNumber: 6,
              delayMs: 350,
            }
          : undefined,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [fastQuery] },
        { pageNumber: 4, textSegments: ['Figure legends'] },
        { pageNumber: 5, textSegments: ['Discussion'] },
        { pageNumber: 6, textSegments: [slowQuery] },
      ],
    })

    fireEvent.load(iframe)

    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({
        anchorId: 'slow-anchor',
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'exact_quote',
          supports_decision: 'supports',
          snippet_text: slowQuery,
          normalized_text: slowQuery,
          viewer_search_text: slowQuery,
          page_number: 6,
          section_title: 'Discussion',
          subsection_title: null,
          chunk_ids: ['chunk-slow'],
        },
        searchText: slowQuery,
        pageNumber: 6,
        sectionTitle: 'Discussion',
      }),
    )
    window.setTimeout(() => {
      dispatchPDFViewerNavigateEvidence(
        buildNavigationCommand({
          anchorId: 'fast-anchor',
          anchor: {
            anchor_kind: 'snippet',
            locator_quality: 'exact_quote',
            supports_decision: 'supports',
            snippet_text: fastQuery,
            normalized_text: fastQuery,
            viewer_search_text: fastQuery,
            page_number: 3,
            section_title: 'Results',
            subsection_title: null,
            chunk_ids: ['chunk-fast'],
          },
          searchText: fastQuery,
          pageNumber: 3,
          sectionTitle: 'Results',
        }),
      )
    }, 50)

    await waitFor(() => {
      expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
        status: 'matched',
        locatorQuality: 'exact_quote',
        degraded: false,
        matchedPage: 3,
        matchedQuery: fastQuery,
      }))
    }, { timeout: 4000 })

    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe).some((node) => node.textContent?.includes(fastQuery))).toBe(true)
    expect(eventBus.findQueries).toEqual([fastQuery])
    expect(screen.getByText('Page 3')).toBeInTheDocument()
  })

  it('ignores stale async section fallback results when a newer evidence click wins', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const slowSection = 'Discussion'
    const fastQuery = 'Fast quote from page three'
    const slowCommand = buildNavigationCommand({
      anchorId: 'slow-section-anchor',
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'section_only',
        supports_decision: 'supports',
        snippet_text: null,
        sentence_text: null,
        normalized_text: null,
        viewer_search_text: null,
        page_number: 6,
        section_title: slowSection,
        subsection_title: null,
        chunk_ids: ['chunk-slow-section'],
      },
      searchText: null,
      pageNumber: 6,
      sectionTitle: slowSection,
    })
    const fastCommand = buildNavigationCommand({
      anchorId: 'fast-quote-anchor',
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: fastQuery,
        normalized_text: fastQuery,
        viewer_search_text: fastQuery,
        page_number: 3,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-fast-quote'],
      },
      searchText: fastQuery,
      pageNumber: 3,
      sectionTitle: 'Results',
    })

    const { rerender } = render(
      <PdfViewer
        pendingNavigation={slowCommand}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-stale-section', '/fixtures/sample.pdf', 'stale-section.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('stale-section.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => {
        if (query === slowSection) {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 5,
            dispatchDelayMs: 350,
          }
        }

        if (query === fastQuery) {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 2,
          }
        }

        return {
          state: 1,
          total: 0,
          current: 0,
          pageIdx: null,
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: [fastQuery] },
        { pageNumber: 4, textSegments: ['Figure legends'] },
        { pageNumber: 5, textSegments: ['Methods'] },
        { pageNumber: 6, textSegments: [slowSection] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(eventBus.findQueries).toEqual([slowSection])
    })

    rerender(
      <PdfViewer
        pendingNavigation={fastCommand}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    await waitFor(() => {
      expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
        status: 'matched',
        locatorQuality: 'exact_quote',
        degraded: false,
        matchedPage: 3,
        matchedQuery: fastQuery,
      }))
    }, { timeout: 4000 })

    await new Promise<void>((resolve) => {
      window.setTimeout(() => resolve(), 500)
    })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 3,
      matchedQuery: fastQuery,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(getNativeSelectedHighlights(iframe).some((node) => node.textContent?.includes(fastQuery))).toBe(true)
    expect(screen.getByText('Page 3')).toBeInTheDocument()
    expect(eventBus.findQueries[0]).toBe(slowSection)
    expect(eventBus.findQueries.at(-1)).toBe(fastQuery)
    expect(eventBus.findQueries.filter((query) => query === fastQuery).length).toBeGreaterThanOrEqual(1)
  })

  it('shows an unresolved marker when no reliable quote, section, or page target exists', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'document',
        locator_quality: 'unresolved',
        supports_decision: 'supports',
        snippet_text: null,
        sentence_text: null,
        normalized_text: null,
        viewer_search_text: null,
        page_number: null,
        section_title: null,
        subsection_title: null,
        chunk_ids: ['chunk-unresolved'],
      },
      searchText: null,
      pageNumber: null,
      sectionTitle: null,
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-6', '/fixtures/sample.pdf', 'unresolved.pdf', 6)
    await waitFor(() => {
      expect(screen.getByText('unresolved.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: () => ({
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'not-found',
      locatorQuality: 'unresolved',
      degraded: true,
      matchedPage: null,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(
      screen.getAllByText('Evidence localization is unresolved. No trusted page or text highlight could be produced.'),
    ).toHaveLength(2)
  })
})

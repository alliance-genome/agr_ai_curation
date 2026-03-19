export interface RenderedTextLayerReference {
  pageElement: HTMLElement
  pageNumber: number
  textLayer: HTMLElement
}

interface NormalizedCharacterPosition {
  node: Text | null
  offset: number
  pageNumber: number
  synthetic: boolean
}

export interface RenderedTextSearchIndex {
  characters: NormalizedCharacterPosition[]
  normalizedText: string
  renderedPages: number[]
}

export interface RenderedTextMatch {
  end: number
  endPosition: {
    node: Text
    offset: number
  }
  excerpt: string
  pages: number[]
  start: number
  startPosition: {
    node: Text
    offset: number
  }
}

export interface PageRelativeRect {
  height: number
  left: number
  pageNumber: number
  top: number
  width: number
}

const WHITESPACE_CHARACTER = /\s/u
const ZERO_WIDTH_CHARACTERS = new Set(['\u00AD', '\u200B', '\u200C', '\u200D', '\uFEFF'])

const isCollapsibleWhitespace = (character: string): boolean => {
  return character === '\u00A0' || WHITESPACE_CHARACTER.test(character)
}

const normalizePageTextValue = (
  value: string,
  pageNumber: number,
  node: Text | null,
  index: RenderedTextSearchIndex,
) => {
  let lastCharacterWasSpace = index.characters[index.characters.length - 1]?.synthetic === true
    ? true
    : index.normalizedText.endsWith(' ')

  for (let offset = 0; offset < value.length; offset += 1) {
    const character = value[offset]

    if (ZERO_WIDTH_CHARACTERS.has(character)) {
      continue
    }

    if (isCollapsibleWhitespace(character)) {
      if (index.normalizedText.length === 0 || lastCharacterWasSpace) {
        continue
      }

      index.normalizedText += ' '
      index.characters.push({
        node,
        offset,
        pageNumber,
        synthetic: false,
      })
      lastCharacterWasSpace = true
      continue
    }

    index.normalizedText += character
    index.characters.push({
      node,
      offset,
      pageNumber,
      synthetic: false,
    })
    lastCharacterWasSpace = false
  }
}

const insertPageBoundarySpace = (pageNumber: number, index: RenderedTextSearchIndex) => {
  if (index.normalizedText.length === 0 || index.normalizedText.endsWith(' ')) {
    return
  }

  index.normalizedText += ' '
  index.characters.push({
    node: null,
    offset: -1,
    pageNumber,
    synthetic: true,
  })
}

export const normalizeSearchSnippet = (value: string): string => {
  const normalized: RenderedTextSearchIndex = {
    characters: [],
    normalizedText: '',
    renderedPages: [],
  }

  normalizePageTextValue(value, 1, null, normalized)
  return normalized.normalizedText.trim()
}

export const getRenderedTextLayerReferences = (iframeDoc: Document): RenderedTextLayerReference[] => {
  return Array.from(iframeDoc.querySelectorAll<HTMLElement>('.page[data-page-number]')).flatMap((pageElement) => {
    const pageNumber = Number(pageElement.dataset.pageNumber)
    const textLayer = pageElement.querySelector<HTMLElement>('.textLayer')

    if (!Number.isFinite(pageNumber) || !textLayer) {
      return []
    }

    return [{ pageElement, pageNumber, textLayer }]
  })
}

export const buildRenderedTextSearchIndex = (
  textLayers: RenderedTextLayerReference[],
): RenderedTextSearchIndex => {
  const index: RenderedTextSearchIndex = {
    characters: [],
    normalizedText: '',
    renderedPages: textLayers.map((layer) => layer.pageNumber),
  }

  textLayers.forEach(({ pageNumber, textLayer }, layerIndex) => {
    if (layerIndex > 0) {
      insertPageBoundarySpace(pageNumber, index)
    }

    const walker = textLayer.ownerDocument.createTreeWalker(textLayer, NodeFilter.SHOW_TEXT)
    let currentNode = walker.nextNode()

    while (currentNode) {
      const textNode = currentNode as Text
      if (textNode.textContent) {
        normalizePageTextValue(textNode.textContent, pageNumber, textNode, index)
      }
      currentNode = walker.nextNode()
    }
  })

  return index
}

export const findRenderedTextMatches = (
  index: RenderedTextSearchIndex,
  snippet: string,
): RenderedTextMatch[] => {
  const normalizedSnippet = normalizeSearchSnippet(snippet)
  if (!normalizedSnippet) {
    return []
  }

  const haystack = index.normalizedText.toLocaleLowerCase()
  const needle = normalizedSnippet.toLocaleLowerCase()
  const matches: RenderedTextMatch[] = []

  let searchStart = 0
  while (searchStart < haystack.length) {
    const foundAt = haystack.indexOf(needle, searchStart)
    if (foundAt === -1) {
      break
    }

    const end = foundAt + needle.length
    const slice = index.characters.slice(foundAt, end)
    const startPosition = slice.find((entry) => entry.node !== null)
    const endPosition = [...slice].reverse().find((entry) => entry.node !== null)

    if (startPosition?.node && endPosition?.node) {
      const pages = Array.from(
        new Set(
          slice
            .filter((entry) => entry.node !== null)
            .map((entry) => entry.pageNumber),
        ),
      )

      matches.push({
        excerpt: index.normalizedText.slice(foundAt, end),
        start: foundAt,
        end,
        pages,
        startPosition: {
          node: startPosition.node,
          offset: startPosition.offset,
        },
        endPosition: {
          node: endPosition.node,
          offset: endPosition.offset,
        },
      })
    }

    searchStart = foundAt + 1
  }

  return matches
}

export const createRangeForRenderedTextMatch = (match: RenderedTextMatch): Range => {
  const range = match.startPosition.node.ownerDocument.createRange()
  range.setStart(match.startPosition.node, match.startPosition.offset)
  range.setEnd(match.endPosition.node, match.endPosition.offset + 1)
  return range
}

export const groupClientRectsByPage = (
  textLayers: RenderedTextLayerReference[],
  clientRects: Iterable<Pick<DOMRect, 'height' | 'left' | 'top' | 'width'>>,
): PageRelativeRect[] => {
  const pageBounds = textLayers.map(({ pageElement, pageNumber }) => ({
    pageNumber,
    rect: pageElement.getBoundingClientRect(),
  }))

  return Array.from(clientRects).flatMap((rect) => {
    if (rect.width <= 0 || rect.height <= 0) {
      return []
    }

    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2

    const containingPage = pageBounds.find(({ rect: pageRect }) => (
      centerX >= pageRect.left &&
      centerX <= pageRect.right &&
      centerY >= pageRect.top &&
      centerY <= pageRect.bottom
    ))

    if (!containingPage) {
      return []
    }

    return [{
      pageNumber: containingPage.pageNumber,
      left: rect.left - containingPage.rect.left,
      top: rect.top - containingPage.rect.top,
      width: rect.width,
      height: rect.height,
    }]
  })
}

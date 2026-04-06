#!/usr/bin/env node

import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const REPO_ROOT = path.resolve(__dirname, '..', '..')
const FRONTEND_ROOT = path.join(REPO_ROOT, 'frontend')
const DEFAULT_CONTEXT_CHARS = 120
const DEFAULT_MATCH_LIMIT = 10
const DEFAULT_TIMEOUT_MS = 5000

const SOFT_HYPHEN = '\u00ad'
const NBSP = '\u00a0'
const ELLIPSIS_PATTERN = /(?:\u2026|\.{3,})/g
const DASH_PATTERN = /[\u2010\u2011\u2012\u2013\u2014\u2212]/g
const SINGLE_QUOTE_PATTERN = /[\u2018\u2019\u201A\u201B]/g
const DOUBLE_QUOTE_PATTERN = /[\u201C\u201D\u201E\u201F]/g
const OPENING_BRACKETS = new Set(['(', '[', '{'])
const TRAILING_SPACE_PUNCTUATION = new Set([',', '.', ';', ':', '!', '?', ')', ']', '}'])
const INLINE_MARKDOWN_WRAPPER_PATTERNS = [
  [/(^|[\s([{])\*\*([^*]+?)\*\*(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])\*([^*]+?)\*(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])__([^_]+?)__(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])_([^_]+?)_(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
  [/(^|[\s([{])`([^`]+?)`(?=$|[\s)\]}.,;:!?])/g, '$1$2'],
]

const FIND_STATE_NAMES = {
  0: 'FOUND',
  1: 'NOT_FOUND',
  2: 'WRAPPED',
  3: 'PENDING',
}

function printUsage() {
  console.log(`Usage:
  node scripts/utilities/pdfjs_find_probe.mjs --pdf <path> [options]

Options:
  --pdf <path>              PDF file to inspect (required)
  --query <text>            Query to run through real PDF.js find (repeatable)
  --query-file <path>       Load queries from a text file or JSON array. JSON entries may
                            be strings or objects with { id, query, preferredPageNumber }.
  --page <number>           Restrict page output to a single page (repeatable)
  --pages <list>            Restrict page output, e.g. "1,2,8-10"
  --context <chars>         Context window around matches (default: ${DEFAULT_CONTEXT_CHARS})
  --match-limit <count>     Max occurrences included per matched page (default: ${DEFAULT_MATCH_LIMIT})
  --include-text-items      Include raw textContent items for selected pages
  --output <path>           Write JSON output to a file instead of stdout
  --timeout-ms <ms>         Query settle timeout (default: ${DEFAULT_TIMEOUT_MS})
  --help                    Show this help

Examples:
  node scripts/utilities/pdfjs_find_probe.mjs \\
    --pdf /home/ctabone/analysis/alliance/ai_curation_new/agr_ai_curation/sample_fly_publication.pdf \\
    --query "Absolute Quantification of Proteins in the Eye of"

  node scripts/utilities/pdfjs_find_probe.mjs \\
    --pdf /home/ctabone/analysis/alliance/ai_curation_new/agr_ai_curation/sample_fly_publication.pdf \\
    --query-file /tmp/queries.txt --output /tmp/pdf-probe.json`)
}

function fail(message) {
  console.error(message)
  process.exitCode = 1
}

function isWhitespaceCharacter(value) {
  return /\s/.test(value)
}

function sanitizeEvidenceSearchText(value) {
  if (!value) {
    return ''
  }

  let sanitized = value.replace(ELLIPSIS_PATTERN, ' ')

  for (let index = 0; index < 2; index += 1) {
    let nextValue = sanitized
    INLINE_MARKDOWN_WRAPPER_PATTERNS.forEach(([pattern, replacement]) => {
      nextValue = nextValue.replace(pattern, replacement)
    })
    if (nextValue === sanitized) {
      break
    }
    sanitized = nextValue
  }

  return sanitized
}

function transformNormalizedCharacter(value) {
  if (value === SOFT_HYPHEN) {
    return ''
  }

  if (value === NBSP) {
    return ' '
  }

  if (value === '\r' || value === '\n') {
    return ' '
  }

  return value
    .replace(DASH_PATTERN, '-')
    .replace(SINGLE_QUOTE_PATTERN, "'")
    .replace(DOUBLE_QUOTE_PATTERN, '"')
}

function buildNormalizedTextSourceMap(value) {
  const output = []
  const sourceIndices = []

  for (let index = 0; index < value.length;) {
    const codePoint = value.codePointAt(index)
    const codeUnitLength = codePoint !== undefined && codePoint > 0xffff ? 2 : 1
    const rawChunk = value.slice(index, index + codeUnitLength).normalize('NFKC')

    for (const normalizedCharacter of rawChunk) {
      const nextCharacter = transformNormalizedCharacter(normalizedCharacter)

      for (const character of nextCharacter) {
        if (character.length === 0) {
          continue
        }

        if (isWhitespaceCharacter(character)) {
          const previous = output[output.length - 1]

          if (previous === undefined || isWhitespaceCharacter(previous) || OPENING_BRACKETS.has(previous)) {
            continue
          }

          output.push(' ')
          sourceIndices.push(index)
          continue
        }

        const previous = output[output.length - 1]
        if (previous === ' ' && TRAILING_SPACE_PUNCTUATION.has(character)) {
          output.pop()
          sourceIndices.pop()
        }

        output.push(character)
        sourceIndices.push(index)
      }
    }

    index += codeUnitLength
  }

  while (output.length > 0 && output[output.length - 1] === ' ') {
    output.pop()
    sourceIndices.pop()
  }

  return {
    text: output.join(''),
    sourceIndices,
  }
}

function normalizeTextForEvidenceMatch(value) {
  return buildNormalizedTextSourceMap(value).text
}

function splitNormalizedWords(value) {
  const normalized = normalizeTextForEvidenceMatch(value)
  return normalized.length > 0 ? normalized.split(/\s+/).filter(Boolean) : []
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

async function waitFor(predicate, timeoutMs, label) {
  const startedAt = Date.now()

  while (Date.now() - startedAt < timeoutMs) {
    const result = await predicate()
    if (result) {
      return result
    }
    await delay(25)
  }

  throw new Error(`Timed out waiting for ${label} after ${timeoutMs}ms`)
}

function parsePagesSpec(value) {
  const pages = new Set()

  value
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .forEach((part) => {
      const rangeMatch = part.match(/^(\d+)-(\d+)$/)
      if (rangeMatch) {
        const start = Number.parseInt(rangeMatch[1], 10)
        const end = Number.parseInt(rangeMatch[2], 10)
        const low = Math.min(start, end)
        const high = Math.max(start, end)
        for (let pageNumber = low; pageNumber <= high; pageNumber += 1) {
          pages.add(pageNumber)
        }
        return
      }

      const pageNumber = Number.parseInt(part, 10)
      if (!Number.isInteger(pageNumber) || pageNumber < 1) {
        throw new Error(`Invalid page specifier: ${part}`)
      }
      pages.add(pageNumber)
    })

  return pages
}

async function loadQueriesFromFile(queryFile) {
  const content = await fs.readFile(queryFile, 'utf8')
  const trimmed = content.trim()
  if (!trimmed) {
    return []
  }

  if (trimmed.startsWith('[')) {
    const parsed = JSON.parse(trimmed)
    if (!Array.isArray(parsed)) {
      throw new Error(`Expected JSON array in query file: ${queryFile}`)
    }
    return parsed
      .map((value, index) => normalizeQuerySpec(value, `query-file[${index}]`))
      .filter(Boolean)
  }

  return content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((query) => ({ query, id: null, preferredPageNumber: null }))
}

function normalizeQuerySpec(value, label = 'query spec') {
  if (typeof value === 'string') {
    const query = value.trim()
    if (!query) {
      return null
    }
    return {
      id: null,
      query,
      preferredPageNumber: null,
    }
  }

  if (!value || typeof value !== 'object') {
    throw new Error(`Invalid ${label}: expected a string or object`)
  }

  const queryValue =
    typeof value.query === 'string'
      ? value.query
      : typeof value.text === 'string'
        ? value.text
        : ''
  const query = queryValue.trim()
  if (!query) {
    return null
  }

  const preferredPageNumberCandidate =
    value.preferredPageNumber ?? value.preferred_page_number ?? value.pageNumber ?? value.page_number ?? null
  const preferredPageNumber =
    Number.isInteger(preferredPageNumberCandidate) && preferredPageNumberCandidate >= 1
      ? preferredPageNumberCandidate
      : null

  return {
    id:
      value.id === null || value.id === undefined
        ? null
        : String(value.id),
    query,
    preferredPageNumber,
  }
}

function parseArgs(argv) {
  const options = {
    pdfPath: null,
    querySpecs: [],
    pageNumbers: new Set(),
    contextChars: DEFAULT_CONTEXT_CHARS,
    matchLimit: DEFAULT_MATCH_LIMIT,
    includeTextItems: false,
    outputPath: null,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  }

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]

    if (arg === '--help') {
      options.help = true
      continue
    }

    if (arg === '--include-text-items') {
      options.includeTextItems = true
      continue
    }

    const next = argv[index + 1]
    if (!next) {
      throw new Error(`Missing value for ${arg}`)
    }

    switch (arg) {
      case '--pdf':
        options.pdfPath = next
        index += 1
        break
      case '--query':
        options.querySpecs.push({
          id: null,
          query: next,
          preferredPageNumber: null,
        })
        index += 1
        break
      case '--query-file':
        options.queryFile = next
        index += 1
        break
      case '--page':
        options.pageNumbers.add(Number.parseInt(next, 10))
        index += 1
        break
      case '--pages':
        parsePagesSpec(next).forEach((pageNumber) => options.pageNumbers.add(pageNumber))
        index += 1
        break
      case '--context':
        options.contextChars = Number.parseInt(next, 10)
        index += 1
        break
      case '--match-limit':
        options.matchLimit = Number.parseInt(next, 10)
        index += 1
        break
      case '--output':
        options.outputPath = next
        index += 1
        break
      case '--timeout-ms':
        options.timeoutMs = Number.parseInt(next, 10)
        index += 1
        break
      default:
        throw new Error(`Unknown argument: ${arg}`)
    }
  }

  if (!options.help && !options.pdfPath) {
    throw new Error('Missing required --pdf argument')
  }

  if (!Number.isInteger(options.contextChars) || options.contextChars < 0) {
    throw new Error(`Invalid --context value: ${options.contextChars}`)
  }

  if (!Number.isInteger(options.matchLimit) || options.matchLimit < 1) {
    throw new Error(`Invalid --match-limit value: ${options.matchLimit}`)
  }

  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 100) {
    throw new Error(`Invalid --timeout-ms value: ${options.timeoutMs}`)
  }

  options.pageNumbers.forEach((pageNumber) => {
    if (!Number.isInteger(pageNumber) || pageNumber < 1) {
      throw new Error(`Invalid page number: ${pageNumber}`)
    }
  })

  return options
}

function createDomEnvironment() {
  const require = createRequire(import.meta.url)
  const { JSDOM } = require(path.join(FRONTEND_ROOT, 'node_modules', 'jsdom'))
  const dom = new JSDOM('<!doctype html><html><body></body></html>')

  globalThis.window = dom.window
  globalThis.document = dom.window.document
  Object.defineProperty(globalThis, 'navigator', {
    value: dom.window.navigator,
    configurable: true,
  })
  globalThis.HTMLElement = dom.window.HTMLElement
  globalThis.Node = dom.window.Node
  globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window)
  globalThis.window.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0)
  globalThis.window.cancelAnimationFrame = (id) => clearTimeout(id)

  return dom
}

async function loadPdfJsModules() {
  const pdfjsLib = await import(
    pathToFileURL(path.join(FRONTEND_ROOT, 'node_modules', 'pdfjs-dist', 'legacy', 'build', 'pdf.mjs'))
  )
  globalThis.pdfjsLib = pdfjsLib

  const pdfViewer = await import(
    pathToFileURL(path.join(FRONTEND_ROOT, 'node_modules', 'pdfjs-dist', 'legacy', 'web', 'pdf_viewer.mjs'))
  )

  return {
    pdfjsLib,
    EventBus: pdfViewer.EventBus,
    PDFFindController: pdfViewer.PDFFindController,
  }
}

function joinTextContentItems(textContent) {
  const parts = []

  for (const item of textContent.items) {
    parts.push(item.str)
    if (item.hasEOL) {
      parts.push('\n')
    }
  }

  return parts.join('')
}

function preview(value, limit = 160) {
  if (value.length <= limit) {
    return value
  }
  return `${value.slice(0, limit)}...`
}

function removeWhitespace(value) {
  return value.replace(/\s+/g, '')
}

function findAllLiteralIndices(haystack, needle, limit = DEFAULT_MATCH_LIMIT) {
  if (!needle) {
    return []
  }

  const indices = []
  let searchStart = 0

  while (indices.length < limit) {
    const index = haystack.indexOf(needle, searchStart)
    if (index < 0) {
      break
    }
    indices.push(index)
    searchStart = index + Math.max(needle.length, 1)
  }

  return indices
}

function buildWhitespaceCollapsedSourceMap(value) {
  const output = []
  const sourceIndices = []

  for (let index = 0; index < value.length; index += 1) {
    const character = value[index]
    if (isWhitespaceCharacter(character)) {
      continue
    }
    output.push(character)
    sourceIndices.push(index)
  }

  return {
    text: output.join(''),
    sourceIndices,
  }
}

function buildWhitespaceBoundaryPattern(value) {
  const compactCharacters = []
  const compactIndices = []
  const boundaries = []
  let sawWhitespaceSincePrevious = false

  for (let index = 0; index < value.length; index += 1) {
    const character = value[index]

    if (isWhitespaceCharacter(character)) {
      if (compactCharacters.length > 0) {
        sawWhitespaceSincePrevious = true
      }
      continue
    }

    if (compactCharacters.length > 0) {
      const leftIndex = compactIndices[compactIndices.length - 1]
      boundaries.push({
        pairIndex: boundaries.length,
        leftChar: compactCharacters[compactCharacters.length - 1],
        rightChar: character,
        leftIndex,
        rightIndex: index,
        hasWhitespace: sawWhitespaceSincePrevious,
        between: value.slice(leftIndex + 1, index),
      })
    }

    compactCharacters.push(character)
    compactIndices.push(index)
    sawWhitespaceSincePrevious = false
  }

  return {
    compactText: compactCharacters.join(''),
    compactIndices,
    boundaries,
  }
}

function analyzeWhitespaceBoundaryAlignment(queryText, matchedText, limit = 12) {
  const queryPattern = buildWhitespaceBoundaryPattern(queryText)
  const matchedPattern = buildWhitespaceBoundaryPattern(matchedText)

  if (queryPattern.compactText !== matchedPattern.compactText) {
    return {
      compactTextsEqual: false,
      queryCompactLength: queryPattern.compactText.length,
      matchedCompactLength: matchedPattern.compactText.length,
      queryCompactPreview: preview(queryPattern.compactText),
      matchedCompactPreview: preview(matchedPattern.compactText),
    }
  }

  let identicalBoundaryCount = 0
  let sharedWhitespaceBoundaryCount = 0
  let queryOnlyWhitespaceBoundaryCount = 0
  let matchedOnlyWhitespaceBoundaryCount = 0
  const differenceSamples = []

  for (let index = 0; index < queryPattern.boundaries.length; index += 1) {
    const queryBoundary = queryPattern.boundaries[index]
    const matchedBoundary = matchedPattern.boundaries[index]

    if (queryBoundary.hasWhitespace === matchedBoundary.hasWhitespace) {
      identicalBoundaryCount += 1
      if (queryBoundary.hasWhitespace) {
        sharedWhitespaceBoundaryCount += 1
      }
      continue
    }

    if (queryBoundary.hasWhitespace) {
      queryOnlyWhitespaceBoundaryCount += 1
    } else {
      matchedOnlyWhitespaceBoundaryCount += 1
    }

    if (differenceSamples.length < limit) {
      differenceSamples.push({
        pairIndex: index,
        pair: `${queryBoundary.leftChar}|${queryBoundary.rightChar}`,
        queryHasWhitespace: queryBoundary.hasWhitespace,
        matchedHasWhitespace: matchedBoundary.hasWhitespace,
        queryBetween: queryBoundary.between,
        matchedBetween: matchedBoundary.between,
        queryPreview: queryText.slice(
          Math.max(0, queryBoundary.leftIndex - 25),
          Math.min(queryText.length, queryBoundary.rightIndex + 26),
        ),
        matchedPreview: matchedText.slice(
          Math.max(0, matchedBoundary.leftIndex - 25),
          Math.min(matchedText.length, matchedBoundary.rightIndex + 26),
        ),
      })
    }
  }

  const queryWhitespaceBoundaryCount = queryPattern.boundaries.filter((entry) => entry.hasWhitespace).length
  const matchedWhitespaceBoundaryCount = matchedPattern.boundaries.filter((entry) => entry.hasWhitespace).length

  return {
    compactTextsEqual: true,
    totalCharacterBoundaries: queryPattern.boundaries.length,
    identicalBoundaryCount,
    sharedWhitespaceBoundaryCount,
    queryWhitespaceBoundaryCount,
    matchedWhitespaceBoundaryCount,
    queryOnlyWhitespaceBoundaryCount,
    matchedOnlyWhitespaceBoundaryCount,
    queryWhitespaceCollapseRate:
      queryWhitespaceBoundaryCount > 0 ? queryOnlyWhitespaceBoundaryCount / queryWhitespaceBoundaryCount : 0,
    allQueryWhitespaceBoundariesCollapsed:
      queryWhitespaceBoundaryCount > 0 && queryOnlyWhitespaceBoundaryCount === queryWhitespaceBoundaryCount,
    differenceSamples,
  }
}

function computeSharedWords(left, right) {
  const leftWords = splitNormalizedWords(left)
  const rightWords = splitNormalizedWords(right)
  const rightSet = new Set(rightWords)
  const shared = []

  for (const word of leftWords) {
    if (rightSet.has(word) && !shared.includes(word)) {
      shared.push(word)
    }
  }

  return {
    leftWordCount: leftWords.length,
    rightWordCount: rightWords.length,
    sharedWordCount: shared.length,
    sharedWords: shared,
  }
}

function buildOccurrenceRecord({
  pageNumber,
  pageMatchIndex,
  rawStart,
  rawLength,
  rawPageText,
  pdfjsSearchText,
  queryText,
  contextChars,
}) {
  const rawEndExclusive = rawStart + rawLength
  const rawSlice = rawPageText.slice(rawStart, rawEndExclusive)
  const adapterSlice = pdfjsSearchText.slice(rawStart, rawEndExclusive)
  const rawNormalized = normalizeTextForEvidenceMatch(rawSlice)
  const adapterNormalized = normalizeTextForEvidenceMatch(adapterSlice)
  const normalizedQuery = normalizeTextForEvidenceMatch(queryText)
  const sharedWords = computeSharedWords(queryText, rawSlice)

  return {
    pageNumber,
    pageMatchIndex,
    rawStart,
    rawLength,
    rawEndExclusive,
    rawSlice,
    rawSlicePreview: preview(rawSlice),
    currentAdapterSlice: adapterSlice,
    currentAdapterSlicePreview: preview(adapterSlice),
    rawSliceMatchesAdapterSlice: rawSlice === adapterSlice,
    rawNormalized,
    adapterNormalized,
    normalizedQuery,
    rawNormalizedContainedInQuery: rawNormalized.length > 0 ? normalizedQuery.includes(rawNormalized) : false,
    queryContainedInRawNormalized: normalizedQuery.length > 0 ? rawNormalized.includes(normalizedQuery) : false,
    adapterNormalizedContainedInQuery:
      adapterNormalized.length > 0 ? normalizedQuery.includes(adapterNormalized) : false,
    queryContainedInAdapterNormalized:
      normalizedQuery.length > 0 ? adapterNormalized.includes(normalizedQuery) : false,
    sharedWords,
    context: {
      prefix: rawPageText.slice(Math.max(0, rawStart - contextChars), rawStart),
      match: rawSlice,
      suffix: rawPageText.slice(rawEndExclusive, rawEndExclusive + contextChars),
    },
  }
}

function buildWhitespaceCollapsedMatchRecord({
  pageNumber,
  collapsedStart,
  collapsedLength,
  rawPageText,
  queryText,
  contextChars,
}) {
  const collapsedPageMap = buildWhitespaceCollapsedSourceMap(rawPageText)
  const rawStart = collapsedPageMap.sourceIndices[collapsedStart]
  const rawEndInclusive = collapsedPageMap.sourceIndices[collapsedStart + collapsedLength - 1]
  const rawEndExclusive = rawEndInclusive + 1
  const rawSlice = rawPageText.slice(rawStart, rawEndExclusive)

  return {
    pageNumber,
    collapsedStart,
    collapsedLength,
    rawStart,
    rawEndExclusive,
    rawSlice,
    rawSlicePreview: preview(rawSlice),
    rawWhitespaceStripped: removeWhitespace(rawSlice),
    queryWhitespaceStripped: removeWhitespace(queryText),
    boundaryAlignment: analyzeWhitespaceBoundaryAlignment(queryText, rawSlice),
    context: {
      prefix: rawPageText.slice(Math.max(0, rawStart - contextChars), rawStart),
      match: rawSlice,
      suffix: rawPageText.slice(rawEndExclusive, rawEndExclusive + contextChars),
    },
  }
}

async function primeFindController(findController, eventBus, pagesCount, timeoutMs) {
  eventBus.dispatch('find', {
    source: 'pdfjs-find-probe-prime',
    type: 'again',
    query: '',
    caseSensitive: false,
    entireWord: false,
    findPrevious: false,
    highlightAll: false,
    matchDiacritics: false,
  })

  await waitFor(
    () => Array.isArray(findController._extractTextPromises) && findController._extractTextPromises.length === pagesCount,
    timeoutMs,
    'PDF.js text extraction setup',
  )
  await Promise.all(findController._extractTextPromises)
}

async function extractPages(pdfDocument, findController, includeTextItems) {
  const pages = []

  for (let pageNumber = 1; pageNumber <= pdfDocument.numPages; pageNumber += 1) {
    const page = await pdfDocument.getPage(pageNumber)
    const textContent = await page.getTextContent({ disableNormalization: true })
    const rawJoinedText = joinTextContentItems(textContent)
    const pdfjsSearchText = findController._pageContents?.[pageNumber - 1] ?? ''
    const pdfjsDiffs = findController._pageDiffs?.[pageNumber - 1] ?? null

    pages.push({
      pageNumber,
      textItemCount: textContent.items.length,
      rawTextLength: rawJoinedText.length,
      pdfjsSearchTextLength: pdfjsSearchText.length,
      diffEntryCount: Array.isArray(pdfjsDiffs) ? pdfjsDiffs.length : 0,
      pdfjsHasDiacritics: Boolean(findController._hasDiacritics?.[pageNumber - 1]),
      rawTextPreview: preview(rawJoinedText),
      pdfjsSearchTextPreview: preview(pdfjsSearchText),
      rawText: rawJoinedText,
      pdfjsSearchText,
      textItems: includeTextItems
        ? textContent.items.map((item, index) => ({
            index,
            str: item.str,
            dir: item.dir,
            hasEOL: Boolean(item.hasEOL),
            width: item.width,
            height: item.height,
            fontName: item.fontName,
            transform: Array.isArray(item.transform) ? item.transform : null,
          }))
        : undefined,
    })
  }

  return pages
}

async function runFindQuery({
  findController,
  eventBus,
  linkService,
  querySpec,
  pageRecords,
  contextChars,
  matchLimit,
  timeoutMs,
}) {
  const query = querySpec.query
  const preferredPageNumber = querySpec.preferredPageNumber ?? null
  const controlEvents = []
  const countEvents = []
  let settledControlEvent = null

  const onControlState = (event) => {
    if (event.rawQuery !== query) {
      return
    }
    const entry = {
      type: 'controlstate',
      state: event.state,
      stateName: FIND_STATE_NAMES[event.state] ?? `UNKNOWN_${String(event.state)}`,
      matchesCount: event.matchesCount,
      currentPage: linkService.page,
      selectedPageIndex: findController.selected?.pageIdx ?? null,
      selectedMatchIndex: findController.selected?.matchIdx ?? null,
    }
    controlEvents.push(entry)
    if (event.state !== 3) {
      settledControlEvent = entry
    }
  }

  const onMatchesCount = (event) => {
    countEvents.push({
      type: 'matchescount',
      matchesCount: event.matchesCount,
      currentPage: linkService.page,
      selectedPageIndex: findController.selected?.pageIdx ?? null,
      selectedMatchIndex: findController.selected?.matchIdx ?? null,
    })
  }

  eventBus._on('updatefindcontrolstate', onControlState)
  eventBus._on('updatefindmatchescount', onMatchesCount)

  try {
    if (Number.isInteger(preferredPageNumber) && preferredPageNumber >= 1 && preferredPageNumber <= linkService.pagesCount) {
      linkService.page = preferredPageNumber
    }

    eventBus.dispatch('find', {
      source: 'pdfjs-find-probe',
      type: 'again',
      query,
      caseSensitive: false,
      entireWord: false,
      findPrevious: false,
      highlightAll: true,
      matchDiacritics: false,
    })

    await waitFor(
      () => settledControlEvent !== null && findController._pendingFindMatches?.size === 0,
      timeoutMs,
      `query "${preview(query, 40)}" to settle`,
    )

    const matchedPages = []
    let matchesTotal = 0

    for (let pageIndex = 0; pageIndex < pageRecords.length; pageIndex += 1) {
      const pageMatches = findController.pageMatches?.[pageIndex] ?? []
      const pageMatchesLength = findController.pageMatchesLength?.[pageIndex] ?? []

      if (!Array.isArray(pageMatches) || !Array.isArray(pageMatchesLength) || pageMatches.length === 0) {
        continue
      }

      matchesTotal += pageMatches.length
      const pageRecord = pageRecords[pageIndex]
      const occurrences = pageMatches
        .slice(0, matchLimit)
        .map((rawStart, matchIndex) =>
          buildOccurrenceRecord({
            pageNumber: pageIndex + 1,
            pageMatchIndex: matchIndex,
            rawStart,
            rawLength: pageMatchesLength[matchIndex] ?? 0,
            rawPageText: pageRecord.rawText,
            pdfjsSearchText: pageRecord.pdfjsSearchText,
            queryText: query,
            contextChars,
          }),
        )

      matchedPages.push({
        pageNumber: pageIndex + 1,
        matchCount: pageMatches.length,
        includedMatchCount: occurrences.length,
        occurrences,
      })
    }

    const selectedPageIndex = findController.selected?.pageIdx ?? -1
    const selectedMatchIndex = findController.selected?.matchIdx ?? -1
    let selectedOccurrence = null

    if (selectedPageIndex >= 0 && selectedMatchIndex >= 0) {
      const rawStart = findController.pageMatches?.[selectedPageIndex]?.[selectedMatchIndex]
      const rawLength = findController.pageMatchesLength?.[selectedPageIndex]?.[selectedMatchIndex]
      const pageRecord = pageRecords[selectedPageIndex]

      if (
        typeof rawStart === 'number'
        && rawStart >= 0
        && typeof rawLength === 'number'
        && rawLength > 0
        && pageRecord
      ) {
        selectedOccurrence = buildOccurrenceRecord({
          pageNumber: selectedPageIndex + 1,
          pageMatchIndex: selectedMatchIndex,
          rawStart,
          rawLength,
          rawPageText: pageRecord.rawText,
          pdfjsSearchText: pageRecord.pdfjsSearchText,
          queryText: query,
          contextChars,
        })
      }
    }

    const sanitizedQuery = sanitizeEvidenceSearchText(query)
    const normalizedQuery = normalizeTextForEvidenceMatch(query)
    const pdfjsNormalizedQuery =
      typeof findController._normalizedQuery === 'string' ? findController._normalizedQuery : null
    const literalPdfjsNormalizedQueryMatches = pageRecords
      .map((pageRecord) => ({
        pageNumber: pageRecord.pageNumber,
        indices: findAllLiteralIndices(pageRecord.pdfjsSearchText, pdfjsNormalizedQuery ?? '', matchLimit),
      }))
      .filter((entry) => entry.indices.length > 0)
    const whitespaceCollapsedQuery = removeWhitespace(query)
    const whitespaceCollapsedMatches = whitespaceCollapsedQuery
      ? pageRecords
          .map((pageRecord) => {
            const collapsedPageMap = buildWhitespaceCollapsedSourceMap(pageRecord.rawText)
            const indices = findAllLiteralIndices(collapsedPageMap.text, whitespaceCollapsedQuery, matchLimit)
            if (indices.length === 0) {
              return null
            }

            return {
              pageNumber: pageRecord.pageNumber,
              indices,
              occurrences: indices.map((collapsedStart) =>
                buildWhitespaceCollapsedMatchRecord({
                  pageNumber: pageRecord.pageNumber,
                  collapsedStart,
                  collapsedLength: whitespaceCollapsedQuery.length,
                  rawPageText: pageRecord.rawText,
                  queryText: query,
                  contextChars,
                }),
              ),
            }
          })
          .filter(Boolean)
      : []

    return {
      queryId: querySpec.id ?? null,
      query,
      preferredPageNumber,
      sanitizedQuery,
      normalizedQuery,
      pdfjsNormalizedQuery,
      literalPdfjsNormalizedQueryMatches,
      whitespaceCollapsedQuery,
      whitespaceCollapsedMatches,
      finalState: settledControlEvent,
      linkServicePage: linkService.page,
      selected: {
        pageIndex: selectedPageIndex,
        matchIndex: selectedMatchIndex,
      },
      selectedOccurrence,
      matchesTotal,
      matchedPageCount: matchedPages.length,
      matchedPages,
      eventTimeline: [...controlEvents, ...countEvents],
    }
  } finally {
    eventBus._off('updatefindcontrolstate', onControlState)
    eventBus._off('updatefindmatchescount', onMatchesCount)
  }
}

async function buildProbeReport(options) {
  const dom = createDomEnvironment()
  const { pdfjsLib, EventBus, PDFFindController } = await loadPdfJsModules()
  const pdfPath = path.resolve(options.pdfPath)
  const pdfBytes = new Uint8Array(await fs.readFile(pdfPath))
  const pdfDocument = await pdfjsLib.getDocument({
    data: pdfBytes,
    disableFontFace: true,
    useSystemFonts: true,
  }).promise

  try {
    const eventBus = new EventBus()
    const linkService = {
      _page: 1,
      pagesCount: pdfDocument.numPages,
      get page() {
        return this._page
      },
      set page(value) {
        this._page = value
      },
    }
    const findController = new PDFFindController({ linkService, eventBus })
    findController.setDocument(pdfDocument)

    await primeFindController(findController, eventBus, pdfDocument.numPages, options.timeoutMs)

    const allPageRecords = await extractPages(pdfDocument, findController, options.includeTextItems)
    const filteredPages =
      options.pageNumbers.size > 0
        ? allPageRecords.filter((pageRecord) => options.pageNumbers.has(pageRecord.pageNumber))
        : allPageRecords

    const queries = options.queryFile
      ? [...options.querySpecs, ...(await loadQueriesFromFile(options.queryFile))]
      : [...options.querySpecs]

    const queryResults = []
    for (const querySpec of queries) {
      queryResults.push(
        await runFindQuery({
          findController,
          eventBus,
          linkService,
          querySpec,
          pageRecords: allPageRecords,
          contextChars: options.contextChars,
          matchLimit: options.matchLimit,
          timeoutMs: options.timeoutMs,
        }),
      )
    }

    return {
      generatedAt: new Date().toISOString(),
      pdfPath,
      pdfFingerprint: pdfDocument.fingerprints ?? null,
      pdfjsVersion: pdfjsLib.version ?? null,
      pageCount: pdfDocument.numPages,
      options: {
        contextChars: options.contextChars,
        matchLimit: options.matchLimit,
        includeTextItems: options.includeTextItems,
        filteredPages:
          options.pageNumbers.size > 0 ? [...options.pageNumbers].sort((left, right) => left - right) : null,
      },
      pages: filteredPages,
      queries: queryResults,
    }
  } finally {
    await pdfDocument.destroy()
    dom.window.close()
  }
}

async function main() {
  try {
    const options = parseArgs(process.argv.slice(2))
    if (options.help) {
      printUsage()
      return
    }

    const report = await buildProbeReport(options)
    const output = `${JSON.stringify(report, null, 2)}\n`

    if (options.outputPath) {
      const outputPath = path.resolve(options.outputPath)
      await fs.mkdir(path.dirname(outputPath), { recursive: true })
      await fs.writeFile(outputPath, output, 'utf8')
      console.error(`Wrote PDF.js probe report to ${outputPath}`)
      return
    }

    process.stdout.write(output)
  } catch (error) {
    fail(error instanceof Error ? error.stack ?? error.message : String(error))
  }
}

export {
  buildNormalizedTextSourceMap,
  buildProbeReport,
  normalizeTextForEvidenceMatch,
  sanitizeEvidenceSearchText,
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  await main()
}

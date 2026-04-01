#!/usr/bin/env node

import fs from 'node:fs/promises'
import path from 'node:path'

import {
  buildNormalizedTextSourceMap,
  buildProbeReport,
  normalizeTextForEvidenceMatch,
  sanitizeEvidenceSearchText,
} from './pdfjs_find_probe.mjs'

const DEFAULT_SAMPLE_SIZE = 30
const DEFAULT_MIN_CHARS = 90
const DEFAULT_MAX_CHARS = 320
const DEFAULT_MAX_QUOTES_PER_CHUNK = 2
const DEFAULT_CONTEXT_CHARS = 120
const DEFAULT_MATCH_LIMIT = 10
const DEFAULT_TIMEOUT_MS = 5000
const WORD_PATTERN = /[a-z0-9]+/g
const COMMON_FUNCTION_WORDS = new Set([
  'a',
  'an',
  'and',
  'are',
  'as',
  'at',
  'be',
  'by',
  'for',
  'from',
  'in',
  'into',
  'is',
  'of',
  'on',
  'or',
  'that',
  'the',
  'their',
  'these',
  'this',
  'those',
  'to',
  'was',
  'were',
  'which',
  'with',
])

const DEFAULT_INCLUDED_TOP_LEVEL_SECTIONS = new Set([
  'Introduction',
  'Methods',
  'Results and Discussion',
  'Conclusions',
])

const DEFAULT_EXCLUDED_TOP_LEVEL_SECTIONS = new Set([
  'TITLE',
  'Acknowledgements',
  'Keywords',
  'Supplementary',
  'Supporting Information',
])

function printUsage() {
  console.log(`Usage:
  node scripts/utilities/pdfjs_quote_benchmark.mjs --pdf <path> [chunk source options] [options]

Chunk source options:
  --backend-url <url>       Backend base URL, e.g. http://10.222.162.167:8900
  --document-id <id>        Document id used with --backend-url
  --chunks-file <path>      Local chunk JSON file from /weaviate/documents/{id}/chunks

Sampling options:
  --sample-size <count>     Number of benchmark quotes to evaluate (default: ${DEFAULT_SAMPLE_SIZE})
  --min-chars <count>       Minimum quote length after whitespace collapse (default: ${DEFAULT_MIN_CHARS})
  --max-chars <count>       Maximum quote length after whitespace collapse (default: ${DEFAULT_MAX_CHARS})
  --max-quotes-per-chunk    Max sampled quotes from one chunk (default: ${DEFAULT_MAX_QUOTES_PER_CHUNK})
  --include-section <name>  Include a top-level section (repeatable)
  --exclude-section <name>  Exclude a top-level section (repeatable)

Probe options:
  --context <chars>         Context chars for probe output (default: ${DEFAULT_CONTEXT_CHARS})
  --match-limit <count>     Max matches retained per query (default: ${DEFAULT_MATCH_LIMIT})
  --timeout-ms <ms>         PDF.js settle timeout (default: ${DEFAULT_TIMEOUT_MS})

Output:
  --output <path>           Write JSON report to a file instead of stdout
  --help                    Show this help

Example:
  node scripts/utilities/pdfjs_quote_benchmark.mjs \\
    --pdf /home/ctabone/analysis/alliance/ai_curation_new/agr_ai_curation/sample_fly_publication.pdf \\
    --backend-url http://10.222.162.167:8900 \\
    --document-id 64fa682e-a074-446c-821e-c4a605d102f0 \\
    --sample-size 30 \\
    --output /tmp/pdf-quote-benchmark.json`)
}

function fail(message) {
  console.error(message)
  process.exitCode = 1
}

function parseArgs(argv) {
  const options = {
    pdfPath: null,
    backendUrl: null,
    documentId: null,
    chunksFile: null,
    sampleSize: DEFAULT_SAMPLE_SIZE,
    minChars: DEFAULT_MIN_CHARS,
    maxChars: DEFAULT_MAX_CHARS,
    maxQuotesPerChunk: DEFAULT_MAX_QUOTES_PER_CHUNK,
    includeSections: new Set(),
    excludeSections: new Set(),
    contextChars: DEFAULT_CONTEXT_CHARS,
    matchLimit: DEFAULT_MATCH_LIMIT,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    outputPath: null,
  }

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]

    if (arg === '--help') {
      options.help = true
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
      case '--backend-url':
        options.backendUrl = next.replace(/\/+$/, '')
        index += 1
        break
      case '--document-id':
        options.documentId = next
        index += 1
        break
      case '--chunks-file':
        options.chunksFile = next
        index += 1
        break
      case '--sample-size':
        options.sampleSize = Number.parseInt(next, 10)
        index += 1
        break
      case '--min-chars':
        options.minChars = Number.parseInt(next, 10)
        index += 1
        break
      case '--max-chars':
        options.maxChars = Number.parseInt(next, 10)
        index += 1
        break
      case '--max-quotes-per-chunk':
        options.maxQuotesPerChunk = Number.parseInt(next, 10)
        index += 1
        break
      case '--include-section':
        options.includeSections.add(next)
        index += 1
        break
      case '--exclude-section':
        options.excludeSections.add(next)
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
      case '--timeout-ms':
        options.timeoutMs = Number.parseInt(next, 10)
        index += 1
        break
      case '--output':
        options.outputPath = next
        index += 1
        break
      default:
        throw new Error(`Unknown argument: ${arg}`)
    }
  }

  if (!options.help && !options.pdfPath) {
    throw new Error('Missing required --pdf argument')
  }

  if (!options.help && !options.chunksFile && !(options.backendUrl && options.documentId)) {
    throw new Error('Provide either --chunks-file or both --backend-url and --document-id')
  }

  const integerOptions = [
    ['sampleSize', options.sampleSize, 1],
    ['minChars', options.minChars, 20],
    ['maxChars', options.maxChars, options.minChars],
    ['maxQuotesPerChunk', options.maxQuotesPerChunk, 1],
    ['contextChars', options.contextChars, 0],
    ['matchLimit', options.matchLimit, 1],
    ['timeoutMs', options.timeoutMs, 100],
  ]

  for (const [name, value, minimum] of integerOptions) {
    if (!Number.isInteger(value) || value < minimum) {
      throw new Error(`Invalid --${name.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`)} value: ${value}`)
    }
  }

  return options
}

async function fetchJson(url) {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}) for ${url}`)
  }
  return response.json()
}

async function loadChunks(options) {
  if (options.chunksFile) {
    const data = JSON.parse(await fs.readFile(path.resolve(options.chunksFile), 'utf8'))
    return {
      documentId: data.document_id ?? options.documentId ?? null,
      chunks: Array.isArray(data.chunks) ? data.chunks : [],
      pagination: data.pagination ?? null,
      source: {
        type: 'file',
        path: path.resolve(options.chunksFile),
      },
    }
  }

  const pageSize = 100
  let page = 1
  let totalPages = 1
  const chunks = []

  do {
    const url = `${options.backendUrl}/weaviate/documents/${options.documentId}/chunks?page=${page}&page_size=${pageSize}&include_metadata=true`
    const payload = await fetchJson(url)
    chunks.push(...(Array.isArray(payload.chunks) ? payload.chunks : []))
    totalPages = payload.pagination?.total_pages ?? 1
    page += 1
  } while (page <= totalPages)

  return {
    documentId: options.documentId,
    chunks,
    pagination: {
      total_pages: totalPages,
      page_size: pageSize,
      total_items: chunks.length,
    },
    source: {
      type: 'backend',
      backendUrl: options.backendUrl,
      documentId: options.documentId,
    },
  }
}

function collapseWhitespace(value) {
  return value.replace(/\s+/g, ' ').trim()
}

function topLevelSection(chunk) {
  const sectionPath = chunk?.metadata?.section_path
  if (Array.isArray(sectionPath) && sectionPath.length > 0) {
    return String(sectionPath[0])
  }
  if (typeof chunk?.section_title === 'string' && chunk.section_title.trim()) {
    return chunk.section_title.trim()
  }
  return null
}

function detailedSectionPath(chunk) {
  const sectionPath = chunk?.metadata?.section_path
  if (Array.isArray(sectionPath) && sectionPath.length > 0) {
    return sectionPath.map((value) => String(value))
  }
  const top = topLevelSection(chunk)
  return top ? [top] : []
}

function shouldKeepChunk(chunk, options) {
  if (chunk?.element_type !== 'NarrativeText') {
    return false
  }

  const topLevel = topLevelSection(chunk)
  if (!topLevel) {
    return false
  }

  if (options.includeSections.size > 0 && !options.includeSections.has(topLevel)) {
    return false
  }

  if (options.excludeSections.has(topLevel)) {
    return false
  }

  if (DEFAULT_EXCLUDED_TOP_LEVEL_SECTIONS.has(topLevel)) {
    return false
  }

  if (options.includeSections.size === 0 && !DEFAULT_INCLUDED_TOP_LEVEL_SECTIONS.has(topLevel)) {
    return false
  }

  return true
}

function splitIntoSentenceLikeUnits(text) {
  const compact = collapseWhitespace(text)
  if (!compact) {
    return []
  }

  const pieces = compact.split(/(?<=[.!?])\s+(?=(?:["'“”‘’(\[])?[A-Z0-9*])/u)
  return pieces
    .map((piece) => piece.trim())
    .filter(Boolean)
}

function looksReferenceLike(text) {
  const compact = collapseWhitespace(text)
  if (!compact) {
    return true
  }

  const lower = compact.toLowerCase()
  const citationCount = (compact.match(/\[[0-9,\-–]+\]/g) ?? []).length
  const yearCount = (compact.match(/\b(?:19|20)\d{2}\b/g) ?? []).length
  const commaCount = (compact.match(/,/g) ?? []).length
  const urlLike = /https?:\/\/|www\./i.test(compact)
  const tooManyAuthors = /(?:[A-Z]\.\s*){2,}[A-Z][a-z]+/.test(compact) && commaCount >= 6
  const boilerplatePatterns = [
    'creative commons',
    'open access article',
    'wiley-vch',
    'e-mail:',
    'department of neurology',
    "children's hospital",
    'biorender',
    'supporting information',
    'projekt deal',
    'conflict of interest',
    'author contributions',
  ]

  return (
    urlLike
    || citationCount >= 6
    || yearCount >= 4
    || tooManyAuthors
    || compact.includes('©')
    || boilerplatePatterns.some((pattern) => lower.includes(pattern))
  )
}

function sentenceWindowScore(text, chunk) {
  const compact = collapseWhitespace(text)
  const words = compact.split(/\s+/).filter(Boolean)
  const lengthTarget = 180
  const lengthPenalty = Math.abs(compact.length - lengthTarget)
  const citationCount = (compact.match(/\[[0-9,\-–]+\]/g) ?? []).length
  const markdownPenalty = (compact.match(/[*_`]/g) ?? []).length
  const startsWell = /^[A-Z0-9"'“‘(]/.test(compact) ? 20 : 0
  const endsWell = /[.!?)]$/.test(compact) ? 15 : 0
  const section = topLevelSection(chunk)
  const sectionBonus =
    section === 'Results and Discussion' ? 30 : section === 'Methods' ? 20 : section === 'Introduction' ? 10 : 5

  return sectionBonus + startsWell + endsWell + Math.max(0, 120 - lengthPenalty) - citationCount * 8 - markdownPenalty * 2 - Math.max(0, words.length - 55)
}

function buildChunkQuoteCandidates(chunk, options) {
  const units = splitIntoSentenceLikeUnits(chunk.content ?? '')
  const candidates = []

  for (let startIndex = 0; startIndex < units.length; startIndex += 1) {
    let merged = ''
    for (let endIndex = startIndex; endIndex < units.length && endIndex < startIndex + 3; endIndex += 1) {
      merged = merged ? `${merged} ${units[endIndex]}` : units[endIndex]
      const claimedQuote = collapseWhitespace(merged)
      const wordCount = claimedQuote.split(/\s+/).filter(Boolean).length

      if (claimedQuote.length < options.minChars || claimedQuote.length > options.maxChars) {
        continue
      }
      if (wordCount < 10 || wordCount > 60) {
        continue
      }
      if (!/^[A-Z0-9"'“‘(]/.test(claimedQuote)) {
        continue
      }
      if (!/[.!?)]$/.test(claimedQuote)) {
        continue
      }
      if (looksReferenceLike(claimedQuote)) {
        continue
      }
      if (/[a-z][A-Z]/.test(claimedQuote)) {
        continue
      }

      candidates.push({
        chunkId: chunk.id,
        chunkIndex: chunk.chunk_index,
        elementType: chunk.element_type,
        pageNumber: chunk.page_number,
        sectionPath: detailedSectionPath(chunk),
        topLevelSection: topLevelSection(chunk),
        claimedQuote,
        sentenceCount: endIndex - startIndex + 1,
        wordCount,
        characterCount: claimedQuote.length,
        score: sentenceWindowScore(claimedQuote, chunk),
        sourceTextPreview: collapseWhitespace(chunk.content ?? '').slice(0, 220),
      })
    }
  }

  candidates.sort((left, right) => right.score - left.score || left.chunkIndex - right.chunkIndex)
  return candidates.slice(0, options.maxQuotesPerChunk)
}

function selectBenchmarkQuotes(chunks, options) {
  const bySection = new Map()

  for (const chunk of chunks) {
    if (!shouldKeepChunk(chunk, options)) {
      continue
    }
    for (const candidate of buildChunkQuoteCandidates(chunk, options)) {
      const key = candidate.topLevelSection ?? 'Unknown'
      if (!bySection.has(key)) {
        bySection.set(key, [])
      }
      bySection.get(key).push(candidate)
    }
  }

  for (const candidates of bySection.values()) {
    candidates.sort((left, right) => right.score - left.score || left.chunkIndex - right.chunkIndex)
  }

  const sectionNames = [...bySection.keys()].sort()
  const selected = []
  const usedChunkIds = new Map()

  while (selected.length < options.sampleSize) {
    let addedThisRound = false

    for (const sectionName of sectionNames) {
      const candidates = bySection.get(sectionName)
      if (!candidates || candidates.length === 0) {
        continue
      }

      const nextIndex = candidates.findIndex((candidate) => (usedChunkIds.get(candidate.chunkId) ?? 0) < options.maxQuotesPerChunk)
      if (nextIndex < 0) {
        continue
      }

      const [candidate] = candidates.splice(nextIndex, 1)
      usedChunkIds.set(candidate.chunkId, (usedChunkIds.get(candidate.chunkId) ?? 0) + 1)
      selected.push(candidate)
      addedThisRound = true

      if (selected.length >= options.sampleSize) {
        break
      }
    }

    if (!addedThisRound) {
      break
    }
  }

  return selected
}

function tokenizeWordSpans(text) {
  const tokens = []
  let match = null
  WORD_PATTERN.lastIndex = 0
  while ((match = WORD_PATTERN.exec(text)) !== null) {
    tokens.push({
      token: match[0],
      start: match.index,
      end: match.index + match[0].length,
    })
  }
  return tokens
}

function buildTokenCounter(tokens) {
  const counter = new Map()
  for (const token of tokens) {
    counter.set(token, (counter.get(token) ?? 0) + 1)
  }
  return counter
}

function buildTokenPositionIndex(tokens) {
  const index = new Map()
  tokens.forEach((token, position) => {
    if (!index.has(token.token)) {
      index.set(token.token, [])
    }
    index.get(token.token).push(position)
  })
  return index
}

function counterOverlap(left, right) {
  let overlap = 0
  for (const [token, leftCount] of left.entries()) {
    overlap += Math.min(leftCount, right.get(token) ?? 0)
  }
  return overlap
}

function buildCharacterNgrams(value, size = 3) {
  const compact = value.replace(/\s+/g, '')
  if (compact.length === 0) {
    return new Set()
  }
  if (compact.length <= size) {
    return new Set([compact])
  }
  const grams = new Set()
  for (let index = 0; index <= compact.length - size; index += 1) {
    grams.add(compact.slice(index, index + size))
  }
  return grams
}

function diceCoefficient(leftSet, rightSet) {
  if (leftSet.size === 0 && rightSet.size === 0) {
    return 1
  }
  if (leftSet.size === 0 || rightSet.size === 0) {
    return 0
  }
  let intersection = 0
  for (const value of leftSet) {
    if (rightSet.has(value)) {
      intersection += 1
    }
  }
  return (2 * intersection) / (leftSet.size + rightSet.size)
}

function sampleCounterDifference(leftCounter, rightCounter, limit = 8) {
  const values = []
  for (const [token, count] of leftCounter.entries()) {
    const diff = count - (rightCounter.get(token) ?? 0)
    for (let index = 0; index < Math.max(0, diff) && values.length < limit; index += 1) {
      values.push(token)
    }
    if (values.length >= limit) {
      break
    }
  }
  return values
}

function firstMismatchDetail(left, right, radius = 60) {
  const shortest = Math.min(left.length, right.length)
  let mismatchIndex = -1
  for (let index = 0; index < shortest; index += 1) {
    if (left[index] !== right[index]) {
      mismatchIndex = index
      break
    }
  }

  if (mismatchIndex < 0 && left.length !== right.length) {
    mismatchIndex = shortest
  }

  if (mismatchIndex < 0) {
    return null
  }

  return {
    index: mismatchIndex,
    queryPreview: left.slice(Math.max(0, mismatchIndex - radius), mismatchIndex + radius),
    candidatePreview: right.slice(Math.max(0, mismatchIndex - radius), mismatchIndex + radius),
  }
}

function buildTokenEditScript(leftTokens, rightTokens) {
  const leftLength = leftTokens.length
  const rightLength = rightTokens.length
  const dp = Array.from({ length: leftLength + 1 }, () => Array(rightLength + 1).fill(0))

  for (let leftIndex = 0; leftIndex <= leftLength; leftIndex += 1) {
    dp[leftIndex][0] = leftIndex
  }
  for (let rightIndex = 0; rightIndex <= rightLength; rightIndex += 1) {
    dp[0][rightIndex] = rightIndex
  }

  for (let leftIndex = 1; leftIndex <= leftLength; leftIndex += 1) {
    for (let rightIndex = 1; rightIndex <= rightLength; rightIndex += 1) {
      if (leftTokens[leftIndex - 1] === rightTokens[rightIndex - 1]) {
        dp[leftIndex][rightIndex] = dp[leftIndex - 1][rightIndex - 1]
        continue
      }
      dp[leftIndex][rightIndex] = Math.min(
        dp[leftIndex - 1][rightIndex] + 1,
        dp[leftIndex][rightIndex - 1] + 1,
        dp[leftIndex - 1][rightIndex - 1] + 1,
      )
    }
  }

  const operations = []
  let leftIndex = leftLength
  let rightIndex = rightLength

  while (leftIndex > 0 || rightIndex > 0) {
    if (
      leftIndex > 0
      && rightIndex > 0
      && leftTokens[leftIndex - 1] === rightTokens[rightIndex - 1]
      && dp[leftIndex][rightIndex] === dp[leftIndex - 1][rightIndex - 1]
    ) {
      operations.push({
        type: 'equal',
        left: leftTokens[leftIndex - 1],
        right: rightTokens[rightIndex - 1],
      })
      leftIndex -= 1
      rightIndex -= 1
      continue
    }

    if (
      leftIndex > 0
      && rightIndex > 0
      && dp[leftIndex][rightIndex] === dp[leftIndex - 1][rightIndex - 1] + 1
    ) {
      operations.push({
        type: 'replace',
        left: leftTokens[leftIndex - 1],
        right: rightTokens[rightIndex - 1],
      })
      leftIndex -= 1
      rightIndex -= 1
      continue
    }

    if (leftIndex > 0 && dp[leftIndex][rightIndex] === dp[leftIndex - 1][rightIndex] + 1) {
      operations.push({
        type: 'delete',
        left: leftTokens[leftIndex - 1],
        right: null,
      })
      leftIndex -= 1
      continue
    }

    operations.push({
      type: 'insert',
      left: null,
      right: rightTokens[rightIndex - 1],
    })
    rightIndex -= 1
  }

  operations.reverse()

  const summary = {
    editDistance: dp[leftLength][rightLength],
    equalTokenCount: 0,
    replacedPairs: [],
    insertedTokens: [],
    deletedTokens: [],
  }

  for (const operation of operations) {
    if (operation.type === 'equal') {
      summary.equalTokenCount += 1
      continue
    }
    if (operation.type === 'replace') {
      summary.replacedPairs.push({
        from: operation.left,
        to: operation.right,
      })
      continue
    }
    if (operation.type === 'insert') {
      summary.insertedTokens.push(operation.right)
      continue
    }
    if (operation.type === 'delete') {
      summary.deletedTokens.push(operation.left)
    }
  }

  return summary
}

function canonicalizeLooseText(value) {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[*_`]/g, '')
    .replace(/[‐‑‒–—−]/g, '-')
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function canonicalizeWithoutWhitespace(value) {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/\s+/g, '')
}

function collectDifferenceSignals(queryText, candidateText, diagnostics, tokenDiff) {
  const signals = new Set()
  const rawQuery = collapseWhitespace(queryText)
  const rawCandidate = collapseWhitespace(candidateText)
  const looseQuery = canonicalizeLooseText(rawQuery)
  const looseCandidate = canonicalizeLooseText(rawCandidate)
  const compactQuery = canonicalizeWithoutWhitespace(rawQuery)
  const compactCandidate = canonicalizeWithoutWhitespace(rawCandidate)

  if (diagnostics.boundaryOnlyFailure) {
    signals.add('identifier_or_token_boundary_collapse')
  }
  if (diagnostics.recoveredBySanitizedOrNormalized) {
    signals.add('markdown_or_search_wrapper_removed')
  }
  if (tokenDiff.editDistance === 0 && rawQuery !== rawCandidate) {
    signals.add('token_content_equal_but_spacing_or_punctuation_blocks_literal_match')
  }
  if (looseQuery === looseCandidate && rawQuery !== rawCandidate) {
    signals.add('punctuation_case_or_symbol_only')
  }
  if (compactQuery === compactCandidate && rawQuery !== rawCandidate) {
    signals.add('whitespace_sensitive_but_compact_equal')
  }
  if (/[‐‑‒–—−]/.test(rawCandidate) || /[‐‑‒–—−]/.test(rawQuery)) {
    if (rawQuery.replace(/[‐‑‒–—−]/g, '-') === rawCandidate.replace(/[‐‑‒–—−]/g, '-')) {
      signals.add('dash_variant')
    }
  }
  if (
    tokenDiff.replacedPairs.some(
      (pair) => pair.from.length >= 4 && pair.to.length >= 4 && pair.from.startsWith(pair.to.slice(0, 4)),
    )
  ) {
    signals.add('spelling_variant_or_inflection')
  }
  if (
    tokenDiff.insertedTokens.length > 0
    && tokenDiff.insertedTokens.every((token) => COMMON_FUNCTION_WORDS.has(token))
    && tokenDiff.deletedTokens.length === 0
  ) {
    signals.add('extra_function_words_in_pdf')
  }
  if (
    tokenDiff.deletedTokens.length > 0
    && tokenDiff.deletedTokens.every((token) => COMMON_FUNCTION_WORDS.has(token))
    && tokenDiff.insertedTokens.length === 0
  ) {
    signals.add('missing_function_words_in_pdf')
  }
  if (tokenDiff.replacedPairs.some((pair) => pair.from !== pair.to)) {
    signals.add('lexical_substitution')
  }

  return [...signals].sort()
}

function buildPageCorpora(pageRecords) {
  return pageRecords.map((pageRecord) => {
    const normalizedMap = buildNormalizedTextSourceMap(pageRecord.pdfjsSearchText ?? '')
    const tokens = tokenizeWordSpans(normalizedMap.text)
    return {
      pageNumber: pageRecord.pageNumber,
      rawText: pageRecord.pdfjsSearchText ?? '',
      normalizedMap,
      tokens,
      tokenCounter: buildTokenCounter(tokens.map((token) => token.token)),
      tokenPositions: buildTokenPositionIndex(tokens),
    }
  })
}

function buildRawSliceFromNormalizedRange(pageCorpus, normalizedStart, normalizedEndExclusive) {
  if (normalizedStart < 0 || normalizedEndExclusive <= normalizedStart) {
    return null
  }
  const sourceIndices = pageCorpus.normalizedMap.sourceIndices
  const rawStart = sourceIndices[normalizedStart]
  const rawEnd = sourceIndices[normalizedEndExclusive - 1] + 1
  return {
    rawStart,
    rawEndExclusive: rawEnd,
    rawSlice: pageCorpus.rawText.slice(rawStart, rawEnd),
  }
}

function findNearestCandidates(queryText, pageCorpora, topN = 3) {
  const normalizedQuery = normalizeTextForEvidenceMatch(queryText)
  const queryTokens = tokenizeWordSpans(normalizedQuery)
  if (queryTokens.length === 0) {
    return []
  }

  const queryTokenStrings = queryTokens.map((token) => token.token)
  const queryCounter = buildTokenCounter(queryTokenStrings)
  const queryNgrams = buildCharacterNgrams(normalizedQuery)
  const queryTokenCount = queryTokens.length
  const minWindow = Math.max(4, queryTokenCount - 4)
  const maxWindow = queryTokenCount + Math.max(4, Math.ceil(queryTokenCount * 0.2))

  const evaluated = new Map()
  const candidates = []

  for (const pageCorpus of pageCorpora) {
    if (pageCorpus.tokens.length === 0) {
      continue
    }

    const anchorStarts = new Set()
    queryTokens.forEach((queryToken, queryIndex) => {
      if (queryToken.token.length < 3) {
        return
      }
      const pagePositions = pageCorpus.tokenPositions.get(queryToken.token) ?? []
      pagePositions.slice(0, 20).forEach((pagePosition) => {
        const baseStart = Math.max(0, pagePosition - queryIndex)
        for (let delta = -2; delta <= 2; delta += 1) {
          const candidateStart = baseStart + delta
          if (candidateStart >= 0 && candidateStart < pageCorpus.tokens.length) {
            anchorStarts.add(candidateStart)
          }
        }
      })
    })

    if (anchorStarts.size === 0) {
      const stride = Math.max(1, Math.floor(queryTokenCount / 2))
      for (let position = 0; position < pageCorpus.tokens.length; position += stride) {
        anchorStarts.add(position)
      }
    }

    for (const startTokenIndex of anchorStarts) {
      for (let windowSize = minWindow; windowSize <= maxWindow; windowSize += 1) {
        const endTokenIndex = startTokenIndex + windowSize
        if (endTokenIndex > pageCorpus.tokens.length) {
          continue
        }

        const dedupeKey = `${pageCorpus.pageNumber}:${startTokenIndex}:${windowSize}`
        if (evaluated.has(dedupeKey)) {
          continue
        }
        evaluated.set(dedupeKey, true)

        const windowTokens = pageCorpus.tokens.slice(startTokenIndex, endTokenIndex)
        const normalizedStart = windowTokens[0].start
        const normalizedEndExclusive = windowTokens[windowTokens.length - 1].end
        const normalizedCandidate = pageCorpus.normalizedMap.text.slice(normalizedStart, normalizedEndExclusive)
        const rawSliceRecord = buildRawSliceFromNormalizedRange(pageCorpus, normalizedStart, normalizedEndExclusive)
        if (!rawSliceRecord) {
          continue
        }

        const candidateTokenStrings = windowTokens.map((token) => token.token)
        const candidateCounter = buildTokenCounter(candidateTokenStrings)
        const overlap = counterOverlap(queryCounter, candidateCounter)
        const sharedTokenRatio = overlap / queryTokenCount
        const candidateNgrams = buildCharacterNgrams(normalizedCandidate)
        const charNgramDice = diceCoefficient(queryNgrams, candidateNgrams)
        const lengthPenalty = Math.abs(windowTokens.length - queryTokenCount) / Math.max(queryTokenCount, 1)
        const score = sharedTokenRatio * 0.7 + charNgramDice * 0.3 - lengthPenalty * 0.05

        candidates.push({
          pageNumber: pageCorpus.pageNumber,
          startTokenIndex,
          windowTokenCount: windowTokens.length,
          normalizedStart,
          normalizedEndExclusive,
          normalizedCandidate,
          rawSlice: rawSliceRecord.rawSlice,
          rawSlicePreview: rawSliceRecord.rawSlice.slice(0, 260),
          score,
          sharedTokenRatio,
          charNgramDice,
          overlapCount: overlap,
          missingQueryWords: sampleCounterDifference(queryCounter, candidateCounter),
          extraCandidateWords: sampleCounterDifference(candidateCounter, queryCounter),
          firstMismatch: firstMismatchDetail(normalizedQuery, normalizedCandidate),
        })
      }
    }
  }

  return candidates.sort((left, right) => right.score - left.score).slice(0, topN)
}

function classifyFailure(diagnostics, nearestCandidates) {
  if (diagnostics.boundaryOnlyFailure) {
    return 'boundary_whitespace_drift'
  }
  if (diagnostics.recoveredBySanitizedOrNormalized) {
    return 'markdown_or_wrapper_drift'
  }

  const bestCandidate = nearestCandidates[0]
  if (!bestCandidate) {
    return 'no_nearby_candidate'
  }
  if (bestCandidate.sharedTokenRatio >= 0.9 && bestCandidate.charNgramDice >= 0.9) {
    return 'punctuation_or_formatting_drift'
  }
  if (bestCandidate.sharedTokenRatio >= 0.75) {
    return 'high_overlap_text_drift'
  }
  if (bestCandidate.sharedTokenRatio >= 0.5) {
    return 'partial_overlap_or_chunk_rewrite'
  }
  return 'low_overlap_or_wrong_quote'
}

function analyzeFailure(quote, diagnostics, pageCorpora) {
  if (
    diagnostics.literalPresent
    && !diagnostics.recoveredBySanitizedOrNormalized
    && !diagnostics.boundaryOnlyFailure
  ) {
    return null
  }

  const nearestCandidates = findNearestCandidates(quote.claimedQuote, pageCorpora, 3)
  const bestCandidate = nearestCandidates[0] ?? null
  const tokenDiff =
    bestCandidate
      ? buildTokenEditScript(
        tokenizeWordSpans(normalizeTextForEvidenceMatch(quote.claimedQuote)).map((token) => token.token),
        tokenizeWordSpans(normalizeTextForEvidenceMatch(bestCandidate.rawSlice)).map((token) => token.token),
      )
      : null
  return {
    classification: classifyFailure(diagnostics, nearestCandidates),
    nearestCandidates,
    differenceSignals:
      bestCandidate && tokenDiff
        ? collectDifferenceSignals(quote.claimedQuote, bestCandidate.rawSlice, diagnostics, tokenDiff)
        : [],
    tokenDiff,
  }
}

function summarizeBenchmark(sampledQuotes, probeQueries, pageCorpora) {
  const results = sampledQuotes.map((quote, index) => {
    const variants = probeQueries[index]
    const probe = variants.raw
    const exactFound = (probe.matchesTotal ?? 0) > 0 && ['FOUND', 'WRAPPED'].includes(probe.finalState?.stateName ?? '')
    const literalPresent = (probe.literalPdfjsNormalizedQueryMatches?.length ?? 0) > 0
    const whitespaceCollapsedPresent = (probe.whitespaceCollapsedMatches?.length ?? 0) > 0
    const collapsedOccurrence = probe.whitespaceCollapsedMatches?.[0]?.occurrences?.[0] ?? null
    const boundaryAlignment = collapsedOccurrence?.boundaryAlignment ?? null
    const boundaryOnlyFailure =
      !exactFound
      && !literalPresent
      && whitespaceCollapsedPresent
      && boundaryAlignment?.compactTextsEqual === true
    const selectedOffsetDelta =
      exactFound
      && probe.selectedOccurrence
      && probe.literalPdfjsNormalizedQueryMatches?.[0]
      && probe.literalPdfjsNormalizedQueryMatches[0].pageNumber === probe.selectedOccurrence.pageNumber
      && probe.literalPdfjsNormalizedQueryMatches[0].indices?.length
        ? probe.selectedOccurrence.rawStart - probe.literalPdfjsNormalizedQueryMatches[0].indices[0]
        : null
    const sanitizedLiteralPresent = (variants.sanitized?.literalPdfjsNormalizedQueryMatches?.length ?? 0) > 0
    const normalizedLiteralPresent = (variants.normalized?.literalPdfjsNormalizedQueryMatches?.length ?? 0) > 0
    const sanitizedFound =
      (variants.sanitized?.matchesTotal ?? 0) > 0
      && ['FOUND', 'WRAPPED'].includes(variants.sanitized?.finalState?.stateName ?? '')
    const normalizedFound =
      (variants.normalized?.matchesTotal ?? 0) > 0
      && ['FOUND', 'WRAPPED'].includes(variants.normalized?.finalState?.stateName ?? '')
    const recoveredBySanitizedOrNormalized =
      !literalPresent && (sanitizedLiteralPresent || normalizedLiteralPresent)
    const failureAnalysis = analyzeFailure(
      quote,
      {
        exactFound,
        literalPresent,
        whitespaceCollapsedPresent,
        boundaryOnlyFailure,
        recoveredBySanitizedOrNormalized,
      },
      pageCorpora,
    )

    return {
      benchmarkIndex: index,
      quote,
      probeVariants: variants,
      diagnostics: {
        exactFound,
        literalPresent,
        whitespaceCollapsedPresent,
        boundaryOnlyFailure,
        selectedOffsetDelta,
        sanitizedFound,
        sanitizedLiteralPresent,
        normalizedFound,
        normalizedLiteralPresent,
        recoveredBySanitizedOrNormalized,
        queryWhitespaceCollapseRate: boundaryAlignment?.queryWhitespaceCollapseRate ?? null,
        allQueryWhitespaceBoundariesCollapsed: boundaryAlignment?.allQueryWhitespaceBoundariesCollapsed ?? null,
        failureAnalysis,
      },
    }
  })

  const summary = {
    totalQuotes: results.length,
    pdfjsControllerFound: 0,
    literalPresent: 0,
    sanitizedLiteralPresent: 0,
    normalizedLiteralPresent: 0,
    recoveredBySanitizedOrNormalized: 0,
    whitespaceCollapsedPresent: 0,
    boundaryOnlyFailures: 0,
    hardFailures: 0,
    controllerFoundWithoutLiteral: 0,
    offsetSliceMismatchCount: 0,
    averageQueryWhitespaceCollapseRate: null,
    failureClassifications: {},
    differenceSignals: {},
    replacementPairs: {},
    insertedTokens: {},
    deletedTokens: {},
  }

  const collapseRates = []

  for (const result of results) {
    if (result.diagnostics.exactFound) {
      summary.pdfjsControllerFound += 1
    }
    if (result.diagnostics.literalPresent) {
      summary.literalPresent += 1
    }
    if (result.diagnostics.sanitizedLiteralPresent) {
      summary.sanitizedLiteralPresent += 1
    }
    if (result.diagnostics.normalizedLiteralPresent) {
      summary.normalizedLiteralPresent += 1
    }
    if (result.diagnostics.recoveredBySanitizedOrNormalized) {
      summary.recoveredBySanitizedOrNormalized += 1
    }
    if (result.diagnostics.whitespaceCollapsedPresent) {
      summary.whitespaceCollapsedPresent += 1
    }
    if (result.diagnostics.boundaryOnlyFailure) {
      summary.boundaryOnlyFailures += 1
    }
    if (result.diagnostics.exactFound && !result.diagnostics.literalPresent) {
      summary.controllerFoundWithoutLiteral += 1
    }
    if (!result.diagnostics.exactFound && !result.diagnostics.literalPresent && !result.diagnostics.whitespaceCollapsedPresent) {
      summary.hardFailures += 1
    }
    if (typeof result.diagnostics.selectedOffsetDelta === 'number' && result.diagnostics.selectedOffsetDelta !== 0) {
      summary.offsetSliceMismatchCount += 1
    }
    if (typeof result.diagnostics.queryWhitespaceCollapseRate === 'number') {
      collapseRates.push(result.diagnostics.queryWhitespaceCollapseRate)
    }
    const classification = result.diagnostics.failureAnalysis?.classification
    if (classification) {
      summary.failureClassifications[classification] = (summary.failureClassifications[classification] ?? 0) + 1
    }
    const differenceSignals = result.diagnostics.failureAnalysis?.differenceSignals ?? []
    for (const signal of differenceSignals) {
      summary.differenceSignals[signal] = (summary.differenceSignals[signal] ?? 0) + 1
    }
    const tokenDiff = result.diagnostics.failureAnalysis?.tokenDiff
    if (tokenDiff) {
      for (const pair of tokenDiff.replacedPairs) {
        const key = `${pair.from} -> ${pair.to}`
        summary.replacementPairs[key] = (summary.replacementPairs[key] ?? 0) + 1
      }
      for (const token of tokenDiff.insertedTokens) {
        summary.insertedTokens[token] = (summary.insertedTokens[token] ?? 0) + 1
      }
      for (const token of tokenDiff.deletedTokens) {
        summary.deletedTokens[token] = (summary.deletedTokens[token] ?? 0) + 1
      }
    }
  }

  if (collapseRates.length > 0) {
    summary.averageQueryWhitespaceCollapseRate =
      collapseRates.reduce((sum, value) => sum + value, 0) / collapseRates.length
  }

  return {
    summary,
    results,
    examples: {
      exactFound: results.filter((entry) => entry.diagnostics.exactFound).slice(0, 5),
      boundaryOnlyFailures: results.filter((entry) => entry.diagnostics.boundaryOnlyFailure).slice(0, 5),
      hardFailures: results
        .filter(
          (entry) =>
            !entry.diagnostics.exactFound
            && !entry.diagnostics.literalPresent
            && !entry.diagnostics.whitespaceCollapsedPresent,
        )
        .slice(0, 5),
    },
  }
}

async function buildBenchmarkReport(options) {
  const chunkPayload = await loadChunks(options)
  const sampledQuotes = selectBenchmarkQuotes(chunkPayload.chunks, options)
  if (sampledQuotes.length === 0) {
    throw new Error('No benchmark quotes were selected. Try loosening section or length filters.')
  }

  const querySpecs = sampledQuotes.map((quote) => {
    const raw = quote.claimedQuote
    const sanitized = sanitizeEvidenceSearchText(raw)
    const normalized = normalizeTextForEvidenceMatch(sanitized)
    return {
      raw,
      sanitized: sanitized !== raw ? sanitized : null,
      normalized: normalized && normalized !== sanitized ? normalized : null,
    }
  })

  const flattenedQueries = querySpecs.flatMap((spec) => [
    spec.raw,
    ...(spec.sanitized ? [spec.sanitized] : []),
    ...(spec.normalized ? [spec.normalized] : []),
  ])

  const probeReport = await buildProbeReport({
    pdfPath: options.pdfPath,
    queries: flattenedQueries,
    pageNumbers: new Set(),
    contextChars: options.contextChars,
    matchLimit: options.matchLimit,
    includeTextItems: false,
    outputPath: null,
    timeoutMs: options.timeoutMs,
  })

  const probeVariants = []
  let probeIndex = 0
  for (const spec of querySpecs) {
    const raw = probeReport.queries[probeIndex]
    probeIndex += 1
    const sanitized = spec.sanitized ? probeReport.queries[probeIndex] : null
    if (spec.sanitized) {
      probeIndex += 1
    }
    const normalized = spec.normalized ? probeReport.queries[probeIndex] : null
    if (spec.normalized) {
      probeIndex += 1
    }
    probeVariants.push({ raw, sanitized, normalized })
  }

  const pageCorpora = buildPageCorpora(probeReport.pages)
  const benchmark = summarizeBenchmark(sampledQuotes, probeVariants, pageCorpora)

  return {
    generatedAt: new Date().toISOString(),
    source: chunkPayload.source,
    documentId: chunkPayload.documentId,
    sampling: {
      sampleSizeRequested: options.sampleSize,
      sampleSizeActual: sampledQuotes.length,
      minChars: options.minChars,
      maxChars: options.maxChars,
      maxQuotesPerChunk: options.maxQuotesPerChunk,
      includedSections:
        options.includeSections.size > 0 ? [...options.includeSections].sort() : [...DEFAULT_INCLUDED_TOP_LEVEL_SECTIONS].sort(),
      excludedSections: [...new Set([...DEFAULT_EXCLUDED_TOP_LEVEL_SECTIONS, ...options.excludeSections])].sort(),
      eligibleChunkCount: chunkPayload.chunks.filter((chunk) => shouldKeepChunk(chunk, options)).length,
      totalChunkCount: chunkPayload.chunks.length,
    },
    probe: {
      pdfPath: probeReport.pdfPath,
      pdfFingerprint: probeReport.pdfFingerprint,
      pdfjsVersion: probeReport.pdfjsVersion,
      pageCount: probeReport.pageCount,
    },
    benchmark,
  }
}

async function main() {
  try {
    const options = parseArgs(process.argv.slice(2))
    if (options.help) {
      printUsage()
      return
    }

    const report = await buildBenchmarkReport(options)
    const output = `${JSON.stringify(report, null, 2)}\n`

    if (options.outputPath) {
      const outputPath = path.resolve(options.outputPath)
      await fs.mkdir(path.dirname(outputPath), { recursive: true })
      await fs.writeFile(outputPath, output, 'utf8')
      console.error(`Wrote PDF.js quote benchmark report to ${outputPath}`)
      return
    }

    process.stdout.write(output)
  } catch (error) {
    fail(error instanceof Error ? error.stack ?? error.message : String(error))
  }
}

await main()

import {
  buildNormalizedTextSourceMap,
  sanitizeEvidenceSearchText,
} from './textNormalization'

interface AnchoringToken {
  value: string
  comparable: string
  alnumComparable: string
  digitComparable: string
  start: number
  endExclusive: number
}

interface AlignmentMatch {
  quoteIndex: number
  pageIndex: number
  similarity: number
}

export interface AnchoredEvidenceSpan {
  rawQuery: string
  normalizedQuery: string
  rawStart: number
  rawEndExclusive: number
  normalizedStart: number
  normalizedEndExclusive: number
  coverage: number
  score: number
  leadingAnchorMatched: boolean
  trailingAnchorMatched: boolean
  includesPreferredAnchor: boolean
}

export interface AnchoredEvidenceDebugRange {
  rawStart: number
  rawEndExclusive: number
}

export interface AnchoredEvidenceDebugTokenPair {
  quoteIndex: number
  quoteToken: string
  pageIndex: number
  pageToken: string
  similarity: number
}

export interface AnchoredEvidenceDebugMismatch {
  quoteIndex: number | null
  quoteToken: string | null
  pageIndex: number | null
  pageToken: string | null
}

interface AnchoringRawRange extends AnchoredEvidenceDebugRange {}

interface ExactAnchoredMatch extends AnchoringRawRange {
  normalizedStart: number
  normalizedEndExclusive: number
}

export interface AnchoredEvidenceSpanWindowDebug {
  searchWindow: AnchoredEvidenceDebugRange
  adjustedPreferredRawRange: AnchoredEvidenceDebugRange | null
  resolution: 'exact' | 'alignment' | 'none'
  normalizedPageTextLength: number
  quoteTokenCount: number
  pageTokenCount: number
  exactMatchCount: number
  selectedExactMatch: {
    rawStart: number
    rawEndExclusive: number
    normalizedStart: number
    normalizedEndExclusive: number
  } | null
  alignmentScore: number | null
  matchedPairCount: number
  boundaryMatchCount: number
  leadingContiguousMatchCount: number
  trailingContiguousMatchCount: number
  coverage: number | null
  score: number | null
  spanDensity: number | null
  largestMatchedPageGap: number | null
  leadingAnchorMatched: boolean | null
  trailingAnchorMatched: boolean | null
  firstLeadingMismatch: AnchoredEvidenceDebugMismatch | null
  firstTrailingMismatch: AnchoredEvidenceDebugMismatch | null
  matchedTokenPreview: AnchoredEvidenceDebugTokenPair[]
  includesPreferredAnchor: boolean | null
  containsPreferredRawRange: boolean | null
  candidateRawRange: AnchoredEvidenceDebugRange | null
  candidateRawQuery: string | null
  passesThreshold: boolean
  rejectionReasons: string[]
}

export interface AnchoredEvidenceSpanDebugInfo {
  normalizedQuote: string
  preferredAnchorNormalized: string | null
  preferredSearchWindows: AnchoredEvidenceDebugRange[]
  windows: AnchoredEvidenceSpanWindowDebug[]
  bestMatch: AnchoredEvidenceSpan | null
}

const TOKEN_PATTERN = /\S+/g
const EDGE_PUNCTUATION_PATTERN = /^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu
const INTERNAL_NON_ALNUM_PATTERN = /[^\p{L}\p{N}]+/gu
const ALNUM_PATTERN = /[\p{L}\p{N}]/u
const LOW_SIGNAL_BOUNDARY_TOKENS = new Set([
  'a',
  'an',
  'and',
  'as',
  'at',
  'by',
  'for',
  'from',
  'in',
  'is',
  'it',
  'of',
  'on',
  'or',
  'that',
  'the',
  'to',
  'with',
])
const MIN_ALIGNMENT_COVERAGE = 0.55
const STRONG_ALIGNMENT_COVERAGE = 0.72
const MIN_ALIGNMENT_SCORE = 0.58
const MIN_ALIGNMENT_SPAN_DENSITY = 0.58
const MATCH_SCORE_SCALE = 2
const GAP_SCORE = -0.72
const MISMATCH_SCORE = -1.08

const normalizeComparableToken = (value: string): string => {
  const lowercase = value.toLocaleLowerCase()
  const stripped = lowercase.replace(EDGE_PUNCTUATION_PATTERN, '')
  return stripped || lowercase
}

const normalizeAlnumToken = (value: string): string => {
  return normalizeComparableToken(value).replace(INTERNAL_NON_ALNUM_PATTERN, '')
}

const normalizeDigitToken = (value: string): string => {
  return normalizeComparableToken(value).replace(/[^\d]+/g, '')
}

const tokenizeNormalizedText = (value: string): AnchoringToken[] => {
  const tokens: AnchoringToken[] = []

  for (const match of value.matchAll(TOKEN_PATTERN)) {
    const token = match[0]
    const start = match.index ?? 0
    tokens.push({
      value: token,
      comparable: normalizeComparableToken(token),
      alnumComparable: normalizeAlnumToken(token),
      digitComparable: normalizeDigitToken(token),
      start,
      endExclusive: start + token.length,
    })
  }

  return tokens
}

const getSourceCodeUnitLength = (value: string, index: number): number => {
  const codePoint = value.codePointAt(index)
  return codePoint !== undefined && codePoint > 0xffff ? 2 : 1
}

const editDistance = (left: string, right: string): number => {
  if (left === right) {
    return 0
  }

  if (!left.length) {
    return right.length
  }

  if (!right.length) {
    return left.length
  }

  let previous = Array.from({ length: right.length + 1 }, (_, index) => index)
  let current = new Array<number>(right.length + 1)

  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    current[0] = leftIndex
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const substitutionCost = left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1
      current[rightIndex] = Math.min(
        previous[rightIndex] + 1,
        current[rightIndex - 1]! + 1,
        previous[rightIndex - 1]! + substitutionCost,
      )
    }

    const next = previous
    previous = current
    current = next
  }

  return previous[right.length] ?? right.length
}

const tokenSimilarity = (left: AnchoringToken, right: AnchoringToken): number => {
  if (!left.comparable || !right.comparable) {
    return 0
  }

  if (left.comparable === right.comparable) {
    return 1
  }

  if (
    left.alnumComparable.length >= 2
    && left.alnumComparable === right.alnumComparable
  ) {
    return 0.98
  }

  if (
    left.digitComparable.length > 0
    && left.digitComparable === right.digitComparable
  ) {
    return 0.95
  }

  const shorterLength = Math.min(left.comparable.length, right.comparable.length)
  if (
    shorterLength >= 4
    && (left.comparable.includes(right.comparable) || right.comparable.includes(left.comparable))
  ) {
    return 0.9
  }

  const maxLength = Math.max(left.comparable.length, right.comparable.length)
  if (maxLength < 4) {
    return 0
  }

  const distance = editDistance(left.comparable, right.comparable)
  const ratio = 1 - (distance / maxLength)
  if (ratio >= 0.84) {
    return ratio
  }

  return 0
}

const normalizeAnchoringInput = (value: string): string => {
  return buildNormalizedTextSourceMap(sanitizeEvidenceSearchText(value)).text.trim()
}

const mapNormalizedRangeToRawRange = (
  rawText: string,
  sourceIndices: number[],
  normalizedStart: number,
  normalizedEndExclusive: number,
): { rawStart: number; rawEndExclusive: number } | null => {
  if (normalizedStart < 0 || normalizedEndExclusive <= normalizedStart) {
    return null
  }

  const rawStart = sourceIndices[normalizedStart]
  const rawEnd = sourceIndices[normalizedEndExclusive - 1]
  if (rawStart === undefined || rawEnd === undefined) {
    return null
  }

  return {
    rawStart,
    rawEndExclusive: rawEnd + getSourceCodeUnitLength(rawText, rawEnd),
  }
}

const collectNormalizedOccurrenceRanges = (
  rawText: string,
  normalizedText: string,
  sourceIndices: number[],
  normalizedNeedle: string,
): AnchoringRawRange[] => {
  if (!normalizedNeedle) {
    return []
  }

  const comparableText = normalizedText.toLocaleLowerCase()
  const comparableNeedle = normalizedNeedle.toLocaleLowerCase()
  const ranges: AnchoringRawRange[] = []
  let searchStart = 0

  while (searchStart <= comparableText.length - comparableNeedle.length) {
    const matchIndex = comparableText.indexOf(comparableNeedle, searchStart)
    if (matchIndex < 0) {
      break
    }

    const rawRange = mapNormalizedRangeToRawRange(
      rawText,
      sourceIndices,
      matchIndex,
      matchIndex + normalizedNeedle.length,
    )
    if (rawRange) {
      ranges.push(rawRange)
    }

    searchStart = matchIndex + 1
  }

  return ranges
}

const buildSearchWindowFromQuoteAnchorRange = (
  rawPageText: string,
  sanitizedQuote: string,
  quoteAnchorRange: AnchoringRawRange,
  preferredRawRange: AnchoringRawRange,
): AnchoringRawRange => {
  const leadingRawLength = quoteAnchorRange.rawStart
  const trailingRawLength = sanitizedQuote.length - quoteAnchorRange.rawEndExclusive
  const slack = Math.max(24, Math.floor(sanitizedQuote.length * 0.12))

  return {
    rawStart: Math.max(0, preferredRawRange.rawStart - leadingRawLength - slack),
    rawEndExclusive: Math.min(
      rawPageText.length,
      preferredRawRange.rawEndExclusive + trailingRawLength + slack,
    ),
  }
}

const buildPreferredSearchWindows = (
  rawPageText: string,
  desiredQuote: string,
  preferredAnchor?: string | null,
  preferredRawRange?: AnchoringRawRange | null,
): AnchoringRawRange[] => {
  if (
    !preferredRawRange
    || preferredRawRange.rawEndExclusive <= preferredRawRange.rawStart
  ) {
    return []
  }

  const normalizedAnchor = normalizeAnchoringInput(preferredAnchor ?? '')
  if (!normalizedAnchor) {
    return []
  }

  const sanitizedQuote = sanitizeEvidenceSearchText(desiredQuote)
  if (!sanitizedQuote.trim()) {
    return []
  }

  const desiredQuoteSourceMap = buildNormalizedTextSourceMap(sanitizedQuote)
  const normalizedDesiredQuote = desiredQuoteSourceMap.text
  const anchorRangesInQuote = collectNormalizedOccurrenceRanges(
    sanitizedQuote,
    normalizedDesiredQuote,
    desiredQuoteSourceMap.sourceIndices,
    normalizedAnchor,
  )
  if (anchorRangesInQuote.length === 0) {
    return []
  }

  const seen = new Set<string>()
  return anchorRangesInQuote.reduce<AnchoringRawRange[]>((windows, anchorRangeInQuote) => {
    const windowRange = buildSearchWindowFromQuoteAnchorRange(
      rawPageText,
      sanitizedQuote,
      anchorRangeInQuote,
      preferredRawRange,
    )
    const key = `${windowRange.rawStart}:${windowRange.rawEndExclusive}`
    if (seen.has(key)) {
      return windows
    }

    seen.add(key)
    windows.push(windowRange)
    return windows
  }, [])
}

const collectExactAnchoredMatches = (
  rawPageText: string,
  normalizedPageText: string,
  sourceIndices: number[],
  normalizedQuote: string,
): ExactAnchoredMatch[] => {
  const comparablePageText = normalizedPageText.toLocaleLowerCase()
  const comparableQuote = normalizedQuote.toLocaleLowerCase()
  const matches: ExactAnchoredMatch[] = []
  let searchStart = 0

  while (searchStart <= comparablePageText.length - comparableQuote.length) {
    const matchIndex = comparablePageText.indexOf(comparableQuote, searchStart)
    if (matchIndex < 0) {
      break
    }

    const normalizedEndExclusive = matchIndex + normalizedQuote.length
    const rawRange = mapNormalizedRangeToRawRange(
      rawPageText,
      sourceIndices,
      matchIndex,
      normalizedEndExclusive,
    )
    if (rawRange) {
      matches.push({
        rawStart: rawRange.rawStart,
        rawEndExclusive: rawRange.rawEndExclusive,
        normalizedStart: matchIndex,
        normalizedEndExclusive,
      })
    }

    searchStart = matchIndex + normalizedQuote.length
  }

  return matches
}

const getRangeCenter = (range: AnchoringRawRange): number => {
  return range.rawStart + ((range.rawEndExclusive - range.rawStart) / 2)
}

const getRangeOverlap = (left: AnchoringRawRange, right: AnchoringRawRange): number => {
  return Math.max(0, Math.min(left.rawEndExclusive, right.rawEndExclusive) - Math.max(left.rawStart, right.rawStart))
}

const selectPreferredExactMatch = (
  matches: ExactAnchoredMatch[],
  preferredRawRange?: AnchoringRawRange | null,
): ExactAnchoredMatch | null => {
  if (matches.length === 0) {
    return null
  }

  if (
    !preferredRawRange
    || preferredRawRange.rawEndExclusive <= preferredRawRange.rawStart
    || matches.length === 1
  ) {
    return matches[0] ?? null
  }

  const preferredCenter = getRangeCenter(preferredRawRange)

  return matches.reduce<ExactAnchoredMatch>((bestMatch, candidate) => {
    const bestOverlap = getRangeOverlap(bestMatch, preferredRawRange)
    const candidateOverlap = getRangeOverlap(candidate, preferredRawRange)
    if (candidateOverlap !== bestOverlap) {
      return candidateOverlap > bestOverlap ? candidate : bestMatch
    }

    const bestCenterDistance = Math.abs(getRangeCenter(bestMatch) - preferredCenter)
    const candidateCenterDistance = Math.abs(getRangeCenter(candidate) - preferredCenter)
    if (candidateCenterDistance !== bestCenterDistance) {
      return candidateCenterDistance < bestCenterDistance ? candidate : bestMatch
    }

    const bestStartDistance = Math.abs(bestMatch.rawStart - preferredRawRange.rawStart)
    const candidateStartDistance = Math.abs(candidate.rawStart - preferredRawRange.rawStart)
    if (candidateStartDistance !== bestStartDistance) {
      return candidateStartDistance < bestStartDistance ? candidate : bestMatch
    }

    return candidate.normalizedStart < bestMatch.normalizedStart ? candidate : bestMatch
  }, matches[0]!)
}

const buildExactAnchoredSpan = (
  rawPageText: string,
  normalizedPageText: string,
  sourceIndices: number[],
  normalizedQuote: string,
  preferredRawRange?: AnchoringRawRange | null,
): AnchoredEvidenceSpan | null => {
  if (!normalizedQuote) {
    return null
  }

  const exactMatches = collectExactAnchoredMatches(
    rawPageText,
    normalizedPageText,
    sourceIndices,
    normalizedQuote,
  )
  const selectedMatch = selectPreferredExactMatch(exactMatches, preferredRawRange)
  if (!selectedMatch) {
    return null
  }

  return {
    rawQuery: rawPageText.slice(selectedMatch.rawStart, selectedMatch.rawEndExclusive),
    normalizedQuery: normalizedPageText.slice(selectedMatch.normalizedStart, selectedMatch.normalizedEndExclusive),
    rawStart: selectedMatch.rawStart,
    rawEndExclusive: selectedMatch.rawEndExclusive,
    normalizedStart: selectedMatch.normalizedStart,
    normalizedEndExclusive: selectedMatch.normalizedEndExclusive,
    coverage: 1,
    score: 1,
    leadingAnchorMatched: true,
    trailingAnchorMatched: true,
    includesPreferredAnchor: true,
  }
}

const buildPreferredAnchorPredicate = (preferredAnchor?: string | null): ((value: string) => boolean) => {
  const normalizedAnchor = normalizeAnchoringInput(preferredAnchor ?? '')
  if (!normalizedAnchor) {
    return () => true
  }

  const comparableAnchor = normalizeComparableToken(normalizedAnchor)
  const alnumAnchor = normalizeAlnumToken(normalizedAnchor)

  return (value: string) => {
    const normalizedValue = normalizeAnchoringInput(value)
    if (!normalizedValue) {
      return false
    }

    const comparableValue = normalizeComparableToken(normalizedValue)
    const alnumValue = normalizeAlnumToken(normalizedValue)
    return (
      comparableValue.includes(comparableAnchor)
      || comparableAnchor.includes(comparableValue)
      || (alnumAnchor.length >= 4 && alnumValue.includes(alnumAnchor))
    )
  }
}

const resolveTrailingBoundary = (
  pageToken: AnchoringToken,
  quoteToken: AnchoringToken,
): number => {
  if (!pageToken.comparable || !quoteToken.comparable) {
    return pageToken.endExclusive
  }

  if (pageToken.comparable === quoteToken.comparable) {
    return pageToken.endExclusive
  }

  const lowercasePageToken = pageToken.value.toLocaleLowerCase()
  const matchIndex = lowercasePageToken.indexOf(quoteToken.comparable)
  if (matchIndex < 0) {
    return pageToken.endExclusive
  }

  const suffix = lowercasePageToken.slice(matchIndex + quoteToken.comparable.length)
  if (!suffix || !ALNUM_PATTERN.test(suffix)) {
    return pageToken.endExclusive
  }

  return pageToken.start + matchIndex + quoteToken.comparable.length
}

const isStrongBoundaryMatch = (
  quoteToken: AnchoringToken,
  pageToken: AnchoringToken,
  similarity: number,
): boolean => {
  if (similarity < 0.9) {
    return false
  }

  if (quoteToken.digitComparable.length > 0 && quoteToken.digitComparable === pageToken.digitComparable) {
    return true
  }

  const comparable = quoteToken.comparable
  return comparable.length >= 3 && !LOW_SIGNAL_BOUNDARY_TOKENS.has(comparable)
}

const recoverBestLocalAlignment = (
  quoteTokens: AnchoringToken[],
  pageTokens: AnchoringToken[],
): {
  matches: AlignmentMatch[]
  score: number
} | null => {
  if (quoteTokens.length === 0 || pageTokens.length === 0) {
    return null
  }

  const rows = quoteTokens.length + 1
  const cols = pageTokens.length + 1
  const scores = Array.from({ length: rows }, () => new Array<number>(cols).fill(0))
  const trace = Array.from({ length: rows }, () => new Array<number>(cols).fill(0))
  const similarities = Array.from({ length: rows }, () => new Array<number>(cols).fill(0))

  let bestScore = 0
  let bestRow = 0
  let bestCol = 0

  for (let row = 1; row < rows; row += 1) {
    for (let col = 1; col < cols; col += 1) {
      const similarity = tokenSimilarity(quoteTokens[row - 1]!, pageTokens[col - 1]!)
      similarities[row][col] = similarity

      const diag = scores[row - 1]![col - 1]! + (similarity > 0 ? similarity * MATCH_SCORE_SCALE : MISMATCH_SCORE)
      const up = scores[row - 1]![col]! + GAP_SCORE
      const left = scores[row]![col - 1]! + GAP_SCORE
      const value = Math.max(0, diag, up, left)

      scores[row][col] = value
      if (value === 0) {
        trace[row][col] = 0
      } else if (value === diag) {
        trace[row][col] = 1
      } else if (value === up) {
        trace[row][col] = 2
      } else {
        trace[row][col] = 3
      }

      if (value > bestScore) {
        bestScore = value
        bestRow = row
        bestCol = col
      }
    }
  }

  if (bestScore <= 0) {
    return null
  }

  const matches: AlignmentMatch[] = []
  let row = bestRow
  let col = bestCol

  while (row > 0 && col > 0 && scores[row]![col]! > 0) {
    const direction = trace[row]![col]
    if (direction === 1) {
      const similarity = similarities[row]![col] ?? 0
      if (similarity > 0) {
        matches.push({
          quoteIndex: row - 1,
          pageIndex: col - 1,
          similarity,
        })
      }
      row -= 1
      col -= 1
      continue
    }

    if (direction === 2) {
      row -= 1
      continue
    }

    if (direction === 3) {
      col -= 1
      continue
    }

    break
  }

  if (matches.length === 0) {
    return null
  }

  matches.reverse()
  return {
    matches,
    score: bestScore,
  }
}

const buildAlignmentMatchPreview = (
  quoteTokens: AnchoringToken[],
  pageTokens: AnchoringToken[],
  matches: AlignmentMatch[],
): AnchoredEvidenceDebugTokenPair[] => {
  return matches.slice(0, 12).map((match) => ({
    quoteIndex: match.quoteIndex,
    quoteToken: quoteTokens[match.quoteIndex]?.value ?? '',
    pageIndex: match.pageIndex,
    pageToken: pageTokens[match.pageIndex]?.value ?? '',
    similarity: match.similarity,
  }))
}

const buildLeadingMismatch = (
  quoteTokens: AnchoringToken[],
  pageTokens: AnchoringToken[],
  matchByQuoteIndex: Map<number, AlignmentMatch>,
  matchedCount: number,
  lastMatchedPageIndex: number,
): AnchoredEvidenceDebugMismatch | null => {
  if (matchedCount >= quoteTokens.length) {
    return null
  }

  const quoteToken = quoteTokens[matchedCount] ?? null
  const directMatch = matchByQuoteIndex.get(matchedCount)
  const pageIndex = directMatch?.pageIndex ?? (
    lastMatchedPageIndex + 1 < pageTokens.length
      ? lastMatchedPageIndex + 1
      : null
  )
  const pageToken = pageIndex !== null ? pageTokens[pageIndex] ?? null : null

  return {
    quoteIndex: matchedCount,
    quoteToken: quoteToken?.value ?? null,
    pageIndex,
    pageToken: pageToken?.value ?? null,
  }
}

const buildTrailingMismatch = (
  quoteTokens: AnchoringToken[],
  pageTokens: AnchoringToken[],
  matchByQuoteIndex: Map<number, AlignmentMatch>,
  matchedCount: number,
  firstMatchedPageIndex: number,
): AnchoredEvidenceDebugMismatch | null => {
  if (matchedCount >= quoteTokens.length) {
    return null
  }

  const quoteIndex = quoteTokens.length - 1 - matchedCount
  const quoteToken = quoteTokens[quoteIndex] ?? null
  const directMatch = matchByQuoteIndex.get(quoteIndex)
  const pageIndex = directMatch?.pageIndex ?? (
    firstMatchedPageIndex - 1 >= 0
      ? firstMatchedPageIndex - 1
      : null
  )
  const pageToken = pageIndex !== null ? pageTokens[pageIndex] ?? null : null

  return {
    quoteIndex,
    quoteToken: quoteToken?.value ?? null,
    pageIndex,
    pageToken: pageToken?.value ?? null,
  }
}

const buildAlignmentContiguityDebug = (
  quoteTokens: AnchoringToken[],
  pageTokens: AnchoringToken[],
  matches: AlignmentMatch[],
): {
  leadingContiguousMatchCount: number
  trailingContiguousMatchCount: number
  firstLeadingMismatch: AnchoredEvidenceDebugMismatch | null
  firstTrailingMismatch: AnchoredEvidenceDebugMismatch | null
  matchedTokenPreview: AnchoredEvidenceDebugTokenPair[]
} => {
  const matchByQuoteIndex = new Map<number, AlignmentMatch>()
  matches.forEach((match) => {
    if (!matchByQuoteIndex.has(match.quoteIndex)) {
      matchByQuoteIndex.set(match.quoteIndex, match)
    }
  })

  let leadingContiguousMatchCount = 0
  let lastLeadingPageIndex = -1
  while (leadingContiguousMatchCount < quoteTokens.length) {
    const match = matchByQuoteIndex.get(leadingContiguousMatchCount)
    if (!match || match.pageIndex <= lastLeadingPageIndex) {
      break
    }
    lastLeadingPageIndex = match.pageIndex
    leadingContiguousMatchCount += 1
  }

  let trailingContiguousMatchCount = 0
  let firstTrailingPageIndex = pageTokens.length
  for (let quoteIndex = quoteTokens.length - 1; quoteIndex >= 0; quoteIndex -= 1) {
    const match = matchByQuoteIndex.get(quoteIndex)
    if (!match || match.pageIndex >= firstTrailingPageIndex) {
      break
    }
    firstTrailingPageIndex = match.pageIndex
    trailingContiguousMatchCount += 1
  }

  return {
    leadingContiguousMatchCount,
    trailingContiguousMatchCount,
    firstLeadingMismatch: buildLeadingMismatch(
      quoteTokens,
      pageTokens,
      matchByQuoteIndex,
      leadingContiguousMatchCount,
      lastLeadingPageIndex,
    ),
    firstTrailingMismatch: buildTrailingMismatch(
      quoteTokens,
      pageTokens,
      matchByQuoteIndex,
      trailingContiguousMatchCount,
      firstTrailingPageIndex,
    ),
    matchedTokenPreview: buildAlignmentMatchPreview(quoteTokens, pageTokens, matches),
  }
}

const spanContainsPreferredRawRange = (
  span: AnchoringRawRange,
  preferredRawRange?: AnchoringRawRange | null,
): boolean => {
  if (
    !preferredRawRange
    || preferredRawRange.rawEndExclusive <= preferredRawRange.rawStart
  ) {
    return true
  }

  return (
    span.rawStart <= preferredRawRange.rawStart
    && span.rawEndExclusive >= preferredRawRange.rawEndExclusive
  )
}

const chooseBetterAnchoredSpan = (
  currentBest: AnchoredEvidenceSpan | null,
  candidate: AnchoredEvidenceSpan,
  preferredRawRange?: AnchoringRawRange | null,
): AnchoredEvidenceSpan => {
  if (!currentBest) {
    return candidate
  }

  const bestContainsPreferredRange = spanContainsPreferredRawRange(currentBest, preferredRawRange)
  const candidateContainsPreferredRange = spanContainsPreferredRawRange(candidate, preferredRawRange)
  if (candidateContainsPreferredRange !== bestContainsPreferredRange) {
    return candidateContainsPreferredRange ? candidate : currentBest
  }

  if (candidate.coverage !== currentBest.coverage) {
    return candidate.coverage > currentBest.coverage ? candidate : currentBest
  }

  if (candidate.score !== currentBest.score) {
    return candidate.score > currentBest.score ? candidate : currentBest
  }

  const candidateLength = candidate.rawEndExclusive - candidate.rawStart
  const currentLength = currentBest.rawEndExclusive - currentBest.rawStart
  if (candidateLength !== currentLength) {
    return candidateLength > currentLength ? candidate : currentBest
  }

  if (preferredRawRange) {
    const preferredCenter = getRangeCenter(preferredRawRange)
    const candidateCenterDistance = Math.abs(getRangeCenter(candidate) - preferredCenter)
    const currentCenterDistance = Math.abs(getRangeCenter(currentBest) - preferredCenter)
    if (candidateCenterDistance !== currentCenterDistance) {
      return candidateCenterDistance < currentCenterDistance ? candidate : currentBest
    }
  }

  return candidate.rawStart < currentBest.rawStart ? candidate : currentBest
}

const evaluateAnchoredEvidenceSpanInWindow = (
  searchPageText: string,
  normalizedQuote: string,
  searchWindowStart: number,
  options?: {
    preferredAnchor?: string | null
    preferredRawRange?: AnchoringRawRange | null
  },
): {
  candidate: AnchoredEvidenceSpan | null
  debug: AnchoredEvidenceSpanWindowDebug
} => {
  const debugWindow: AnchoredEvidenceSpanWindowDebug = {
    searchWindow: {
      rawStart: searchWindowStart,
      rawEndExclusive: searchWindowStart + searchPageText.length,
    },
    adjustedPreferredRawRange: options?.preferredRawRange
      ? { ...options.preferredRawRange }
      : null,
    resolution: 'none',
    normalizedPageTextLength: 0,
    quoteTokenCount: 0,
    pageTokenCount: 0,
    exactMatchCount: 0,
    selectedExactMatch: null,
    alignmentScore: null,
    matchedPairCount: 0,
    boundaryMatchCount: 0,
    leadingContiguousMatchCount: 0,
    trailingContiguousMatchCount: 0,
    coverage: null,
    score: null,
    spanDensity: null,
    largestMatchedPageGap: null,
    leadingAnchorMatched: null,
    trailingAnchorMatched: null,
    firstLeadingMismatch: null,
    firstTrailingMismatch: null,
    matchedTokenPreview: [],
    includesPreferredAnchor: null,
    containsPreferredRawRange: null,
    candidateRawRange: null,
    candidateRawQuery: null,
    passesThreshold: false,
    rejectionReasons: [],
  }
  const sourceMap = buildNormalizedTextSourceMap(searchPageText)
  const normalizedPageText = sourceMap.text
  debugWindow.normalizedPageTextLength = normalizedPageText.length
  if (!normalizedPageText.trim()) {
    debugWindow.rejectionReasons.push('empty-normalized-page-text')
    return {
      candidate: null,
      debug: debugWindow,
    }
  }

  const exactMatches = collectExactAnchoredMatches(
    searchPageText,
    normalizedPageText,
    sourceMap.sourceIndices,
    normalizedQuote,
  )
  debugWindow.exactMatchCount = exactMatches.length
  const exactSelectedMatch = selectPreferredExactMatch(exactMatches, options?.preferredRawRange)
  debugWindow.selectedExactMatch = exactSelectedMatch
  const exactMatch = exactSelectedMatch
    ? buildExactAnchoredSpan(
      searchPageText,
      normalizedPageText,
      sourceMap.sourceIndices,
      normalizedQuote,
      options?.preferredRawRange,
    )
    : null
  if (exactMatch) {
    debugWindow.candidateRawRange = {
      rawStart: exactMatch.rawStart + searchWindowStart,
      rawEndExclusive: exactMatch.rawEndExclusive + searchWindowStart,
    }
    debugWindow.candidateRawQuery = exactMatch.rawQuery
    debugWindow.containsPreferredRawRange = spanContainsPreferredRawRange(exactMatch, options?.preferredRawRange)
  }
  if (exactMatch && debugWindow.containsPreferredRawRange) {
    debugWindow.resolution = 'exact'
    debugWindow.passesThreshold = true
    return {
      candidate: {
        ...exactMatch,
        rawStart: exactMatch.rawStart + searchWindowStart,
        rawEndExclusive: exactMatch.rawEndExclusive + searchWindowStart,
      },
      debug: debugWindow,
    }
  }
  if (exactMatch) {
    debugWindow.rejectionReasons.push('exact-match-missed-preferred-range')
  } else {
    debugWindow.rejectionReasons.push('no-exact-match')
  }

  const quoteTokens = tokenizeNormalizedText(normalizedQuote)
  const pageTokens = tokenizeNormalizedText(normalizedPageText)
  debugWindow.quoteTokenCount = quoteTokens.length
  debugWindow.pageTokenCount = pageTokens.length
  const alignment = recoverBestLocalAlignment(quoteTokens, pageTokens)
  if (!alignment) {
    debugWindow.rejectionReasons.push('no-local-alignment')
    return {
      candidate: null,
      debug: debugWindow,
    }
  }
  debugWindow.alignmentScore = alignment.score
  debugWindow.matchedPairCount = alignment.matches.length
  const contiguityDebug = buildAlignmentContiguityDebug(quoteTokens, pageTokens, alignment.matches)
  debugWindow.leadingContiguousMatchCount = contiguityDebug.leadingContiguousMatchCount
  debugWindow.trailingContiguousMatchCount = contiguityDebug.trailingContiguousMatchCount
  debugWindow.firstLeadingMismatch = contiguityDebug.firstLeadingMismatch
  debugWindow.firstTrailingMismatch = contiguityDebug.firstTrailingMismatch
  debugWindow.matchedTokenPreview = contiguityDebug.matchedTokenPreview

  const boundaryMatches = alignment.matches.filter((match) => {
    const quoteToken = quoteTokens[match.quoteIndex]
    const pageToken = pageTokens[match.pageIndex]
    return Boolean(quoteToken && pageToken && isStrongBoundaryMatch(quoteToken, pageToken, match.similarity))
  })
  const effectiveBoundaryMatches = boundaryMatches.length > 0 ? boundaryMatches : alignment.matches
  debugWindow.boundaryMatchCount = boundaryMatches.length
  const matchedQuoteIndices = effectiveBoundaryMatches.map((match) => match.quoteIndex)
  const matchedPageIndices = effectiveBoundaryMatches.map((match) => match.pageIndex)
  const firstPageIndex = Math.min(...matchedPageIndices)
  const lastPageIndex = Math.max(...matchedPageIndices)
  const firstQuoteIndex = Math.min(...matchedQuoteIndices)
  const lastQuoteIndex = Math.max(...matchedQuoteIndices)
  const firstPageToken = pageTokens[firstPageIndex]
  const lastPageToken = pageTokens[lastPageIndex]
  const lastMatch = effectiveBoundaryMatches[effectiveBoundaryMatches.length - 1]
  const lastQuoteToken = lastMatch ? quoteTokens[lastMatch.quoteIndex] : null
  if (!firstPageToken || !lastPageToken) {
    return null
  }

  const normalizedStart = firstPageToken.start
  const normalizedEndExclusive = lastQuoteToken
    ? resolveTrailingBoundary(lastPageToken, lastQuoteToken)
    : lastPageToken.endExclusive
  const rawRange = mapNormalizedRangeToRawRange(
    searchPageText,
    sourceMap.sourceIndices,
    normalizedStart,
    normalizedEndExclusive,
  )
  if (!rawRange) {
    debugWindow.rejectionReasons.push('unable-to-map-normalized-range-to-raw-text')
    return {
      candidate: null,
      debug: debugWindow,
    }
  }

  const coverage = alignment.matches.length / quoteTokens.length
  const score = alignment.score / (quoteTokens.length * MATCH_SCORE_SCALE)
  const pageSpanTokenCount = lastPageIndex - firstPageIndex + 1
  const spanDensity = alignment.matches.length / pageSpanTokenCount
  const largestMatchedPageGap = alignment.matches.reduce((largestGap, match, index) => {
    if (index === 0) {
      return largestGap
    }

    const previousMatch = alignment.matches[index - 1]
    if (!previousMatch) {
      return largestGap
    }

    return Math.max(largestGap, match.pageIndex - previousMatch.pageIndex - 1)
  }, 0)
  const leadingAnchorMatched = firstQuoteIndex <= Math.max(1, Math.floor(quoteTokens.length * 0.12))
  const trailingAnchorMatched = lastQuoteIndex >= quoteTokens.length - 1 - Math.max(1, Math.floor(quoteTokens.length * 0.12))
  const rawQuery = searchPageText.slice(rawRange.rawStart, rawRange.rawEndExclusive)
  const includesPreferredAnchor = buildPreferredAnchorPredicate(options?.preferredAnchor)(rawQuery)
  const containsPreferredRawRange = spanContainsPreferredRawRange(rawRange, options?.preferredRawRange)
  debugWindow.coverage = coverage
  debugWindow.score = score
  debugWindow.spanDensity = spanDensity
  debugWindow.largestMatchedPageGap = largestMatchedPageGap
  debugWindow.leadingAnchorMatched = leadingAnchorMatched
  debugWindow.trailingAnchorMatched = trailingAnchorMatched
  debugWindow.includesPreferredAnchor = includesPreferredAnchor
  debugWindow.containsPreferredRawRange = containsPreferredRawRange
  debugWindow.candidateRawRange = {
    rawStart: rawRange.rawStart + searchWindowStart,
    rawEndExclusive: rawRange.rawEndExclusive + searchWindowStart,
  }
  debugWindow.candidateRawQuery = rawQuery

  const passesThreshold = (
    score >= MIN_ALIGNMENT_SCORE
    && (
      (coverage >= MIN_ALIGNMENT_COVERAGE && leadingAnchorMatched && trailingAnchorMatched)
      || (coverage >= STRONG_ALIGNMENT_COVERAGE && (leadingAnchorMatched || trailingAnchorMatched))
    )
    && spanDensity >= MIN_ALIGNMENT_SPAN_DENSITY
    && largestMatchedPageGap <= Math.max(8, Math.floor(quoteTokens.length * 0.35))
    && includesPreferredAnchor
    && containsPreferredRawRange
  )
  debugWindow.passesThreshold = passesThreshold

  if (!passesThreshold) {
    if (score < MIN_ALIGNMENT_SCORE) {
      debugWindow.rejectionReasons.push('score-below-threshold')
    }
    if (
      !(
        (coverage >= MIN_ALIGNMENT_COVERAGE && leadingAnchorMatched && trailingAnchorMatched)
        || (coverage >= STRONG_ALIGNMENT_COVERAGE && (leadingAnchorMatched || trailingAnchorMatched))
      )
    ) {
      debugWindow.rejectionReasons.push('coverage-or-boundary-threshold-failed')
    }
    if (spanDensity < MIN_ALIGNMENT_SPAN_DENSITY) {
      debugWindow.rejectionReasons.push('span-density-too-low')
    }
    if (largestMatchedPageGap > Math.max(8, Math.floor(quoteTokens.length * 0.35))) {
      debugWindow.rejectionReasons.push('page-gap-too-large')
    }
    if (!includesPreferredAnchor) {
      debugWindow.rejectionReasons.push('preferred-anchor-not-contained')
    }
    if (!containsPreferredRawRange) {
      debugWindow.rejectionReasons.push('preferred-raw-range-not-contained')
    }
    return {
      candidate: null,
      debug: debugWindow,
    }
  }

  debugWindow.resolution = 'alignment'
  return {
    candidate: {
      rawQuery,
      normalizedQuery: normalizedPageText.slice(normalizedStart, normalizedEndExclusive),
      rawStart: rawRange.rawStart + searchWindowStart,
      rawEndExclusive: rawRange.rawEndExclusive + searchWindowStart,
      normalizedStart,
      normalizedEndExclusive,
      coverage,
      score,
      leadingAnchorMatched,
      trailingAnchorMatched,
      includesPreferredAnchor,
    },
    debug: debugWindow,
  }
}

export const findAnchoredEvidenceSpan = (
  rawPageText: string,
  desiredQuote: string,
  options?: {
    preferredAnchor?: string | null
    preferredRawRange?: AnchoringRawRange | null
  },
): AnchoredEvidenceSpan | null => {
  const normalizedQuote = normalizeAnchoringInput(desiredQuote)
  if (!rawPageText.trim() || !normalizedQuote) {
    return null
  }

  const preferredSearchWindows = buildPreferredSearchWindows(
    rawPageText,
    desiredQuote,
    options?.preferredAnchor,
    options?.preferredRawRange,
  )
  const searchWindows = preferredSearchWindows.length > 0
    ? [
        ...preferredSearchWindows,
        { rawStart: 0, rawEndExclusive: rawPageText.length },
      ]
    : [{ rawStart: 0, rawEndExclusive: rawPageText.length }]

  let bestMatch: AnchoredEvidenceSpan | null = null
  const seenWindows = new Set<string>()

  for (const searchWindow of searchWindows) {
    const key = `${searchWindow.rawStart}:${searchWindow.rawEndExclusive}`
    if (seenWindows.has(key)) {
      continue
    }
    seenWindows.add(key)

    const searchPageText = rawPageText.slice(searchWindow.rawStart, searchWindow.rawEndExclusive)
    const adjustedPreferredRawRange = options?.preferredRawRange
      ? {
          rawStart: Math.max(0, options.preferredRawRange.rawStart - searchWindow.rawStart),
          rawEndExclusive: Math.min(
            searchPageText.length,
            options.preferredRawRange.rawEndExclusive - searchWindow.rawStart,
          ),
        }
      : null

    const evaluation = evaluateAnchoredEvidenceSpanInWindow(
      searchPageText,
      normalizedQuote,
      searchWindow.rawStart,
      {
        preferredAnchor: options?.preferredAnchor,
        preferredRawRange: adjustedPreferredRawRange,
      },
    )
    if (!evaluation.candidate) {
      continue
    }

    bestMatch = chooseBetterAnchoredSpan(bestMatch, evaluation.candidate, options?.preferredRawRange)
  }

  return bestMatch
}

export const getAnchoredEvidenceSpanDebugInfo = (
  rawPageText: string,
  desiredQuote: string,
  options?: {
    preferredAnchor?: string | null
    preferredRawRange?: AnchoringRawRange | null
  },
): AnchoredEvidenceSpanDebugInfo => {
  const normalizedQuote = normalizeAnchoringInput(desiredQuote)
  const preferredSearchWindows = normalizedQuote
    ? buildPreferredSearchWindows(
      rawPageText,
      desiredQuote,
      options?.preferredAnchor,
      options?.preferredRawRange,
    )
    : []

  if (!rawPageText.trim() || !normalizedQuote) {
    return {
      normalizedQuote,
      preferredAnchorNormalized: normalizeAnchoringInput(options?.preferredAnchor ?? '') || null,
      preferredSearchWindows,
      windows: [],
      bestMatch: null,
    }
  }

  const searchWindows = preferredSearchWindows.length > 0
    ? [
      ...preferredSearchWindows,
      { rawStart: 0, rawEndExclusive: rawPageText.length },
    ]
    : [{ rawStart: 0, rawEndExclusive: rawPageText.length }]

  let bestMatch: AnchoredEvidenceSpan | null = null
  const windows: AnchoredEvidenceSpanWindowDebug[] = []
  const seenWindows = new Set<string>()

  for (const searchWindow of searchWindows) {
    const key = `${searchWindow.rawStart}:${searchWindow.rawEndExclusive}`
    if (seenWindows.has(key)) {
      continue
    }
    seenWindows.add(key)

    const searchPageText = rawPageText.slice(searchWindow.rawStart, searchWindow.rawEndExclusive)
    const adjustedPreferredRawRange = options?.preferredRawRange
      ? {
        rawStart: Math.max(0, options.preferredRawRange.rawStart - searchWindow.rawStart),
        rawEndExclusive: Math.min(
          searchPageText.length,
          options.preferredRawRange.rawEndExclusive - searchWindow.rawStart,
        ),
      }
      : null

    const evaluation = evaluateAnchoredEvidenceSpanInWindow(
      searchPageText,
      normalizedQuote,
      searchWindow.rawStart,
      {
        preferredAnchor: options?.preferredAnchor,
        preferredRawRange: adjustedPreferredRawRange,
      },
    )
    windows.push(evaluation.debug)

    if (evaluation.candidate) {
      bestMatch = chooseBetterAnchoredSpan(bestMatch, evaluation.candidate, options?.preferredRawRange)
    }
  }

  return {
    normalizedQuote,
    preferredAnchorNormalized: normalizeAnchoringInput(options?.preferredAnchor ?? '') || null,
    preferredSearchWindows,
    windows,
    bestMatch,
  }
}

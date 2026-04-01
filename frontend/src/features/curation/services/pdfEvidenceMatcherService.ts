import { readCurationApiError } from './api'

export type PdfEvidenceFuzzyMatchStrategy =
  | 'none'
  | 'rapidfuzz-single-page'
  | 'rapidfuzz-stitched-page'

export interface PdfEvidenceFuzzyMatchPage {
  pageNumber: number
  text: string
}

export interface PdfEvidenceFuzzyMatchRange {
  pageNumber: number
  rawStart: number
  rawEndExclusive: number
  query: string
}

export interface PdfEvidenceFuzzyMatchResult {
  found: boolean
  strategy: PdfEvidenceFuzzyMatchStrategy
  score: number
  matchedPage: number | null
  matchedQuery: string | null
  matchedRange: PdfEvidenceFuzzyMatchRange | null
  fullQuery: string | null
  pageRanges: PdfEvidenceFuzzyMatchRange[]
  crossPage: boolean
  note: string
}

export interface PdfEvidenceFuzzyMatchRequest {
  quote: string
  pageHints?: number[]
  minScore?: number
  pages: PdfEvidenceFuzzyMatchPage[]
}

interface PdfEvidenceFuzzyMatchApiRange {
  page_number: number
  raw_start: number
  raw_end_exclusive: number
  query: string
}

interface PdfEvidenceFuzzyMatchApiResponse {
  found: boolean
  strategy: PdfEvidenceFuzzyMatchStrategy
  score: number
  matched_page: number | null
  matched_query: string | null
  matched_range: PdfEvidenceFuzzyMatchApiRange | null
  full_query: string | null
  page_ranges: PdfEvidenceFuzzyMatchApiRange[]
  cross_page: boolean
  note: string
}

const normalizeRange = (
  range: PdfEvidenceFuzzyMatchApiRange | null,
): PdfEvidenceFuzzyMatchRange | null => {
  if (!range) {
    return null
  }

  return {
    pageNumber: range.page_number,
    rawStart: range.raw_start,
    rawEndExclusive: range.raw_end_exclusive,
    query: range.query,
  }
}

export async function fuzzyMatchPdfEvidenceQuote(
  request: PdfEvidenceFuzzyMatchRequest,
): Promise<PdfEvidenceFuzzyMatchResult> {
  const response = await fetch('/api/pdf-viewer/evidence/fuzzy-match', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      quote: request.quote,
      page_hints: request.pageHints ?? [],
      min_score: request.minScore,
      pages: request.pages.map((page) => ({
        page_number: page.pageNumber,
        text: page.text,
      })),
    }),
  })

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  const payload = await response.json() as PdfEvidenceFuzzyMatchApiResponse
  return {
    found: payload.found,
    strategy: payload.strategy,
    score: payload.score,
    matchedPage: payload.matched_page,
    matchedQuery: payload.matched_query,
    matchedRange: normalizeRange(payload.matched_range),
    fullQuery: payload.full_query,
    pageRanges: payload.page_ranges.map((range) => normalizeRange(range)).filter((range): range is PdfEvidenceFuzzyMatchRange => range !== null),
    crossPage: payload.cross_page,
    note: payload.note,
  }
}

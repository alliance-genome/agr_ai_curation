/**
 * Evidence navigation across a paper's files (ALL-1460, P9b).
 *
 * Every evidence record written from 0.12.0 names its file. The viewer decides from
 * the file id alone, before any search, whether the evidence can be shown:
 * - no file id: saved before files were recorded; nothing opens (Q4, Chris's text);
 * - a file id that is not one of the paper's tabs: the file was removed; nothing opens;
 * - otherwise the file's tab is shown and the quote is located inside that file only.
 *
 * Locating a quote in a text file is exact copied-text matching (AGENTS.md section 8
 * allows it for highlighting): the display blocks hold the text exactly as it was cut
 * into pieces, so a prose quote is an exact substring of a block and a table-row quote
 * equals a row's stored line. No normalisation, no splitting, no similarity.
 */
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type { PdfViewerNavigationResult } from './pdfEvidenceNavigation'
import type { DisplayBlock } from './viewerFiles'

/** Chris's chosen text (Q4, 20 Nov 2026). Do not change without his sign-off. */
export const EVIDENCE_WITHOUT_FILE_MESSAGE = 'This evidence was saved before files were recorded'

/** Proposal, awaiting sign-off. */
export const FILE_REMOVED_MESSAGE = 'This file is no longer part of the paper, so this evidence cannot be shown.'

/** Proposal, awaiting sign-off. */
export const TEXT_QUOTE_NOT_FOUND_MESSAGE = 'This quote could not be found in the file\'s text.'

/** Proposal, awaiting sign-off: a PDF file opened for page-less evidence whose quote was not located. */
export function pageLessQuoteNotLocatedMessage(fileName: string): string {
  return `Opened ${fileName}, but this quote could not be located in it.`
}

/** Proposal, awaiting sign-off: announced to screen readers when evidence opens another file's tab. */
export function openedFileAnnouncement(fileName: string): string {
  return `Opened ${fileName}`
}

export interface TextNavigationRequest {
  requestId: number
  fileId: string
  command: EvidenceNavigationCommand
}

/** The quote of a command as the evidence copied it (never the PDF search text). */
export function commandQuote(command: EvidenceNavigationCommand): string {
  return (
    command.anchor.snippet_text
    ?? command.anchor.sentence_text
    ?? command.anchor.normalized_text
    ?? ''
  ).trim()
}

function baseFileResult(
  command: EvidenceNavigationCommand,
  documentId: string | null,
): Omit<PdfViewerNavigationResult, 'status' | 'strategy' | 'locatorQuality' | 'degraded' | 'note'> {
  return {
    mode: command.mode,
    documentId,
    fileId: command.fileId,
    quote: commandQuote(command),
    pageHints: [],
    sectionTitle: command.sectionTitle,
    matchedQuery: null,
    matchedPage: null,
    matchesTotal: 0,
    currentMatch: 0,
    attemptedQueries: [],
  }
}

/** The result for evidence that names no file, or a file that is no longer a tab. */
export function buildEvidenceFileResult(
  command: EvidenceNavigationCommand,
  documentId: string | null,
  kind: 'evidence-without-file' | 'file-removed',
): PdfViewerNavigationResult {
  const message = kind === 'evidence-without-file' ? EVIDENCE_WITHOUT_FILE_MESSAGE : FILE_REMOVED_MESSAGE
  return {
    ...baseFileResult(command, documentId),
    status: kind,
    strategy: 'evidence-file',
    locatorQuality: 'unresolved',
    degraded: true,
    note: kind === 'evidence-without-file'
      ? 'The evidence names no file (saved before files were recorded); nothing was opened.'
      : `The evidence names file ${command.fileId}, which is not one of the paper's files; nothing was opened.`,
    curatorMessage: message,
  }
}

export interface TextQuoteLocation {
  blockIndex: number
  /** The table row, for a table block; null for prose. */
  rowIndex: number | null
  /** More than one place held the quote; the first in the evidence's section (or in reading order) was chosen. */
  chosenAmongSeveral: boolean
}

/**
 * Where a quote sits in a text file's blocks, by exact copied text, or null.
 *
 * Several matches: the first whose section path contains the evidence's section
 * name exactly, otherwise the first in reading order.
 */
export function locateQuoteInBlocks(
  blocks: readonly DisplayBlock[],
  quote: string,
  sectionTitle: string | null,
): TextQuoteLocation | null {
  if (!quote) {
    return null
  }
  const matches: Array<{ blockIndex: number; rowIndex: number | null; sectionPath: string[] }> = []
  blocks.forEach((block, blockIndex) => {
    if (block.kind === 'table') {
      block.rows?.forEach((row, rowIndex) => {
        if (row.line === quote) {
          matches.push({ blockIndex, rowIndex, sectionPath: block.section_path })
        }
      })
      return
    }
    if (block.text !== null && block.text.includes(quote)) {
      matches.push({ blockIndex, rowIndex: null, sectionPath: block.section_path })
    }
  })
  if (matches.length === 0) {
    return null
  }
  const inSection = sectionTitle === null
    ? undefined
    : matches.find((match) => match.sectionPath.includes(sectionTitle))
  const chosen = inSection ?? matches[0]!
  return {
    blockIndex: chosen.blockIndex,
    rowIndex: chosen.rowIndex,
    chosenAmongSeveral: matches.length > 1,
  }
}

/** The navigation result for a text file, after locating the quote in its blocks. */
export function buildTextNavigationResult(
  request: TextNavigationRequest,
  documentId: string | null,
  location: TextQuoteLocation | null,
): PdfViewerNavigationResult {
  const base = baseFileResult(request.command, documentId)
  if (!location) {
    return {
      ...base,
      status: 'not-found',
      strategy: 'text-exact',
      locatorQuality: 'unresolved',
      degraded: true,
      attemptedQueries: base.quote ? [base.quote] : [],
      note: 'The quote is not an exact part of any block of this text file.',
      curatorMessage: TEXT_QUOTE_NOT_FOUND_MESSAGE,
    }
  }
  return {
    ...base,
    status: 'matched',
    strategy: 'text-exact',
    locatorQuality: 'exact_quote',
    degraded: false,
    matchedQuery: base.quote,
    matchesTotal: 1,
    currentMatch: 1,
    attemptedQueries: [base.quote],
    note: location.chosenAmongSeveral
      ? 'The quote appears more than once in this text file; the first place in the evidence\'s section (or in reading order) is highlighted.'
      : 'The quote was found exactly in this text file.',
  }
}

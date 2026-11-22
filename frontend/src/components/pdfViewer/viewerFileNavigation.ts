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
 * allows it for highlighting): every stored piece is an exact slice of the display
 * blocks' texts joined by a blank line (P9a's guarantee), so a quote, which is an
 * exact slice of a piece, is found by plain substring search over that joined text.
 * No normalisation, no splitting, no similarity.
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

/** Proposal, awaiting sign-off: the quote was not found, and the evidence's section is shown instead. */
export const TEXT_QUOTE_SECTION_SHOWN_MESSAGE = 'This quote could not be found in the file\'s text. Its section is shown instead.'

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

/** One block the located quote covers, and for a table the rows it covers. */
export interface TextQuoteBlockSpan {
  blockIndex: number
  /** Indexes of the table rows the quote covers; null for a block that is not a table. */
  rowIndexes: number[] | null
}

export interface TextQuoteLocation {
  /** The blocks (and table rows) the quote covers, in reading order. */
  blocks: TextQuoteBlockSpan[]
  /** The quote appears more than once; one place was chosen (see locateQuoteInBlocks). */
  chosenAmongSeveral: boolean
}

/** The separator the server's guarantee uses: every stored piece is an exact slice of the joined texts. */
export const TEXT_BLOCK_SEPARATOR = '\n\n'

/** Where each row line of a table block sits inside the block's text (its lines joined by "\n"). */
function tableRowOffsets(block: DisplayBlock): Array<{ start: number; end: number }> {
  const offsets: Array<{ start: number; end: number }> = []
  let cursor = 0
  for (const row of block.rows ?? []) {
    const start = block.text.indexOf(row.line, cursor)
    if (start < 0) {
      throw new Error('A table row line is not part of its table block\'s text')
    }
    offsets.push({ start, end: start + row.line.length })
    cursor = start + row.line.length
  }
  return offsets
}

/** The section an evidence record names, as evidence stores it. */
export interface EvidenceSection {
  /** The piece's parentSection, or its sectionTitle when it has none. */
  section: string
  subsection: string | null
}

/** The evidence's section from a navigation command, or null when it names none. */
export function commandSection(command: EvidenceNavigationCommand): EvidenceSection | null {
  const section = command.anchor.section_title?.trim() || null
  if (!section) {
    return null
  }
  return { section, subsection: command.anchor.subsection_title?.trim() || null }
}

/**
 * Whether a block sits in the evidence's section. Evidence stores a piece's
 * parentSection (or sectionTitle when there is none) and its subsection; the text
 * route gives each block exactly those names (P9a), so this is an exact comparison.
 */
export function blockIsInSection(block: DisplayBlock, section: EvidenceSection): boolean {
  if ((block.parent_section ?? block.section_title) !== section.section) {
    return false
  }
  return section.subsection === null || block.subsection === section.subsection
}

function blockSpansFor(
  blocks: readonly DisplayBlock[],
  start: number,
  end: number,
): TextQuoteBlockSpan[] {
  const spans: TextQuoteBlockSpan[] = []
  let blockStart = 0
  blocks.forEach((block, blockIndex) => {
    const blockEnd = blockStart + block.text.length
    if (start < blockEnd && end > blockStart) {
      if (block.kind === 'table') {
        const rowIndexes = tableRowOffsets(block)
          .map((offset, rowIndex) => ({ rowIndex, start: blockStart + offset.start, end: blockStart + offset.end }))
          .filter((row) => start < row.end && end > row.start)
          .map((row) => row.rowIndex)
        spans.push({ blockIndex, rowIndexes })
      } else {
        spans.push({ blockIndex, rowIndexes: null })
      }
    }
    blockStart = blockEnd + TEXT_BLOCK_SEPARATOR.length
  })
  return spans
}

/**
 * Where a quote sits in a text file's blocks, or null.
 *
 * Exact comparison against the text exactly as the server serves it (the text the
 * pieces were cut from, already cleaned up on the server); nothing is normalised or
 * loosened in the browser. A quote can cross blocks, so the blocks' texts are
 * searched joined by a blank line. Several places: the first that starts in the
 * evidence's section, otherwise the first in reading order.
 *
 * Known gap (P9a): inside an element longer than 1,500 characters that the chunker
 * cut at a sentence boundary, the piece has one space where the block keeps the
 * original whitespace; a quote across that cut is not found here, by design.
 */
export function locateQuoteInBlocks(
  blocks: readonly DisplayBlock[],
  quote: string,
  section: EvidenceSection | null,
): TextQuoteLocation | null {
  if (!quote) {
    return null
  }
  const joined = blocks.map((block) => block.text).join(TEXT_BLOCK_SEPARATOR)
  const places: TextQuoteBlockSpan[][] = []
  for (let start = joined.indexOf(quote); start >= 0; start = joined.indexOf(quote, start + 1)) {
    places.push(blockSpansFor(blocks, start, start + quote.length))
  }
  if (places.length === 0) {
    return null
  }
  const inSection = section === null
    ? undefined
    : places.find((spans) => blockIsInSection(blocks[spans[0]!.blockIndex]!, section))
  return {
    blocks: inSection ?? places[0]!,
    chosenAmongSeveral: places.length > 1,
  }
}

/** The first block of the evidence's section, for the "quote not found" path; null when none. */
export function locateSectionInBlocks(
  blocks: readonly DisplayBlock[],
  section: EvidenceSection | null,
): number | null {
  if (section === null) {
    return null
  }
  const index = blocks.findIndex((block) => blockIsInSection(block, section))
  return index >= 0 ? index : null
}

/** The navigation result for a text file, after locating the quote in its blocks. */
export function buildTextNavigationResult(
  request: TextNavigationRequest,
  documentId: string | null,
  location: TextQuoteLocation | null,
  sectionBlockIndex: number | null,
): PdfViewerNavigationResult {
  const base = baseFileResult(request.command, documentId)
  if (!location && sectionBlockIndex !== null) {
    return {
      ...base,
      status: 'section-fallback',
      strategy: 'text-exact',
      locatorQuality: 'section_only',
      degraded: true,
      attemptedQueries: base.quote ? [base.quote] : [],
      note: 'The quote is not an exact part of this text file; the first block of the evidence\'s section is shown.',
      curatorMessage: TEXT_QUOTE_SECTION_SHOWN_MESSAGE,
    }
  }
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

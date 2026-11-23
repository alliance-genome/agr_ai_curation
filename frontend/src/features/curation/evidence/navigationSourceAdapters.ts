import type {
  CurationEvidenceRecord,
  DomainEnvelopeEvidenceAnchorProjection,
  EvidenceRecord,
  EvidenceSourceFragment,
} from '../types'
import type { EvidenceAnchor } from '../contracts'
import type { EvidenceNavigationCommand, TextEvidenceFragment, TextEvidenceTarget } from './types'
import {
  buildQuoteCentricEvidenceNavigationCommand,
  normalizeEvidenceNavigationText,
} from './navigationCommandBuilder'
import { normalizeEvidenceSectionHierarchy } from './navigationPresentation'
import { evidenceViewerSearchText } from './evidenceViewerSearchText'

function buildAnchorToken(value: string, fallback: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)

  return normalized || fallback
}

/** Chat evidence has no stored anchor id; this one is stable per file, piece, entity and quote. */
function buildChatEvidenceAnchorId(evidenceRecord: EvidenceRecord): string {
  return [
    'chat-evidence',
    evidenceRecord.file_id ?? 'no-file',
    evidenceRecord.chunk_id,
    buildAnchorToken(evidenceRecord.entity, 'entity'),
    buildAnchorToken(evidenceRecord.verified_quote, 'quote'),
  ].join(':')
}

function rawName(value: string | null | undefined): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

/** The stored fragments with text, unchanged; null when the record has none. */
function textEvidenceFragments(
  fragments: readonly EvidenceSourceFragment[] | null | undefined,
): TextEvidenceFragment[] | null {
  const withText = (fragments ?? []).filter(
    (fragment): fragment is EvidenceSourceFragment & { text: string } => (
      typeof fragment.text === 'string' && fragment.text.length > 0
    ),
  )
  if (withText.length === 0) {
    return null
  }
  return withText.map((fragment) => ({
    text: fragment.text,
    section: rawName(fragment.section),
    subsection: rawName(fragment.subsection),
  }))
}

function buildTextEvidence(
  fragments: readonly EvidenceSourceFragment[] | null | undefined,
  section: string | null | undefined,
  subsection: string | null | undefined,
): TextEvidenceTarget {
  return {
    fragments: textEvidenceFragments(fragments),
    section: rawName(section),
    subsection: rawName(subsection),
  }
}

export function deriveNavigationQuoteFromAnchor(
  anchor: Pick<
    EvidenceAnchor,
    'sentence_text' | 'snippet_text' | 'normalized_text' | 'viewer_search_text'
  >,
): string | null {
  return normalizeEvidenceNavigationText(anchor.sentence_text)
    ?? normalizeEvidenceNavigationText(anchor.snippet_text)
    ?? normalizeEvidenceNavigationText(anchor.normalized_text)
    ?? normalizeEvidenceNavigationText(anchor.viewer_search_text)
}

function buildAnchorContextNavigationCommand(args: {
  anchorId: string
  fileId: string | null
  textEvidence: TextEvidenceTarget
  anchor: EvidenceAnchor
  mode: EvidenceNavigationCommand['mode']
}): EvidenceNavigationCommand | null {
  const normalizedHierarchy = normalizeEvidenceSectionHierarchy(
    args.anchor.section_title,
    args.anchor.subsection_title,
  )
  const searchText = normalizeEvidenceNavigationText(args.anchor.viewer_search_text)
  const pageNumber = args.anchor.page_number ?? null
  const sectionTitle = normalizedHierarchy.sectionTitle
  const hasNavigableContext = searchText !== null
    || pageNumber !== null
    || sectionTitle !== null
    || args.anchor.locator_quality === 'document_only'

  if (!hasNavigableContext) {
    return null
  }

  return {
    anchorId: args.anchorId,
    fileId: args.fileId,
    textEvidence: args.textEvidence,
    anchor: {
      ...args.anchor,
      section_title: normalizedHierarchy.sectionTitle,
      subsection_title: normalizedHierarchy.subsectionTitle,
    },
    searchText,
    pageNumber,
    sectionTitle,
    mode: args.mode,
  }
}

export function buildNavigationCommandFromChatEvidenceRecord(
  evidenceRecord: EvidenceRecord,
  mode: EvidenceNavigationCommand['mode'] = 'select',
): EvidenceNavigationCommand {
  const quote = evidenceRecord.verified_quote.trim()

  return buildQuoteCentricEvidenceNavigationCommand({
    anchorId: buildChatEvidenceAnchorId(evidenceRecord),
    fileId: evidenceRecord.file_id ?? null,
    textEvidence: buildTextEvidence(
      evidenceRecord.source_fragments,
      evidenceRecord.section,
      evidenceRecord.subsection,
    ),
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      viewer_highlightable: true,
      page_number: evidenceRecord.page,
      section_title: evidenceRecord.section,
      subsection_title: evidenceRecord.subsection ?? null,
      figure_reference: evidenceRecord.figure_reference ?? null,
      chunk_ids: [evidenceRecord.chunk_id],
    },
    quote,
    searchText: evidenceViewerSearchText(evidenceRecord.source_fragments),
    pageNumber: evidenceRecord.page,
    sectionTitle: evidenceRecord.section,
    mode,
  })
}

export function buildNavigationCommandFromCurationEvidenceRecord(
  evidenceRecord: CurationEvidenceRecord,
  mode: EvidenceNavigationCommand['mode'] = 'select',
): EvidenceNavigationCommand | null {
  const quote = deriveNavigationQuoteFromAnchor(evidenceRecord.anchor)
  const fileId = evidenceRecord.file_id ?? null
  // A review-grid anchor carries no fragments: a text file locates its quote.
  const textEvidence = buildTextEvidence(
    null,
    evidenceRecord.anchor.section_title,
    evidenceRecord.anchor.subsection_title,
  )
  if (!quote) {
    return buildAnchorContextNavigationCommand({
      anchorId: evidenceRecord.anchor_id,
      fileId,
      textEvidence,
      anchor: evidenceRecord.anchor,
      mode,
    })
  }

  return buildQuoteCentricEvidenceNavigationCommand({
    anchorId: evidenceRecord.anchor_id,
    fileId,
    textEvidence,
    anchor: evidenceRecord.anchor,
    quote,
    pageNumber: evidenceRecord.anchor.page_number ?? null,
    sectionTitle: evidenceRecord.anchor.section_title ?? null,
    mode,
  })
}

/** The envelope evidence record's source fragments, when the projection carries it. */
function sourceRecordFragments(
  projection: DomainEnvelopeEvidenceAnchorProjection,
): EvidenceSourceFragment[] | null {
  const sourceRecord = projection.metadata?.source_record
  if (!sourceRecord || typeof sourceRecord !== 'object') {
    return null
  }
  const fragments = (sourceRecord as { source_fragments?: unknown }).source_fragments
  return Array.isArray(fragments) ? fragments as EvidenceSourceFragment[] : null
}

export function deriveNavigationQuoteFromEnvelopeEvidenceProjection(
  projection: DomainEnvelopeEvidenceAnchorProjection,
): string | null {
  return normalizeEvidenceNavigationText(projection.quote)
    ?? normalizeEvidenceNavigationText(projection.anchor.sentence_text)
    ?? normalizeEvidenceNavigationText(projection.anchor.snippet_text)
    ?? normalizeEvidenceNavigationText(projection.anchor.normalized_text)
}

export function buildNavigationCommandFromEnvelopeEvidenceProjection(
  projection: DomainEnvelopeEvidenceAnchorProjection,
  mode: EvidenceNavigationCommand['mode'] = 'select',
): EvidenceNavigationCommand | null {
  const anchor: EvidenceAnchor = {
    ...projection.anchor,
    page_number: projection.page_number ?? projection.anchor.page_number ?? null,
    section_title:
      normalizeEvidenceNavigationText(projection.section_title)
      ?? projection.anchor.section_title,
    subsection_title:
      normalizeEvidenceNavigationText(projection.subsection_title)
      ?? projection.anchor.subsection_title,
  }
  const quote = deriveNavigationQuoteFromEnvelopeEvidenceProjection(projection)
  const fileId = projection.file_id ?? null
  const textEvidence = buildTextEvidence(
    sourceRecordFragments(projection),
    projection.section_title ?? projection.anchor.section_title,
    projection.subsection_title ?? projection.anchor.subsection_title,
  )

  if (!quote) {
    return buildAnchorContextNavigationCommand({
      anchorId: projection.anchor_id,
      fileId,
      textEvidence,
      anchor,
      mode,
    })
  }

  return buildQuoteCentricEvidenceNavigationCommand({
    anchorId: projection.anchor_id,
    fileId,
    textEvidence,
    anchor,
    quote,
    searchText: evidenceViewerSearchText(sourceRecordFragments(projection)),
    pageNumber: anchor.page_number,
    sectionTitle: anchor.section_title,
    mode,
  })
}

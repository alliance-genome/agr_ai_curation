import type { EvidenceRecord, CurationEvidenceRecord } from '../types'
import type { EvidenceAnchor } from '../contracts'
import type { EvidenceNavigationCommand } from './types'
import {
  buildQuoteCentricEvidenceNavigationCommand,
  normalizeEvidenceNavigationText,
} from './navigationCommandBuilder'
import { normalizeEvidenceSectionHierarchy } from './navigationPresentation'

function buildAnchorToken(value: string, fallback: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)

  return normalized || fallback
}

function buildChatEvidenceAnchorId(evidenceRecord: EvidenceRecord): string {
  return [
    'chat-evidence',
    evidenceRecord.chunk_id,
    `p${evidenceRecord.page}`,
    buildAnchorToken(evidenceRecord.entity, 'entity'),
    buildAnchorToken(evidenceRecord.verified_quote, 'quote'),
  ].join(':')
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
  if (!quote) {
    return buildAnchorContextNavigationCommand({
      anchorId: evidenceRecord.anchor_id,
      anchor: evidenceRecord.anchor,
      mode,
    })
  }

  return buildQuoteCentricEvidenceNavigationCommand({
    anchorId: evidenceRecord.anchor_id,
    anchor: evidenceRecord.anchor,
    quote,
    pageNumber: evidenceRecord.anchor.page_number ?? null,
    sectionTitle: evidenceRecord.anchor.section_title ?? null,
    mode,
  })
}

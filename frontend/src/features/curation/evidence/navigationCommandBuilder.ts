import type { EvidenceAnchor } from '../contracts'
import type { EvidenceNavigationCommand, TextEvidenceTarget } from './types'
import { normalizeEvidenceSectionHierarchy } from './navigationPresentation'

function normalizeText(value: string | null | undefined): string | null {
  const normalized = value?.trim() ?? ''
  return normalized.length > 0 ? normalized : null
}

type QuoteCentricAnchorInput = Omit<
  EvidenceAnchor,
  'snippet_text' | 'sentence_text' | 'normalized_text' | 'viewer_search_text'
>
  & Partial<
    Pick<
      EvidenceAnchor,
      'snippet_text' | 'sentence_text' | 'normalized_text' | 'viewer_search_text'
    >
  >

export function buildQuoteCentricEvidenceNavigationCommand(args: {
  anchorId: string
  fileId: string | null
  textEvidence: TextEvidenceTarget
  anchor: QuoteCentricAnchorInput
  quote: string
  /** What the viewer searches the PDF for; the quote itself unless it differs (table rows). */
  searchText?: string | null
  pageNumber?: number | null
  sectionTitle?: string | null
  mode: EvidenceNavigationCommand['mode']
}): EvidenceNavigationCommand {
  const quote = args.quote.trim()
  const searchText = args.searchText?.trim() || quote
  const normalizedHierarchy = normalizeEvidenceSectionHierarchy(
    args.anchor.section_title ?? null,
    args.anchor.subsection_title ?? null,
  )
  const anchor: EvidenceAnchor = {
    ...args.anchor,
    // Quote-centric viewer navigation should describe the command we are issuing
    // now, not preserve historical anchor-quality metadata from persistence.
    locator_quality: 'exact_quote',
    snippet_text: quote,
    sentence_text: quote,
    normalized_text: quote,
    viewer_search_text: searchText,
    viewer_highlightable: args.anchor.viewer_highlightable ?? true,
    section_title: normalizedHierarchy.sectionTitle,
    subsection_title: normalizedHierarchy.subsectionTitle,
  }
  return {
    anchorId: args.anchorId,
    fileId: args.fileId,
    textEvidence: args.textEvidence,
    anchor,
    searchText,
    pageNumber: args.pageNumber ?? anchor.page_number ?? null,
    sectionTitle: normalizeText(anchor.section_title),
    mode: args.mode,
  }
}

export function normalizeEvidenceNavigationText(
  value: string | null | undefined,
): string | null {
  return normalizeText(value)
}

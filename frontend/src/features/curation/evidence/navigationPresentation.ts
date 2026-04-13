import type { EvidenceNavigationCommand } from './types'
import { dispatchPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'

const SECTION_PATH_SEPARATOR = /\s[>›]\s/g

function normalizeText(value: string | null | undefined): string | null {
  const normalized = value?.trim() ?? ''
  return normalized.length > 0 ? normalized : null
}

export function normalizeEvidenceSectionHierarchy(
  sectionTitle: string | null | undefined,
  subsectionTitle: string | null | undefined,
): {
  sectionTitle: string | null
  subsectionTitle: string | null
} {
  const normalizedSectionTitle = normalizeText(sectionTitle)
  const normalizedSubsectionTitle = normalizeText(subsectionTitle)

  if (!normalizedSectionTitle) {
    return {
      sectionTitle: null,
      subsectionTitle: normalizedSubsectionTitle,
    }
  }

  const pathParts = normalizedSectionTitle
    .split(SECTION_PATH_SEPARATOR)
    .map((part) => part.trim())
    .filter((part) => part.length > 0)

  if (pathParts.length <= 1) {
    return {
      sectionTitle: normalizedSectionTitle,
      subsectionTitle: normalizedSubsectionTitle,
    }
  }

  const derivedSectionTitle = pathParts.slice(0, -1).join(' › ') || null
  const derivedSubsectionTitle = pathParts.at(-1) ?? null

  if (normalizedSubsectionTitle) {
    return {
      sectionTitle: derivedSectionTitle,
      subsectionTitle: normalizedSubsectionTitle,
    }
  }

  return {
    sectionTitle: derivedSectionTitle,
    subsectionTitle: derivedSubsectionTitle,
  }
}

export function buildEvidenceLocationLabel(args: {
  pageNumber?: number | null
  sectionTitle?: string | null
  subsectionTitle?: string | null
}): string {
  const { sectionTitle, subsectionTitle } = normalizeEvidenceSectionHierarchy(
    args.sectionTitle,
    args.subsectionTitle,
  )
  const sectionLabel = [sectionTitle, subsectionTitle]
    .filter((part): part is string => Boolean(part))
    .join(' › ')

  if (args.pageNumber != null && sectionLabel) {
    return `p. ${args.pageNumber} · ${sectionLabel}`
  }

  if (args.pageNumber != null) {
    return `p. ${args.pageNumber}`
  }

  if (sectionLabel) {
    return sectionLabel
  }

  return 'Location unavailable'
}

export function dispatchEvidenceNavigationCommand(
  command: EvidenceNavigationCommand,
  debugContext?: Record<string, unknown>,
): void {
  if (window.__pdfViewerEvidenceDebug?.enabled) {
    console.info('[PDF EVIDENCE DEBUG] Dispatching shared evidence navigation', {
      anchorId: command.anchorId,
      mode: command.mode,
      pageNumber: command.pageNumber,
      sectionTitle: command.sectionTitle,
      subsectionTitle: command.anchor.subsection_title ?? null,
      searchText: command.searchText,
      ...debugContext,
    })
  }

  dispatchPDFViewerNavigateEvidence(command)
}

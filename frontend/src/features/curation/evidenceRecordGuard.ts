import type { EvidenceRecord } from './types'

/**
 * Whether a served value is a chat evidence record the chat can show.
 *
 * Records made from 0.12.0 name their file (`file_id`) and may have no page
 * (`page: null`, a file with no pages to cite) and no section (`section: null`,
 * text before a file's first heading). Records saved before have no `file_id`
 * and always a page and a section. A page-less or section-less record without a
 * file is malformed.
 */
function isNonBlank(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

export function isEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }
  const record = value as Record<string, unknown>
  // Mirrors the backend's evidence_record_page and evidence_record_section (#1109).
  const namesFile = isNonBlank(record.file_id)
  const hasPage = typeof record.page === 'number' && Number.isInteger(record.page) && record.page > 0
  const hasSection = isNonBlank(record.section)
  const sectionEmpty = record.section === null || (typeof record.section === 'string' && !hasSection)
  return (
    typeof record.entity === 'string'
    && typeof record.verified_quote === 'string'
    && (hasPage || (namesFile && record.page === null))
    && (hasSection || (namesFile && sectionEmpty))
    && typeof record.chunk_id === 'string'
  )
}

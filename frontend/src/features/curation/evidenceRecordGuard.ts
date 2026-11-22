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
export function isEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }
  const record = value as Record<string, unknown>
  const namesFile = typeof record.file_id === 'string'
  const hasPage = typeof record.page === 'number' && Number.isFinite(record.page)
  const hasSection = typeof record.section === 'string'
  return (
    typeof record.entity === 'string'
    && typeof record.verified_quote === 'string'
    && (hasPage || (namesFile && record.page === null))
    && (hasSection || (namesFile && record.section === null))
    && typeof record.chunk_id === 'string'
  )
}

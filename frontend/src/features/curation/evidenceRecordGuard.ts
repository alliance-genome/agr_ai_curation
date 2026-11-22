import type { EvidenceRecord } from './types'

/**
 * Whether a served value is a chat evidence record the chat can show.
 *
 * Records made from 0.12.0 name their file (`file_id`) and may have no page
 * (`page: null`, a file with no pages to cite). Records saved before have no
 * `file_id` and always a page. A page-less record without a file is malformed.
 */
export function isEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }
  const record = value as Record<string, unknown>
  const hasPage = typeof record.page === 'number' && Number.isFinite(record.page)
  const pageLessWithFile = record.page === null && typeof record.file_id === 'string'
  return (
    typeof record.entity === 'string'
    && typeof record.verified_quote === 'string'
    && (hasPage || pageLessWithFile)
    && typeof record.section === 'string'
    && typeof record.chunk_id === 'string'
  )
}

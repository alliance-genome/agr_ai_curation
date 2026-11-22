import { describe, expect, it } from 'vitest'

import { isEvidenceRecord } from './evidenceRecordGuard'

const base = {
  entity: 'XS0401',
  verified_quote: '| XS0401 | 25 C |',
  section: 'Sheet 1',
  chunk_id: 'chunk-36',
}

describe('isEvidenceRecord', () => {
  it('keeps a record saved before files were recorded (no file id, a page)', () => {
    expect(isEvidenceRecord({ ...base, page: 3 })).toBe(true)
  })

  it('keeps a page-less record that names its file (P8b shape)', () => {
    expect(isEvidenceRecord({
      ...base,
      page: null,
      file_id: 'aaaaaaaa-0000-0000-0000-000000000002',
      file_name: 'Table_S1.xlsx',
    })).toBe(true)
  })

  it('keeps a record that names its file and has no section (text before the file\'s first heading)', () => {
    expect(isEvidenceRecord({
      ...base,
      section: null,
      page: null,
      file_id: 'aaaaaaaa-0000-0000-0000-000000000002',
      file_name: 'Table_S1.xlsx',
    })).toBe(true)
  })

  it('drops a section-less record without a file', () => {
    expect(isEvidenceRecord({ ...base, page: 3, section: null })).toBe(false)
  })

  it('drops a page-less record without a file, and records missing required fields', () => {
    expect(isEvidenceRecord({ ...base, page: null })).toBe(false)
    expect(isEvidenceRecord({ ...base })).toBe(false)
    expect(isEvidenceRecord({ ...base, page: 3, chunk_id: undefined })).toBe(false)
    expect(isEvidenceRecord(null)).toBe(false)
  })
})

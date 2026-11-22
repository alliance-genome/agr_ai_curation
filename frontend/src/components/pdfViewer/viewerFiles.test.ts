import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ViewerFilesContractError,
  defaultActiveFileId,
  fetchViewerFiles,
  parseViewerFile,
  parseViewerFilesPayload,
  viewerTabFiles,
} from './viewerFiles'
import {
  FOUR_FILES_PAYLOAD,
  MAIN_FILE_ID,
  PDF_SUPPLEMENT_FILE_ID,
  SUPPLEMENTS_ONLY_PAYLOAD,
  TEXT_SUPPLEMENT_FILE_ID,
} from './viewerFilesFixtures'

describe('parseViewerFilesPayload', () => {
  it('accepts the files route payload in the shape the backend returns', () => {
    const payload = parseViewerFilesPayload(FOUR_FILES_PAYLOAD)

    expect(payload.document_id).toBe(FOUR_FILES_PAYLOAD.document_id)
    expect(payload.files.map((file) => file.display_mode)).toEqual([
      'local_pdf',
      'text',
      'local_pdf',
      'text',
    ])
  })

  it('accepts a paper with supplements only', () => {
    const payload = parseViewerFilesPayload(SUPPLEMENTS_ONLY_PAYLOAD)

    expect(payload.files).toHaveLength(1)
    expect(payload.files[0]?.role).toBe('supplement')
  })

  it.each([
    ['an unknown display mode', { display_mode: 'image' }],
    ['an unknown role', { role: 'appendix' }],
    ['an unknown status', { status: 'converting' }],
    ['a text file with a page count', { page_count: 3 }],
    ['a text file with a PDF URL', { content_url: '/api/pdf-viewer/documents/d/files/f/content' }],
    ['a missing file id', { file_id: undefined }],
    ['a negative tab order', { tab_order: -1 }],
  ])('rejects %s with a named error', (_label, override) => {
    const textFile = { ...FOUR_FILES_PAYLOAD.files[1], ...override }

    expect(() => parseViewerFile(textFile)).toThrow(ViewerFilesContractError)
  })

  it('rejects a PDF file without a page count', () => {
    const pdfFile = { ...FOUR_FILES_PAYLOAD.files[0], page_count: null }

    expect(() => parseViewerFile(pdfFile)).toThrow(ViewerFilesContractError)
  })

  it('rejects a payload whose files are out of tab order', () => {
    const shuffled = {
      ...FOUR_FILES_PAYLOAD,
      files: [FOUR_FILES_PAYLOAD.files[1], FOUR_FILES_PAYLOAD.files[0]],
    }

    expect(() => parseViewerFilesPayload(shuffled)).toThrow(ViewerFilesContractError)
  })
})

describe('viewerTabFiles and defaultActiveFileId', () => {
  it('gives a tab to every file with a URL, in tab order', () => {
    const { files } = parseViewerFilesPayload(FOUR_FILES_PAYLOAD)

    expect(viewerTabFiles(files).map((file) => file.file_id)).toEqual([
      MAIN_FILE_ID,
      TEXT_SUPPLEMENT_FILE_ID,
      PDF_SUPPLEMENT_FILE_ID,
    ])
  })

  it('opens the main file first when it has a URL', () => {
    const { files } = parseViewerFilesPayload(FOUR_FILES_PAYLOAD)

    expect(defaultActiveFileId(viewerTabFiles(files))).toBe(MAIN_FILE_ID)
  })

  it('opens the first file with a URL when the paper has no main file', () => {
    const { files } = parseViewerFilesPayload(SUPPLEMENTS_ONLY_PAYLOAD)

    expect(defaultActiveFileId(viewerTabFiles(files))).toBe(SUPPLEMENTS_ONLY_PAYLOAD.files[0]?.file_id)
  })

  it('has no active file when no file has a URL yet', () => {
    const pendingOnly = parseViewerFilesPayload({
      ...FOUR_FILES_PAYLOAD,
      files: [FOUR_FILES_PAYLOAD.files[3]],
    })

    expect(defaultActiveFileId(viewerTabFiles(pendingOnly.files))).toBeNull()
  })
})

describe('fetchViewerFiles', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reads the files route of the paper', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(FOUR_FILES_PAYLOAD), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const payload = await fetchViewerFiles(FOUR_FILES_PAYLOAD.document_id)

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/pdf-viewer/documents/${FOUR_FILES_PAYLOAD.document_id}/files`,
      expect.objectContaining({ credentials: 'same-origin' }),
    )
    expect(payload.files).toHaveLength(4)
  })

  it('fails loudly when the route fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })))

    await expect(fetchViewerFiles('doc-missing')).rejects.toThrow('Failed to fetch the paper\'s files (404)')
  })
})

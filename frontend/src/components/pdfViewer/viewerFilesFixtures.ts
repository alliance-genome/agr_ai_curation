/**
 * Viewer payloads in the exact shapes the backend returns (ALL-1460, P9a, I5).
 *
 * Copied from the backend's own route tests
 * (`backend/tests/unit/api/test_pdf_viewer_files.py`): the same paper and file ids,
 * names, display modes, statuses, page counts and URLs. Made-up names.
 */
import type { ViewerFile, ViewerFilesPayload, ViewerTextPayload } from './viewerFiles'

export const DOCUMENT_ID = '22222222-2222-2222-2222-222222222222'
export const MAIN_FILE_ID = 'aaaaaaaa-0000-0000-0000-000000000001'
export const TEXT_SUPPLEMENT_FILE_ID = 'aaaaaaaa-0000-0000-0000-000000000002'
export const PDF_SUPPLEMENT_FILE_ID = 'aaaaaaaa-0000-0000-0000-000000000003'
export const PENDING_SUPPLEMENT_FILE_ID = 'aaaaaaaa-0000-0000-0000-000000000004'

export const contentUrl = (documentId: string, fileId: string): string =>
  `/api/pdf-viewer/documents/${documentId}/files/${fileId}/content`

export const textUrl = (documentId: string, fileId: string): string =>
  `/api/pdf-viewer/documents/${documentId}/files/${fileId}/text`

export const FOUR_FILES_PAYLOAD: ViewerFilesPayload = {
  document_id: DOCUMENT_ID,
  files: [
    {
      file_id: MAIN_FILE_ID,
      role: 'main',
      display_name: 'made-up-paper.pdf',
      display_mode: 'local_pdf',
      status: 'ready',
      tab_order: 0,
      page_count: 12,
      content_url: contentUrl(DOCUMENT_ID, MAIN_FILE_ID),
      text_url: null,
    },
    {
      file_id: TEXT_SUPPLEMENT_FILE_ID,
      role: 'supplement',
      display_name: 'Table_S1.xlsx',
      display_mode: 'text',
      status: 'ready',
      tab_order: 1,
      page_count: null,
      content_url: null,
      text_url: textUrl(DOCUMENT_ID, TEXT_SUPPLEMENT_FILE_ID),
    },
    {
      file_id: PDF_SUPPLEMENT_FILE_ID,
      role: 'supplement',
      display_name: 'Figure_S2.pdf',
      display_mode: 'local_pdf',
      status: 'ready',
      tab_order: 2,
      page_count: 4,
      content_url: contentUrl(DOCUMENT_ID, PDF_SUPPLEMENT_FILE_ID),
      text_url: null,
    },
    {
      file_id: PENDING_SUPPLEMENT_FILE_ID,
      role: 'supplement',
      display_name: 'Table_S3.docx',
      display_mode: 'text',
      status: 'pending',
      tab_order: 3,
      page_count: null,
      content_url: null,
      text_url: null,
    },
  ],
}

/** `test_files_list_of_a_supplements_only_paper_needs_no_main_file` (`new_supplements_only_paper`: one text supplement at tab 1). */
export const SUPPLEMENTS_ONLY_PAYLOAD: ViewerFilesPayload = {
  document_id: DOCUMENT_ID,
  files: [
    {
      file_id: TEXT_SUPPLEMENT_FILE_ID,
      role: 'supplement',
      display_name: 'Table_S1.xlsx',
      display_mode: 'text',
      status: 'ready',
      tab_order: 1,
      page_count: null,
      content_url: null,
      text_url: textUrl(DOCUMENT_ID, TEXT_SUPPLEMENT_FILE_ID),
    },
  ],
}

/** `test_text_returns_the_files_display_blocks`. */
export const TEXT_SUPPLEMENT_BLOCKS: ViewerTextPayload = {
  document_id: DOCUMENT_ID,
  file_id: TEXT_SUPPLEMENT_FILE_ID,
  display_name: 'Table_S1.xlsx',
  blocks: [
    { kind: 'heading', text: 'Sheet 1', level: 1, section_path: ['Sheet 1'], rows: null },
    {
      kind: 'table',
      text: '| Strain | Temp |\n|---|---|\n| XS0401 | 25 C |',
      level: null,
      section_path: ['Sheet 1'],
      rows: [
        { line: '| Strain | Temp |', cells: ['Strain', 'Temp'], is_header: true },
        { line: '| XS0401 | 25 C |', cells: ['XS0401', '25 C'], is_header: false },
      ],
    },
    {
      kind: 'paragraph',
      text: 'Kept at two temperatures.',
      level: null,
      section_path: ['Sheet 1'],
      rows: null,
    },
  ],
}

/** One ready main PDF, as every paper on main has today. */
export function singleMainPdfFile(args: {
  documentId?: string
  fileId?: string
  displayName?: string
  pageCount?: number
} = {}): ViewerFile {
  const documentId = args.documentId ?? DOCUMENT_ID
  const fileId = args.fileId ?? MAIN_FILE_ID
  return {
    file_id: fileId,
    role: 'main',
    display_name: args.displayName ?? 'made-up-paper.pdf',
    display_mode: 'local_pdf',
    status: 'ready',
    tab_order: 0,
    page_count: args.pageCount ?? 12,
    content_url: contentUrl(documentId, fileId),
    text_url: null,
  }
}

/** One ready main text file (a text-imported paper). */
export function singleMainTextFile(args: {
  documentId?: string
  fileId?: string
  displayName?: string
} = {}): ViewerFile {
  const documentId = args.documentId ?? DOCUMENT_ID
  const fileId = args.fileId ?? MAIN_FILE_ID
  return {
    file_id: fileId,
    role: 'main',
    display_name: args.displayName ?? 'made-up-paper.md',
    display_mode: 'text',
    status: 'ready',
    tab_order: 0,
    page_count: null,
    content_url: null,
    text_url: textUrl(documentId, fileId),
  }
}

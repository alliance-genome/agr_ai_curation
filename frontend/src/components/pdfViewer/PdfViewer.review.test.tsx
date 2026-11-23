/**
 * The independent review's probes for P9b (ALL-1460), adopted as tests, and the fixes
 * they led to. Evidence records are in the record_evidence writer's shape: the quote
 * is the fragment texts joined by a blank line; each fragment keeps its exact text.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import { buildNavigationCommandFromChatEvidenceRecord } from '@/features/curation/evidence/navigationSourceAdapters'
import { fuzzyMatchPdfEvidenceQuote } from '@/features/curation/services/pdfEvidenceMatcherService'
import { DOCUMENT_LOAD_COMPLETE_EVENT, DOCUMENT_LOAD_ERROR_EVENT } from '@/features/documents/documentLoadEvents'
import PdfViewer from './PdfViewer'
import { HOME_PDF_VIEWER_OWNER, dispatchPDFViewerNavigateEvidence, showPaperFilesInViewer } from './pdfEvents'
import { TextFileBlocks } from './TextFileBlocks'
import { parseViewerFile, type DisplayBlock, type ViewerFile } from './viewerFiles'
import {
  DOCUMENT_ID,
  FOUR_FILES_PAYLOAD,
  MAIN_FILE_ID,
  PDF_SUPPLEMENT_FILE_ID,
  PENDING_SUPPLEMENT_FILE_ID,
  TEXT_SUPPLEMENT_BLOCKS,
  TEXT_SUPPLEMENT_FILE_ID,
  singleMainPdfFile,
  textUrl,
} from './viewerFilesFixtures'

vi.mock('@/features/curation/services/pdfEvidenceMatcherService', () => ({
  fuzzyMatchPdfEvidenceQuote: vi.fn(),
}))

const MAIN_PAGES = ['Main paper page one.', 'Main paper page two mentions XS0401 too.']

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

let textFetches = 0
function stubViewerFetch(options: { textBlocks?: DisplayBlock[]; headStatus?: (url: string) => number } = {}) {
  textFetches = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'HEAD') return new Response(null, { status: options.headStatus?.(url) ?? 200 })
    if (url === textUrl(DOCUMENT_ID, TEXT_SUPPLEMENT_FILE_ID)) {
      textFetches += 1
      return jsonResponse({ ...TEXT_SUPPLEMENT_BLOCKS, blocks: options.textBlocks ?? TEXT_SUPPLEMENT_BLOCKS.blocks })
    }
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function installPdfApp(pages: string[], startPage = 1) {
  const iframe = screen.getByTitle('PDF Viewer') as HTMLIFrameElement
  const pdfDocument = { numPages: pages.length }
  let currentPageNumber = startPage
  const app = {
    eventBus: { on: vi.fn(), off: vi.fn(), dispatch: vi.fn() },
    findController: { _pageContents: pages, selected: { pageIdx: -1, matchIdx: -1 }, pageMatches: [], pageMatchesLength: [] },
    pdfViewer: {
      get currentPageNumber() { return currentPageNumber },
      set currentPageNumber(value: number) { currentPageNumber = value },
      currentScaleValue: 'auto',
      pdfDocument,
    },
    pdfDocument,
  }
  Object.defineProperty(iframe, 'contentWindow', {
    configurable: true,
    value: { document: document.implementation.createHTMLDocument('pdf-frame'), PDFViewerApplication: app },
  })
  fireEvent.load(iframe)
  return { app }
}

function chatCommand(overrides: Partial<Parameters<typeof buildNavigationCommandFromChatEvidenceRecord>[0]>): EvidenceNavigationCommand {
  return buildNavigationCommandFromChatEvidenceRecord({
    entity: 'XS0401',
    verified_quote: 'Strain XS0401 was kept at 25 C.',
    page: null,
    section: 'Results',
    chunk_id: 'chunk-36',
    ...overrides,
  })
}

/** A two-row record as record_evidence writes it for the Excel supplement. */
function twoRowRecord(rows: [string, string]): EvidenceNavigationCommand {
  return chatCommand({
    verified_quote: rows.join('\n\n'),
    section: 'Sheet 1',
    file_id: TEXT_SUPPLEMENT_FILE_ID,
    file_name: 'Table_S1.xlsx',
    source_fragments: rows.map((text) => ({
      text,
      span_type: 'table_row',
      section: 'Sheet 1',
      file_id: TEXT_SUPPLEMENT_FILE_ID,
      file_name: 'Table_S1.xlsx',
    })),
  })
}

async function showFourFilePaper(files: ViewerFile[] = FOUR_FILES_PAYLOAD.files) {
  render(<PdfViewer activeDocumentOwnerToken={HOME_PDF_VIEWER_OWNER} />)
  act(() => {
    showPaperFilesInViewer(DOCUMENT_ID, files, { ownerToken: HOME_PDF_VIEWER_OWNER })
  })
  await screen.findAllByRole('tab')
}

describe('P9b review fixes', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockReset()
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValue({
      found: false, strategy: 'rapidfuzz-single-page', score: 0, matchedPage: null, matchedQuery: null,
      matchedRange: null, pageRanges: [], crossPage: false, note: 'not found',
    } as never)
  })
  afterEach(() => { vi.unstubAllGlobals() })

  describe('F1: multi-span evidence in a text file', () => {
    it('R1c: two-row Excel evidence highlights both rows', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      act(() => { dispatchPDFViewerNavigateEvidence(twoRowRecord(['| Strain | Temp |', '| XS0401 | 25 C |'])) })

      await waitFor(() => expect(screen.getAllByRole('row', { current: true })).toHaveLength(2))
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('two-sentence evidence, separated by a space in the file, is found', async () => {
      stubViewerFetch({
        textBlocks: [{ ...TEXT_SUPPLEMENT_BLOCKS.blocks[2]!, text: 'Worms were grown. They were kept at 25 C.' }],
      })
      await showFourFilePaper()
      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: 'Worms were grown.\n\nThey were kept at 25 C.',
          section: 'Sheet 1',
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          file_name: 'Table_S1.xlsx',
          source_fragments: [
            { text: 'Worms were grown.', section: 'Sheet 1', file_id: TEXT_SUPPLEMENT_FILE_ID },
            { text: 'They were kept at 25 C.', section: 'Sheet 1', file_id: TEXT_SUPPLEMENT_FILE_ID },
          ],
        }))
      })

      const highlighted = await screen.findByText('Worms were grown. They were kept at 25 C.')
      expect(highlighted).toHaveAttribute('aria-current', 'true')
    })

    it('a partial find highlights the found part and says that part of the evidence is missing', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      act(() => { dispatchPDFViewerNavigateEvidence(twoRowRecord(['| XS0401 | 25 C |', '| XS9999 | 15 C |'])) })

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Only part of this evidence was found in the file\'s text. The part that was found is highlighted.',
      )
      expect(screen.getAllByRole('row', { current: true })).toHaveLength(1)
      // The chip says so too, so closing the banner leaves nothing misleading.
      expect(screen.getByText('Partly found')).toBeInTheDocument()
      expect(screen.queryByText('Exact quote')).not.toBeInTheDocument()
    })
  })

  it('evidence with no section (text before the first heading) that is not found gets the plain not-found banner, no section mark', async () => {
    stubViewerFetch()
    await showFourFilePaper()
    act(() => {
      dispatchPDFViewerNavigateEvidence(chatCommand({
        verified_quote: 'A sentence that is not in this file.',
        section: null,
        file_id: TEXT_SUPPLEMENT_FILE_ID,
        file_name: 'Table_S1.xlsx',
        source_fragments: [{ text: 'A sentence that is not in this file.', file_id: TEXT_SUPPLEMENT_FILE_ID }],
      }))
    })

    expect(await screen.findByRole('alert')).toHaveTextContent(/^This quote could not be found in the file's text\.$/)
    expect(document.querySelector('[data-highlight-kind="section"]')).toBeNull()
  })

  it('evidence with no section that is found is highlighted as usual', async () => {
    stubViewerFetch()
    await showFourFilePaper()
    act(() => {
      dispatchPDFViewerNavigateEvidence(chatCommand({
        verified_quote: '| XS0401 | 25 C |',
        section: null,
        file_id: TEXT_SUPPLEMENT_FILE_ID,
        file_name: 'Table_S1.xlsx',
        source_fragments: [{ text: '| XS0401 | 25 C |', span_type: 'table_row', file_id: TEXT_SUPPLEMENT_FILE_ID }],
      }))
    })

    expect(await screen.findByRole('row', { current: true })).toHaveTextContent('XS0401')
  })

  describe('P9c: large tables and the converted-text note', () => {
    function longSheet(dataRows: number): DisplayBlock {
      const header = { line: '| Strain | Temp |', cells: ['Strain', 'Temp'], is_header: true }
      const rows = [header, ...Array.from({ length: dataRows }, (_, index) => ({
        line: `| XS${index + 1} | 25 C |`, cells: [`XS${index + 1}`, '25 C'], is_header: false,
      }))]
      return {
        ...TEXT_SUPPLEMENT_BLOCKS.blocks[1]!,
        text: [header.line, '|---|---|', ...rows.slice(1).map((row) => row.line)].join('\n'),
        rows,
      }
    }

    it('evidence on rows 7,000 to 7,001 of a 9,000-row sheet reveals their part and highlights them without drawing every row', async () => {
      stubViewerFetch({ textBlocks: [TEXT_SUPPLEMENT_BLOCKS.blocks[0]!, longSheet(8999)] })
      await showFourFilePaper()
      act(() => {
        dispatchPDFViewerNavigateEvidence(twoRowRecord(['| XS7000 | 25 C |', '| XS7001 | 25 C |']))
      })

      await waitFor(() => expect(document.querySelectorAll('tr[aria-current="true"]')).toHaveLength(2), { timeout: 8000 })
      expect(document.querySelectorAll('tbody tr')).toHaveLength(7500)
      expect(screen.getByText('Showing rows 1 to 7,500 of 9,000.')).toBeInTheDocument()
      // Every shown row is page text the browser's find can reach; rows not yet shown are not in the page.
      const pageText = document.body.textContent ?? ''
      expect(pageText).toContain('XS7499')
      expect(pageText).not.toContain('XS7500')
    }, 20000)

    it('a section mark on a block after a long table, in a later section, is shown', async () => {
      stubViewerFetch({
        textBlocks: [
          TEXT_SUPPLEMENT_BLOCKS.blocks[0]!,
          longSheet(1999),
          {
            ...TEXT_SUPPLEMENT_BLOCKS.blocks[2]!,
            text: 'Second sheet note.',
            section_title: 'Sheet 2',
            section_path: ['Sheet 2'],
          },
        ],
      })
      await showFourFilePaper()
      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: 'A sentence that is not in this file.',
          section: 'Sheet 2',
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          file_name: 'Table_S1.xlsx',
        }))
      })

      expect(await screen.findByRole('alert')).toHaveTextContent('Its section is shown instead.')
      expect(document.querySelector('[data-highlight-kind="section"]')).toHaveTextContent('Second sheet note.')
      expect(document.querySelectorAll('tbody tr')).toHaveLength(500)
    })

    it('a text tab says what conversion does not keep; a PDF tab does not', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      expect(screen.queryByText('This is text converted from the original file. Formatting, images and some table layout are not kept.')).not.toBeInTheDocument()

      fireEvent.click(screen.getByRole('tab', { name: 'Table_S1.xlsx, file 2 of 3, converted text' }))

      expect(await screen.findByText('This is text converted from the original file. Formatting, images and some table layout are not kept.')).toBeInTheDocument()
    })
  })

  describe('F2: the same paper sent again', () => {
    it('R3: equal files keep the text highlight and do not fetch the text again', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: '| XS0401 | 25 C |', file_id: TEXT_SUPPLEMENT_FILE_ID, file_name: 'Table_S1.xlsx',
        }))
      })
      await screen.findByRole('row', { current: true })
      const before = textFetches

      act(() => { showPaperFilesInViewer(DOCUMENT_ID, structuredClone(FOUR_FILES_PAYLOAD.files), { ownerToken: HOME_PDF_VIEWER_OWNER }) })
      await new Promise((resolve) => setTimeout(resolve, 50))

      expect({ refetched: textFetches - before, highlighted: Boolean(screen.queryByRole('row', { current: true })) })
        .toEqual({ refetched: 0, highlighted: true })
    })

    it('R3b: a loaded PDF keeps its zoom and page, and no second load is signalled', async () => {
      stubViewerFetch()
      render(<PdfViewer activeDocumentOwnerToken={HOME_PDF_VIEWER_OWNER} />)
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, [singleMainPdfFile()], { ownerToken: HOME_PDF_VIEWER_OWNER }) })
      await screen.findAllByRole('tab')
      const { app } = installPdfApp(MAIN_PAGES)
      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeEnabled())
      await new Promise((resolve) => setTimeout(resolve, 400))
      const loads: string[] = []
      const listener = (event: Event) => loads.push(String((event as CustomEvent).detail?.message))
      window.addEventListener(DOCUMENT_LOAD_COMPLETE_EVENT, listener)
      app.pdfViewer.currentScaleValue = '1.5'
      app.pdfViewer.currentPageNumber = 2

      act(() => { showPaperFilesInViewer(DOCUMENT_ID, [structuredClone(singleMainPdfFile())], { ownerToken: HOME_PDF_VIEWER_OWNER }) })
      await new Promise((resolve) => setTimeout(resolve, 400))

      window.removeEventListener(DOCUMENT_LOAD_COMPLETE_EVENT, listener)
      expect({ scale: app.pdfViewer.currentScaleValue, page: app.pdfViewer.currentPageNumber, loads })
        .toEqual({ scale: '1.5', page: 2, loads: [] })
    })

    it('a supplement joining while a PDF is shown keeps the zoom and page and adds its tab', async () => {
      stubViewerFetch()
      render(<PdfViewer activeDocumentOwnerToken={HOME_PDF_VIEWER_OWNER} />)
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, [FOUR_FILES_PAYLOAD.files[0]!], { ownerToken: HOME_PDF_VIEWER_OWNER }) })
      await screen.findAllByRole('tab')
      const { app } = installPdfApp(MAIN_PAGES)
      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeEnabled())
      await new Promise((resolve) => setTimeout(resolve, 400))
      app.pdfViewer.currentScaleValue = '1.5'
      app.pdfViewer.currentPageNumber = 2

      act(() => { showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files.slice(0, 2), { ownerToken: HOME_PDF_VIEWER_OWNER }) })

      expect(await screen.findAllByRole('tab')).toHaveLength(2)
      await new Promise((resolve) => setTimeout(resolve, 400))
      expect({ scale: app.pdfViewer.currentScaleValue, page: app.pdfViewer.currentPageNumber })
        .toEqual({ scale: '1.5', page: 2 })
    })
  })

  describe('F3: one parser for both paths', () => {
    it('R8: an empty display name, which the backend serves, is accepted', () => {
      expect(() => parseViewerFile({ ...FOUR_FILES_PAYLOAD.files[0]!, display_name: '' })).not.toThrow()
    })

    it('a file with an empty name is shown with a neutral label', async () => {
      stubViewerFetch()
      await showFourFilePaper([{ ...FOUR_FILES_PAYLOAD.files[0]!, display_name: '' }])

      expect(screen.getByRole('tab', { name: 'Unnamed file (main paper), file 1 of 1' })).toBeInTheDocument()
    })

    it('the curation path\'s files go through the same parser and are refused when malformed', () => {
      expect(() => showPaperFilesInViewer(DOCUMENT_ID, [{ ...FOUR_FILES_PAYLOAD.files[0]!, display_mode: 'image' } as unknown as ViewerFile]))
        .toThrow('Viewer file field "display_mode" has an unknown value')
    })
  })

  it('F5: the blocks and the table are not rendered again when unrelated state changes', () => {
    let rowReads = 0
    let headingReads = 0
    const rows = TEXT_SUPPLEMENT_BLOCKS.blocks[1]!.rows!
    const table: DisplayBlock = {
      ...TEXT_SUPPLEMENT_BLOCKS.blocks[1]!,
      get rows() { rowReads += 1; return rows },
    }
    // The heading is rendered by TextFileBlocks itself: its text is read only when the outer component renders.
    const heading: DisplayBlock = {
      ...TEXT_SUPPLEMENT_BLOCKS.blocks[0]!,
      get text() { headingReads += 1; return 'Sheet 1' },
    }
    const blocks = [heading, table]
    const ref = () => undefined
    let bump: () => void = () => undefined
    function Parent() {
      const [count, setCount] = useState(0)
      bump = () => setCount((value) => value + 1)
      return (
        <div data-count={count}>
          <TextFileBlocks blocks={blocks} highlightedBlocks={null} sectionBlockIndex={null} firstHighlightRef={ref} />
        </div>
      )
    }
    render(<Parent />)
    const readsAfterFirstRender = { rows: rowReads, heading: headingReads }

    act(() => { bump() })
    act(() => { bump() })

    expect({ rows: rowReads, heading: headingReads }).toEqual(readsAfterFirstRender)
  })

  it('F6: a fragment found in more than one place shows the plainer note under the chips', async () => {
    stubViewerFetch({
      textBlocks: [
        { ...TEXT_SUPPLEMENT_BLOCKS.blocks[2]!, text: 'Strain A grew.' },
        { ...TEXT_SUPPLEMENT_BLOCKS.blocks[2]!, text: 'Strain A grew.' },
      ],
    })
    await showFourFilePaper()
    act(() => {
      dispatchPDFViewerNavigateEvidence(chatCommand({ verified_quote: 'Strain A grew.', file_id: TEXT_SUPPLEMENT_FILE_ID, file_name: 'Table_S1.xlsx' }))
    })

    expect(await screen.findByText(
      'This quote appears more than once in this file. The highlighted place is the first one in the evidence\'s section, or the first in the file if that section has none.',
    )).toBeInTheDocument()
  })

  it('F6: a found text quote shows its note under the chips', async () => {
    stubViewerFetch()
    await showFourFilePaper()
    act(() => {
      dispatchPDFViewerNavigateEvidence(chatCommand({ verified_quote: '| XS0401 | 25 C |', file_id: TEXT_SUPPLEMENT_FILE_ID, file_name: 'Table_S1.xlsx' }))
    })
    await screen.findByRole('row', { current: true })
    expect(screen.getByText('The quote was found exactly in this text file.')).toBeInTheDocument()
  })

  it('F7: a PDF supplement that cannot be reached shows its error in its own pane, not Home\'s backdrop', async () => {
    stubViewerFetch({ headStatus: (url) => (url.includes(PDF_SUPPLEMENT_FILE_ID) ? 404 : 200) })
    await showFourFilePaper()
    installPdfApp(MAIN_PAGES)
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeEnabled())
    const errors: string[] = []
    const listener = (event: Event) => errors.push(String((event as CustomEvent).detail?.message))
    window.addEventListener(DOCUMENT_LOAD_ERROR_EVENT, listener)

    fireEvent.click(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 3 of 3' }))

    expect(await screen.findByText('This supplement could not be loaded.')).toBeInTheDocument()
    window.removeEventListener(DOCUMENT_LOAD_ERROR_EVENT, listener)
    expect(errors).toEqual([])
  })

  describe('F7 remainder (re-check probes P1, P2)', () => {
    function collectErrors() {
      const errors: string[] = []
      const listener = (event: Event) => errors.push(String((event as CustomEvent).detail?.message))
      window.addEventListener(DOCUMENT_LOAD_ERROR_EVENT, listener)
      return { errors, stop: () => window.removeEventListener(DOCUMENT_LOAD_ERROR_EVENT, listener) }
    }

    it('P1: a text main paper, then a PDF supplement that fails: no Home load error', async () => {
      stubViewerFetch({ headStatus: (url) => (url.includes(PDF_SUPPLEMENT_FILE_ID) ? 404 : 200) })
      const textMain = { ...FOUR_FILES_PAYLOAD.files[1]!, role: 'main' as const, tab_order: 0 }
      await showFourFilePaper([textMain, FOUR_FILES_PAYLOAD.files[2]!])
      await screen.findByText('Kept at two temperatures.')
      const { errors, stop } = collectErrors()

      fireEvent.click(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 2 of 2' }))

      expect(await screen.findByText('This supplement could not be loaded.')).toBeInTheDocument()
      stop()
      expect(errors).toEqual([])
    })

    it('P2: a loaded PDF paper sent again unchanged, then a failing PDF supplement: no Home load error', async () => {
      stubViewerFetch({ headStatus: (url) => (url.includes(PDF_SUPPLEMENT_FILE_ID) ? 404 : 200) })
      await showFourFilePaper()
      installPdfApp(MAIN_PAGES)
      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeEnabled())
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, structuredClone(FOUR_FILES_PAYLOAD.files), { ownerToken: HOME_PDF_VIEWER_OWNER }) })
      const { errors, stop } = collectErrors()

      fireEvent.click(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 3 of 3' }))

      expect(await screen.findByText('This supplement could not be loaded.')).toBeInTheDocument()
      stop()
      expect(errors).toEqual([])
    })

    it('a main PDF that cannot be reached still tells Home, with the main-file text', async () => {
      stubViewerFetch({ headStatus: () => 404 })
      const { errors, stop } = collectErrors()
      await showFourFilePaper()

      expect(await screen.findByText('Unable to reach the PDF document. Please retry or re-upload.')).toBeInTheDocument()
      // One word for retry (approved 23 Nov 2026), on the main file's error overlay too.
      expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
      stop()
      expect(errors).toEqual(['Unable to reach the PDF document. Please retry or re-upload.'])
    })
  })

  it('F8: a main file\'s accessible name includes the visible "(main paper)"', async () => {
    stubViewerFetch()
    await showFourFilePaper()

    expect(screen.getByRole('tab', { name: 'made-up-paper.pdf (main paper), file 1 of 3' })).toBeInTheDocument()
  })

  it('F10: a text address on another origin is refused before use', async () => {
    const fetchMock = stubViewerFetch()
    render(<PdfViewer />)
    act(() => {
      showPaperFilesInViewer(DOCUMENT_ID, [{ ...FOUR_FILES_PAYLOAD.files[1]!, role: 'main', text_url: 'https://elsewhere.example/text' }])
    })

    expect(await screen.findByText('The viewer only opens files from this site.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContain('https://elsewhere.example/text')
  })

  it('F11: evidence naming a file that is not ready says so, not that it was removed', async () => {
    stubViewerFetch()
    await showFourFilePaper()

    act(() => { dispatchPDFViewerNavigateEvidence(chatCommand({ file_id: PENDING_SUPPLEMENT_FILE_ID, file_name: 'Table_S3.docx' })) })

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This evidence\'s file is still being added to the paper, so it cannot be shown yet.',
    )
  })

  describe('kept behaviour (review probes)', () => {
    it('R2: new evidence on a one-PDF paper searches the main file with its page hint', async () => {
      stubViewerFetch()
      render(<PdfViewer />)
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, [singleMainPdfFile()]) })
      await screen.findAllByRole('tab')
      installPdfApp(MAIN_PAGES)
      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeEnabled())
      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({ page: 2, file_id: MAIN_FILE_ID, file_name: 'made-up-paper.pdf' }))
      })
      await waitFor(() => expect(fuzzyMatchPdfEvidenceQuote).toHaveBeenCalledTimes(1))
      const call = vi.mocked(fuzzyMatchPdfEvidenceQuote).mock.calls[0]![0]
      expect(call.pages).toHaveLength(2)
      expect(call.pageHints).toEqual([2])
    })

    it('R2b: old evidence (no file id) on a one-PDF paper shows Q4 and searches nothing', async () => {
      stubViewerFetch()
      render(<PdfViewer />)
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, [singleMainPdfFile()]) })
      await screen.findAllByRole('tab')
      installPdfApp(MAIN_PAGES)
      act(() => { dispatchPDFViewerNavigateEvidence(chatCommand({ page: 2 })) })
      expect(await screen.findByRole('alert')).toHaveTextContent('This evidence was saved before files were recorded')
      expect(fuzzyMatchPdfEvidenceQuote).not.toHaveBeenCalled()
    })

    it('R4: two quick clicks in two files end on the later one with no stale text result', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      installPdfApp(MAIN_PAGES)
      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({ verified_quote: '| XS0401 | 25 C |', file_id: TEXT_SUPPLEMENT_FILE_ID, file_name: 'Table_S1.xlsx' }))
        dispatchPDFViewerNavigateEvidence(chatCommand({ file_id: PDF_SUPPLEMENT_FILE_ID, file_name: 'Figure_S2.pdf' }))
      })
      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 3 of 3' })).toHaveAttribute('aria-selected', 'true')
      })
      await new Promise((resolve) => setTimeout(resolve, 50))
      expect(screen.queryByRole('row', { current: true })).not.toBeInTheDocument()
      expect(screen.queryByText(/could not be found in the file's text/)).not.toBeInTheDocument()
    })

    it('R5: the active file removed by a refresh reopens the main file without a crash', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      fireEvent.click(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 3 of 3' }))
      act(() => {
        showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files.filter((file) => file.file_id !== PDF_SUPPLEMENT_FILE_ID), { ownerToken: HOME_PDF_VIEWER_OWNER })
      })
      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'made-up-paper.pdf (main paper), file 1 of 2' })).toHaveAttribute('aria-selected', 'true')
      })
    })

    it('R6: twenty files render twenty tabs and the menu', async () => {
      stubViewerFetch()
      const files = Array.from({ length: 20 }, (_, index) => ({
        ...singleMainPdfFile({ fileId: `aaaaaaaa-0000-0000-0000-0000000002${String(index).padStart(2, '0')}`, displayName: `A very long supplementary file name number ${index + 1}.pdf` }),
        role: index === 0 ? 'main' as const : 'supplement' as const,
        tab_order: index,
      }))
      render(<PdfViewer />)
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, files) })
      expect(await screen.findAllByRole('tab')).toHaveLength(20)
      expect(screen.getByRole('button', { name: 'Show all 20 files of this paper' })).toBeInTheDocument()
    })

    it('R7: markup in a block and in a file name is shown as text', async () => {
      const markup = '<img src=x onerror="window.__pwned=1">'
      stubViewerFetch({ textBlocks: [{ ...TEXT_SUPPLEMENT_BLOCKS.blocks[2]!, text: markup }] })
      render(<PdfViewer />)
      act(() => {
        showPaperFilesInViewer(DOCUMENT_ID, [{ ...FOUR_FILES_PAYLOAD.files[1]!, role: 'main', display_name: markup }])
      })
      expect(await screen.findAllByText(markup)).not.toHaveLength(0)
      expect(document.querySelector('img[src="x"]')).toBeNull()
      expect(within(document.body).queryByRole('img')).toBeNull()
    })
  })
})

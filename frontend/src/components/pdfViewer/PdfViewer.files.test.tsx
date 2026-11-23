/**
 * The viewer with a paper's files (ALL-1460, P9b): one tab per file, per-file state,
 * and evidence that opens its own file first. Files are in the backend's I5 shapes
 * (`viewerFilesFixtures.ts`); evidence records in P8b's shapes.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import { buildNavigationCommandFromChatEvidenceRecord } from '@/features/curation/evidence/navigationSourceAdapters'
import { fuzzyMatchPdfEvidenceQuote } from '@/features/curation/services/pdfEvidenceMatcherService'
import PdfViewer from './PdfViewer'
import { HOME_PDF_VIEWER_OWNER, dispatchPDFViewerNavigateEvidence, showPaperFilesInViewer } from './pdfEvents'
import type { ViewerFile } from './viewerFiles'
import {
  DOCUMENT_ID,
  FOUR_FILES_PAYLOAD,
  MAIN_FILE_ID,
  PDF_SUPPLEMENT_FILE_ID,
  TEXT_SUPPLEMENT_BLOCKS,
  TEXT_SUPPLEMENT_FILE_ID,
  contentUrl,
  mainPdfFileNotReady,
  textUrl,
} from './viewerFilesFixtures'

vi.mock('@/features/curation/services/pdfEvidenceMatcherService', () => ({
  fuzzyMatchPdfEvidenceQuote: vi.fn(),
}))

const MAIN_PAGES = ['Main paper page one.', 'Main paper page two mentions XS0401 too.']
const SUPPLEMENT_PAGES = ['Supplement page one.', 'Strain XS0401 was kept at 25 C.']

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function stubViewerFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'HEAD') {
      return new Response(null, { status: 200 })
    }
    if (url === textUrl(DOCUMENT_ID, TEXT_SUPPLEMENT_FILE_ID)) {
      return jsonResponse(TEXT_SUPPLEMENT_BLOCKS)
    }
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** A minimal pdf.js application in the current frame, with its pages' text. */
function installPdfApp(pages: string[], startPage = 1) {
  const iframe = screen.getByTitle('PDF Viewer') as HTMLIFrameElement
  const pdfDocument = { numPages: pages.length }
  let currentPageNumber = startPage
  const pageSets: number[] = []
  const app = {
    eventBus: { on: vi.fn(), off: vi.fn(), dispatch: vi.fn() },
    findController: {
      _pageContents: pages,
      selected: { pageIdx: -1, matchIdx: -1 },
      pageMatches: [],
      pageMatchesLength: [],
    },
    pdfViewer: {
      get currentPageNumber() { return currentPageNumber },
      set currentPageNumber(value: number) { pageSets.push(value); currentPageNumber = value },
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
  return { app, pageSets }
}

function filesWithTabs(): ViewerFile[] {
  return FOUR_FILES_PAYLOAD.files
}

async function showFourFilePaper() {
  render(<PdfViewer activeDocumentOwnerToken={HOME_PDF_VIEWER_OWNER} />)
  act(() => {
    showPaperFilesInViewer(DOCUMENT_ID, filesWithTabs(), { ownerToken: HOME_PDF_VIEWER_OWNER })
  })
  await screen.findByRole('tab', { name: 'made-up-paper.pdf (main paper), file 1 of 3' })
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

describe('PdfViewer with a paper\'s files', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockReset()
    vi.mocked(fuzzyMatchPdfEvidenceQuote).mockResolvedValue({
      found: false,
      strategy: 'rapidfuzz-single-page',
      score: 0,
      matchedPage: null,
      matchedQuery: null,
      matchedRange: null,
      pageRanges: [],
      crossPage: false,
      note: 'not found',
    } as never)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('gives one tab to each file with a URL, in tab order, and none to a file still being added', async () => {
    stubViewerFetch()
    await showFourFilePaper()

    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.getAttribute('aria-label'))).toEqual([
      'made-up-paper.pdf (main paper), file 1 of 3',
      'Table_S1.xlsx, file 2 of 3, converted text',
      'Figure_S2.pdf, file 3 of 3',
    ])
    expect(screen.queryByText('Table_S3.docx')).not.toBeInTheDocument()
    expect(within(tabs[0]!).getByText('(main paper)')).toBeInTheDocument()
    expect(within(tabs[1]!).getByText('Text')).toBeInTheDocument()
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    // The full name stays available when the label is shortened.
    expect(within(tabs[1]!).getByTitle('Table_S1.xlsx')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Show all/ })).not.toBeInTheDocument()
  })

  it('shows a paper with one PDF as one tab with its PDF frame', async () => {
    stubViewerFetch()
    render(<PdfViewer />)
    act(() => { showPaperFilesInViewer(DOCUMENT_ID, [FOUR_FILES_PAYLOAD.files[0]!]) })

    expect(await screen.findAllByRole('tab')).toHaveLength(1)
    expect((screen.getByTitle('PDF Viewer') as HTMLIFrameElement).src).toContain(
      encodeURIComponent(contentUrl(DOCUMENT_ID, MAIN_FILE_ID)),
    )
  })

  it('shows a text file in the text pane, with no PDF toolbar and no PDF frame source', async () => {
    stubViewerFetch()
    await showFourFilePaper()

    fireEvent.click(screen.getByRole('tab', { name: 'Table_S1.xlsx, file 2 of 3, converted text' }))

    expect(await screen.findByText('Kept at two temperatures.')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Strain' })).toBeInTheDocument()
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', `viewer-file-tab-${TEXT_SUPPLEMENT_FILE_ID}`)
    expect(screen.queryByRole('textbox', { name: 'Find in PDF' })).not.toBeInTheDocument()
    expect((screen.getByTitle('PDF Viewer') as HTMLIFrameElement).getAttribute('src')).toBe('about:blank')
    expect(screen.queryByText('Loading PDF...')).not.toBeInTheDocument()
  })

  it('says so, and offers to try again, when a text file cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => (
      init?.method === 'HEAD' ? new Response(null, { status: 200 }) : new Response('{}', { status: 500 })
    )))
    await showFourFilePaper()

    fireEvent.click(screen.getByRole('tab', { name: 'Table_S1.xlsx, file 2 of 3, converted text' }))

    expect(await screen.findByText('Could not load this file\'s text.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('moves between tabs with the arrow keys', async () => {
    stubViewerFetch()
    await showFourFilePaper()
    const [first, second] = screen.getAllByRole('tab')

    first!.focus()
    fireEvent.keyDown(first!, { key: 'ArrowRight' })

    expect(second).toHaveFocus()
  })

  it('clears the PDF search when another file is shown', async () => {
    stubViewerFetch()
    await showFourFilePaper()
    installPdfApp(MAIN_PAGES)
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeEnabled())
    fireEvent.change(screen.getByRole('textbox', { name: 'Find in PDF' }), { target: { value: 'XS0401' } })

    fireEvent.click(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 3 of 3' }))

    expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toHaveValue('')
  })

  it('says the main file is not available, instead of an empty pane, when nothing can be shown', async () => {
    stubViewerFetch()
    render(<PdfViewer />)
    act(() => { showPaperFilesInViewer(DOCUMENT_ID, [mainPdfFileNotReady()]) })

    expect(await screen.findByText('This paper\'s main file is not available to show.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'PDF drop zone' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('shows the supplements with a note when the main file is not available', async () => {
    stubViewerFetch()
    render(<PdfViewer />)
    act(() => { showPaperFilesInViewer(DOCUMENT_ID, [mainPdfFileNotReady('failed'), ...FOUR_FILES_PAYLOAD.files.slice(1)]) })

    expect(await screen.findByText('This paper\'s main file is not available to show.')).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((tab) => tab.getAttribute('aria-label'))).toEqual([
      'Table_S1.xlsx, file 1 of 2, converted text',
      'Figure_S2.pdf, file 2 of 2',
    ])
    expect(await screen.findByText('Kept at two temperatures.')).toBeInTheDocument()
  })

  describe('the files menu (Q15)', () => {
    function manyFiles(count: number): ViewerFile[] {
      return Array.from({ length: count }, (_, index) => ({
        ...FOUR_FILES_PAYLOAD.files[1]!,
        file_id: `aaaaaaaa-0000-0000-0000-0000000001${String(index).padStart(2, '0')}`,
        role: index === 0 ? 'main' as const : 'supplement' as const,
        display_name: `Table_S${index + 1}.xlsx`,
        tab_order: index,
        text_url: textUrl(DOCUMENT_ID, `aaaaaaaa-0000-0000-0000-0000000001${String(index).padStart(2, '0')}`),
      }))
    }

    it('is not shown for six files', async () => {
      stubViewerFetch()
      render(<PdfViewer />)
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, manyFiles(6)) })

      expect(await screen.findAllByRole('tab')).toHaveLength(6)
      expect(screen.queryByRole('button', { name: /Show all/ })).not.toBeInTheDocument()
    })

    it('lists every file above six, opens from the keyboard and switches the tab', async () => {
      stubViewerFetch()
      render(<PdfViewer />)
      act(() => { showPaperFilesInViewer(DOCUMENT_ID, manyFiles(7)) })

      const user = userEvent.setup()
      const menuButton = await screen.findByRole('button', { name: 'Show all 7 files of this paper' })
      expect(menuButton).toHaveTextContent('All files (7)')
      menuButton.focus()
      await user.keyboard('{Enter}')

      const items = await screen.findAllByRole('menuitem')
      expect(items.map((item) => item.getAttribute('aria-label'))).toEqual(
        Array.from({ length: 7 }, (_, index) => `Table_S${index + 1}.xlsx${index === 0 ? ' (main paper)' : ''}, file ${index + 1} of 7, converted text`),
      )
      await user.click(items[6]!)

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Table_S7.xlsx, file 7 of 7, converted text' })).toHaveAttribute('aria-selected', 'true')
      })
    })
  })

  describe('evidence opens its own file', () => {
    it('evidence saved before files were recorded shows the Q4 text and opens nothing', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      installPdfApp(MAIN_PAGES)

      act(() => { dispatchPDFViewerNavigateEvidence(chatCommand({})) })

      expect(await screen.findAllByText('This evidence was saved before files were recorded')).not.toHaveLength(0)
      expect(fuzzyMatchPdfEvidenceQuote).not.toHaveBeenCalled()
      expect(screen.getByRole('tab', { name: 'made-up-paper.pdf (main paper), file 1 of 3' })).toHaveAttribute('aria-selected', 'true')
    })

    it('the Q4 banner is announced, can be closed, and changes nothing else in the viewer', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      fireEvent.click(screen.getByRole('tab', { name: 'Table_S1.xlsx, file 2 of 3, converted text' }))
      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: '| XS0401 | 25 C |',
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          file_name: 'Table_S1.xlsx',
        }))
      })
      await screen.findByRole('row', { current: true })
      const scrolls = vi.mocked(Element.prototype.scrollIntoView).mock.calls.length

      act(() => { dispatchPDFViewerNavigateEvidence(chatCommand({})) })

      const banner = await screen.findByRole('alert')
      expect(banner).toHaveTextContent('This evidence was saved before files were recorded')
      // No tab switch, no scroll, and the earlier highlight stays.
      expect(screen.getByRole('tab', { name: 'Table_S1.xlsx, file 2 of 3, converted text' })).toHaveAttribute('aria-selected', 'true')
      expect(vi.mocked(Element.prototype.scrollIntoView).mock.calls.length).toBe(scrolls)
      expect(screen.getByRole('row', { current: true })).toBeInTheDocument()

      fireEvent.click(within(banner).getByRole('button', { name: 'Close' }))
      await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
      expect(screen.queryByText('This evidence was saved before files were recorded')).not.toBeInTheDocument()
    })

    it('evidence with an explicit null file id is treated as saved before files were recorded', async () => {
      stubViewerFetch()
      await showFourFilePaper()

      // P8b never sends null (old records have no file_id key), but a null must not open anything either.
      act(() => { dispatchPDFViewerNavigateEvidence(chatCommand({ file_id: null as unknown as string })) })

      expect(await screen.findByRole('alert')).toHaveTextContent('This evidence was saved before files were recorded')
    })

    it('a quote across a long element\'s sentence cut is reported as not found, with no highlight', async () => {
      // P9a's one exception: the stored piece has one space where the block keeps two.
      const longText = `${'A'.repeat(1500)}.  Strain XS0401 grew at 25 C.`
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'HEAD') return new Response(null, { status: 200 })
        if (String(input) === textUrl(DOCUMENT_ID, TEXT_SUPPLEMENT_FILE_ID)) {
          return jsonResponse({
            ...TEXT_SUPPLEMENT_BLOCKS,
            blocks: [{ ...TEXT_SUPPLEMENT_BLOCKS.blocks[2]!, text: longText }],
          })
        }
        throw new Error(`Unexpected fetch: ${String(input)}`)
      }))
      await showFourFilePaper()

      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: `${'A'.repeat(20)}. Strain XS0401 grew`,
          section: 'Not a section of this file',
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          file_name: 'Table_S1.xlsx',
        }))
      })

      expect(await screen.findByRole('alert')).toHaveTextContent('This quote could not be found in the file\'s text.')
      expect(document.querySelector('[aria-current]')).toBeNull()
    })

    it('a quote not found in a text file shows the evidence\'s section, marked as such', async () => {
      stubViewerFetch()
      await showFourFilePaper()

      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: 'A sentence that is not in this file.',
          section: 'Sheet 1',
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          file_name: 'Table_S1.xlsx',
        }))
      })

      expect(await screen.findByRole('alert')).toHaveTextContent('This quote could not be found in the file\'s text. Its section is shown instead.')
      expect(document.querySelector('[data-highlight-kind="section"]')).toHaveTextContent('Sheet 1')
      expect(screen.queryByRole('row', { current: true })).not.toBeInTheDocument()
    })

    it('evidence whose file is no longer part of the paper says so and opens nothing', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      installPdfApp(MAIN_PAGES)

      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          file_id: 'aaaaaaaa-0000-0000-0000-0000000000ff',
          file_name: 'Removed_S9.xlsx',
        }))
      })

      expect(await screen.findAllByText('This file is no longer part of the paper, so this evidence cannot be shown.')).not.toHaveLength(0)
      expect(fuzzyMatchPdfEvidenceQuote).not.toHaveBeenCalled()
    })

    it('table-row evidence in a text file opens its tab, highlights the row by its stored line and never searches a PDF', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      const { app } = installPdfApp(MAIN_PAGES)
      const elsewhere = document.createElement('button')
      document.body.appendChild(elsewhere)
      elsewhere.focus()

      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: '| XS0401 | 25 C |',
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          file_name: 'Table_S1.xlsx',
          source_fragments: [{ text: '| XS0401 | 25 C |', span_type: 'table_row', search_text: 'XS0401 25 C' }],
        }))
      })

      const row = await screen.findByRole('row', { current: true })
      expect(within(row).getByText('XS0401')).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: 'Table_S1.xlsx, file 2 of 3, converted text' })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByTestId('viewer-file-announcement')).toHaveTextContent('Opened Table_S1.xlsx')
      expect(elsewhere).toHaveFocus()
      expect(fuzzyMatchPdfEvidenceQuote).not.toHaveBeenCalled()
      expect(app.eventBus.dispatch).not.toHaveBeenCalledWith('find', expect.anything())
      elsewhere.remove()
    })

    it('a prose quote not in the text file says it was not found', async () => {
      stubViewerFetch()
      await showFourFilePaper()

      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          verified_quote: 'A sentence that is not in this file.',
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          file_name: 'Table_S1.xlsx',
        }))
      })

      expect(await screen.findAllByText('This quote could not be found in the file\'s text.')).not.toHaveLength(0)
      expect(screen.queryByRole('row', { current: true })).not.toBeInTheDocument()
    })

    it('evidence on a PDF supplement opens its tab and searches only that file\'s pages, without jumping to page 1', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      installPdfApp(MAIN_PAGES)

      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({
          file_id: PDF_SUPPLEMENT_FILE_ID,
          file_name: 'Figure_S2.pdf',
        }))
      })

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 3 of 3' })).toHaveAttribute('aria-selected', 'true')
      })
      expect(fuzzyMatchPdfEvidenceQuote).not.toHaveBeenCalled()
      expect((screen.getByTitle('PDF Viewer') as HTMLIFrameElement).src).toContain(
        encodeURIComponent(contentUrl(DOCUMENT_ID, PDF_SUPPLEMENT_FILE_ID)),
      )
      // The curator was on page 2 of the supplement; page-less evidence must not move to page 1.
      const { pageSets } = installPdfApp(SUPPLEMENT_PAGES, 2)

      await waitFor(() => expect(fuzzyMatchPdfEvidenceQuote).toHaveBeenCalledTimes(1))
      // The quote is in both files; only the supplement's pages are searched.
      expect(vi.mocked(fuzzyMatchPdfEvidenceQuote).mock.calls[0]![0].pages).toEqual([
        { pageNumber: 1, text: SUPPLEMENT_PAGES[0] },
        { pageNumber: 2, text: SUPPLEMENT_PAGES[1] },
      ])
      expect(vi.mocked(fuzzyMatchPdfEvidenceQuote).mock.calls[0]![0].pageHints).toEqual([])
      expect(await screen.findAllByText(
        'Opened Figure_S2.pdf, but this quote could not be located in it.',
        {},
        { timeout: 8000 },
      )).not.toHaveLength(0)
      expect(pageSets).not.toContain(1)
    })

    it('a curator\'s own tab switch drops evidence still waiting for its file', async () => {
      stubViewerFetch()
      await showFourFilePaper()
      installPdfApp(MAIN_PAGES)

      act(() => {
        dispatchPDFViewerNavigateEvidence(chatCommand({ file_id: PDF_SUPPLEMENT_FILE_ID, file_name: 'Figure_S2.pdf' }))
      })
      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Figure_S2.pdf, file 3 of 3' })).toHaveAttribute('aria-selected', 'true')
      })

      // Before the supplement's PDF has loaded, the curator goes back to the main paper.
      fireEvent.click(screen.getByRole('tab', { name: 'made-up-paper.pdf (main paper), file 1 of 3' }))
      installPdfApp(MAIN_PAGES)
      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeEnabled())

      expect(fuzzyMatchPdfEvidenceQuote).not.toHaveBeenCalled()
    })
  })
})

import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  HOME_PDF_VIEWER_OWNER,
  buildCurationPDFViewerOwner,
  showPaperFilesInViewer,
  dispatchPDFViewerDocumentCleared,
} from './pdfEvents'
import { singleMainPdfFile, singleMainTextFile } from './viewerFilesFixtures'
import PdfViewer from './PdfViewer'

const CURATION_OWNER = buildCurationPDFViewerOwner('session-text')

/** The layout's owner switch: home on '/', the session's owner on a curation route. */
function RouteOwnerHarness() {
  const [owner, setOwner] = useState(HOME_PDF_VIEWER_OWNER)
  return (
    <>
      <button type="button" onClick={() => setOwner(CURATION_OWNER)}>Open curation session</button>
      <button type="button" onClick={() => setOwner(HOME_PDF_VIEWER_OWNER)}>Back to chat</button>
      <PdfViewer activeDocumentOwnerToken={owner} />
    </>
  )
}

async function showChatPdfA() {
  showPaperFilesInViewer('doc-a', [singleMainPdfFile({ documentId: 'doc-a', displayName: 'paper-a.pdf', pageCount: 6 })], {
    ownerToken: HOME_PDF_VIEWER_OWNER,
  })
  expect(await screen.findByText('paper-a.pdf')).toBeInTheDocument()
}

describe('PdfViewer on a curation session whose paper is text', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('the workspace\'s text paper replaces paper A with its text tab, and no PDF is loading', async () => {
    const textFile = singleMainTextFile({ documentId: 'doc-text', displayName: 'paper-text.md' })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === textFile.text_url) {
        return new Response(JSON.stringify({
          document_id: 'doc-text',
          file_id: textFile.file_id,
          display_name: 'paper-text.md',
          blocks: [{ kind: 'paragraph', text: 'Made-up text paper.', level: null, section_path: [], rows: null }],
        }), { status: 200 })
      }
      return new Response(null, { status: 200 })
    }))
    render(<RouteOwnerHarness />)
    await showChatPdfA()
    fireEvent.click(screen.getByText('Open curation session'))

    act(() => { showPaperFilesInViewer('doc-text', [textFile], { ownerToken: CURATION_OWNER }) })

    expect(await screen.findByText('Made-up text paper.')).toBeInTheDocument()
    expect(screen.queryByText('paper-a.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('Loading PDF...')).not.toBeInTheDocument()
  })

  it('the owner switch alone keeps chat paper A on screen (why the workspace must clear)', async () => {
    render(<RouteOwnerHarness />)
    await showChatPdfA()

    fireEvent.click(screen.getByText('Open curation session'))

    expect(screen.getByText('paper-a.pdf')).toBeInTheDocument()
  })

  it('the workspace clear removes paper A and leaves the viewer with no document', async () => {
    render(<RouteOwnerHarness />)
    await showChatPdfA()
    fireEvent.click(screen.getByText('Open curation session'))

    act(() => { dispatchPDFViewerDocumentCleared(CURATION_OWNER) })

    await waitFor(() => expect(screen.queryByText('paper-a.pdf')).not.toBeInTheDocument())
    // Idle state: no active document, so evidence navigation (which runs only
    // for an active, ready document) has nothing to search, and A is not searched.
    expect(screen.getByText(/Drop a PDF here/)).toBeInTheDocument()
  })

  it('clearing for the same text paper twice keeps the viewer empty', async () => {
    render(<RouteOwnerHarness />)
    await showChatPdfA()
    fireEvent.click(screen.getByText('Open curation session'))

    act(() => { dispatchPDFViewerDocumentCleared(CURATION_OWNER) })
    act(() => { dispatchPDFViewerDocumentCleared(CURATION_OWNER) })

    await waitFor(() => expect(screen.queryByText('paper-a.pdf')).not.toBeInTheDocument())
    expect(screen.queryByText('Loading PDF...')).not.toBeInTheDocument()
  })

  it('back in chat, paper A is shown again once chat restores it', async () => {
    render(<RouteOwnerHarness />)
    await showChatPdfA()
    fireEvent.click(screen.getByText('Open curation session'))
    act(() => { dispatchPDFViewerDocumentCleared(CURATION_OWNER) })
    await waitFor(() => expect(screen.queryByText('paper-a.pdf')).not.toBeInTheDocument())

    fireEvent.click(screen.getByText('Back to chat'))
    // The chat panel remounts on '/' and restores its active document (useChatController).
    await showChatPdfA()
  })
})

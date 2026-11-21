import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  HOME_PDF_VIEWER_OWNER,
  buildCurationPDFViewerOwner,
  dispatchPDFDocumentChanged,
  dispatchPDFViewerDocumentCleared,
} from './pdfEvents'
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
  dispatchPDFDocumentChanged('doc-a', '/fixtures/paper-a.pdf', 'paper-a.pdf', 6, {
    ownerToken: HOME_PDF_VIEWER_OWNER,
  })
  expect(await screen.findByText('paper-a.pdf')).toBeInTheDocument()
}

describe('PdfViewer on a curation session whose paper is text', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
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

import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  HOME_PDF_VIEWER_OWNER,
  buildCurationPDFViewerOwner,
  dispatchPDFDocumentChanged,
} from './pdfEvents'
import PdfViewer from './PdfViewer'

function OwnerHarness() {
  const [ownerToken, setOwnerToken] = useState(HOME_PDF_VIEWER_OWNER)

  return (
    <>
      <button onClick={() => setOwnerToken(buildCurationPDFViewerOwner('session-1'))} type="button">
        Switch owner
      </button>
      <PdfViewer activeDocumentOwnerToken={ownerToken} />
    </>
  )
}

describe('PdfViewer document ownership', () => {
  it('ignores document change events owned by a different route host', async () => {
    const curationOwnerToken = buildCurationPDFViewerOwner('session-1')

    render(<PdfViewer activeDocumentOwnerToken={curationOwnerToken} />)

    dispatchPDFDocumentChanged(
      'doc-home',
      '/fixtures/home.pdf',
      'home.pdf',
      12,
      { ownerToken: HOME_PDF_VIEWER_OWNER },
    )

    await waitFor(() => {
      expect(screen.queryByText('home.pdf')).not.toBeInTheDocument()
    })

    dispatchPDFDocumentChanged(
      'doc-curation',
      '/fixtures/curation.pdf',
      'curation.pdf',
      8,
      { ownerToken: curationOwnerToken },
    )

    expect(await screen.findByText('curation.pdf')).toBeInTheDocument()
  })

  it('preserves the current document until the new active owner explicitly replaces it', async () => {
    render(<OwnerHarness />)

    dispatchPDFDocumentChanged(
      'doc-home',
      '/fixtures/home.pdf',
      'home.pdf',
      12,
      { ownerToken: HOME_PDF_VIEWER_OWNER },
    )

    expect(await screen.findByText('home.pdf')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Switch owner' }))

    await waitFor(() => {
      expect(screen.getByText('home.pdf')).toBeInTheDocument()
    })
  })

  it('ignores chat-document-changed events from an inactive owner', async () => {
    const curationOwnerToken = buildCurationPDFViewerOwner('session-1')

    render(<PdfViewer activeDocumentOwnerToken={curationOwnerToken} />)

    dispatchPDFDocumentChanged(
      'doc-curation',
      '/fixtures/curation.pdf',
      'curation.pdf',
      8,
      { ownerToken: curationOwnerToken },
    )

    expect(await screen.findByText('curation.pdf')).toBeInTheDocument()

    window.dispatchEvent(new CustomEvent('chat-document-changed', {
      detail: {
        active: false,
        document: null,
        ownerToken: HOME_PDF_VIEWER_OWNER,
      },
    }))

    await waitFor(() => {
      expect(screen.getByText('curation.pdf')).toBeInTheDocument()
    })
  })
})

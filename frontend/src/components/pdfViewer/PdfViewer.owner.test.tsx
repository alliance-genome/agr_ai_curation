import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import {
  HOME_PDF_VIEWER_OWNER,
  buildCurationPDFViewerOwner,
  showPaperFilesInViewer,
} from './pdfEvents'
import { singleMainPdfFile } from './viewerFilesFixtures'
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

function StorageUserHarness() {
  const [storageUserId, setStorageUserId] = useState('user-1')

  return (
    <>
      <button onClick={() => setStorageUserId('user-2')} type="button">
        Switch storage user
      </button>
      <PdfViewer storageUserId={storageUserId} />
    </>
  )
}

describe('PdfViewer document ownership', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  it('ignores document change events owned by a different route host', async () => {
    const curationOwnerToken = buildCurationPDFViewerOwner('session-1')

    render(<PdfViewer activeDocumentOwnerToken={curationOwnerToken} />)

    showPaperFilesInViewer('doc-home', [singleMainPdfFile({ documentId: 'doc-home', displayName: 'home.pdf', pageCount: 12 })], { ownerToken: HOME_PDF_VIEWER_OWNER })

    await waitFor(() => {
      expect(screen.queryByText('home.pdf')).not.toBeInTheDocument()
    })

    showPaperFilesInViewer('doc-curation', [singleMainPdfFile({ documentId: 'doc-curation', displayName: 'curation.pdf', pageCount: 8 })], { ownerToken: curationOwnerToken })

    expect(await screen.findByText('curation.pdf')).toBeInTheDocument()
  })

  it('preserves the current document until the new active owner explicitly replaces it', async () => {
    render(<OwnerHarness />)

    showPaperFilesInViewer('doc-home', [singleMainPdfFile({ documentId: 'doc-home', displayName: 'home.pdf', pageCount: 12 })], { ownerToken: HOME_PDF_VIEWER_OWNER })

    expect(await screen.findByText('home.pdf')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Switch owner' }))

    await waitFor(() => {
      expect(screen.getByText('home.pdf')).toBeInTheDocument()
    })
  })

  it('ignores chat-document-changed events from an inactive owner', async () => {
    const curationOwnerToken = buildCurationPDFViewerOwner('session-1')

    render(<PdfViewer activeDocumentOwnerToken={curationOwnerToken} />)

    showPaperFilesInViewer('doc-curation', [singleMainPdfFile({ documentId: 'doc-curation', displayName: 'curation.pdf', pageCount: 8 })], { ownerToken: curationOwnerToken })

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

  it('rejects cross-origin document viewer urls before loading a document', async () => {
    render(<PdfViewer />)

    showPaperFilesInViewer('doc-external', [{ ...singleMainPdfFile({ documentId: 'doc-external', displayName: 'external.pdf', pageCount: 3 }), content_url: 'https://example.org/external.pdf' }])

    expect(
      await screen.findByText('The viewer only opens files from this site.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('external.pdf')).not.toBeInTheDocument()
  })

  it('clears the current document when a replacement viewer url is rejected', async () => {
    render(<PdfViewer />)

    showPaperFilesInViewer('doc-home', [singleMainPdfFile({ documentId: 'doc-home', displayName: 'home.pdf', pageCount: 12 })])

    expect(await screen.findByText('home.pdf')).toBeInTheDocument()

    showPaperFilesInViewer('doc-external', [{ ...singleMainPdfFile({ documentId: 'doc-external', displayName: 'external.pdf', pageCount: 3 }), content_url: 'https://example.org/external.pdf' }])

    expect(
      await screen.findByText('The viewer only opens files from this site.'),
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText('home.pdf')).not.toBeInTheDocument()
    })
    expect(screen.getByText('No document loaded')).toBeInTheDocument()
  })

  it('clears the live document on storage user switch without leaking it into the next namespace', async () => {
    const userOneKeys = getChatLocalStorageKeys('user-1')
    const userTwoKeys = getChatLocalStorageKeys('user-2')

    render(<StorageUserHarness />)

    showPaperFilesInViewer('doc-home', [singleMainPdfFile({ documentId: 'doc-home', displayName: 'home.pdf', pageCount: 12 })])

    expect(await screen.findByText('home.pdf')).toBeInTheDocument()

    await waitFor(() => {
      expect(localStorage.getItem(userOneKeys.pdfViewerSession)).toContain('"documentId":"doc-home"')
    })

    fireEvent.click(screen.getByRole('button', { name: 'Switch storage user' }))

    await waitFor(() => {
      expect(screen.queryByText('home.pdf')).not.toBeInTheDocument()
    })

    expect(localStorage.getItem(userTwoKeys.pdfViewerSession)).toBeNull()
    expect(localStorage.getItem(userOneKeys.pdfViewerSession)).toContain('"documentId":"doc-home"')
    expect(localStorage.getItem('[object Object]')).toBeNull()
  })
})

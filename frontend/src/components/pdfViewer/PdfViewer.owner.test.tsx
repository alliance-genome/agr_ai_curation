import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
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

  it('rejects cross-origin document viewer urls before loading a document', async () => {
    render(<PdfViewer />)

    dispatchPDFDocumentChanged(
      'doc-external',
      'https://example.org/external.pdf',
      'external.pdf',
      3,
    )

    expect(
      await screen.findByText('The PDF viewer only supports same-origin document URLs.'),
    ).toBeInTheDocument()
    expect(screen.queryByText('external.pdf')).not.toBeInTheDocument()
  })

  it('clears the live document on storage user switch without leaking it into the next namespace', async () => {
    const userOneKeys = getChatLocalStorageKeys('user-1')
    const userTwoKeys = getChatLocalStorageKeys('user-2')

    render(<StorageUserHarness />)

    dispatchPDFDocumentChanged(
      'doc-home',
      '/fixtures/home.pdf',
      'home.pdf',
      12,
    )

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

import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { rehydrateChatDocument } from '@/features/documents/chatDocumentRehydration'
import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import {
  HOME_PDF_VIEWER_OWNER,
  buildCurationPDFViewerOwner,
  showPaperFilesInViewer,
  dispatchPDFViewerDocumentCleared,
} from './pdfEvents'
import { singleMainPdfFile, singleMainTextFile } from './viewerFilesFixtures'
import PdfViewer from './PdfViewer'

function dispatchChatDocumentLoaded(documentId: string) {
  window.dispatchEvent(new CustomEvent('chat-document-changed', {
    detail: { active: true, document: { id: documentId }, ownerToken: HOME_PDF_VIEWER_OWNER },
  }))
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function showPreviousPdf() {
  render(<PdfViewer activeDocumentOwnerToken={HOME_PDF_VIEWER_OWNER} />)
  showPaperFilesInViewer('doc-pdf', [singleMainPdfFile({ documentId: 'doc-pdf', displayName: 'previous.pdf', pageCount: 4 })], {
    ownerToken: HOME_PDF_VIEWER_OWNER,
  })
  expect(await screen.findByText('previous.pdf')).toBeInTheDocument()
  const iframe = document.querySelector('iframe')
  if (iframe) act(() => { iframe.dispatchEvent(new Event('load')) })
  await waitFor(() => expect(screen.queryByText('Loading PDF...')).not.toBeInTheDocument())
}

describe('PdfViewer when chat loads a text paper', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('a chat document load starts the loading overlay over the previous PDF', async () => {
    await showPreviousPdf()

    act(() => { dispatchChatDocumentLoaded('doc-text') })

    expect(await screen.findByText('Loading PDF...')).toBeInTheDocument()
  })

  it('ends the load and clears the previous PDF when the new document has no PDF', async () => {
    await showPreviousPdf()
    act(() => { dispatchChatDocumentLoaded('doc-text') })
    expect(await screen.findByText('Loading PDF...')).toBeInTheDocument()

    act(() => { dispatchPDFViewerDocumentCleared(HOME_PDF_VIEWER_OWNER) })

    await waitFor(() => expect(screen.queryByText('Loading PDF...')).not.toBeInTheDocument())
    expect(screen.queryByText('previous.pdf')).not.toBeInTheDocument()
  })

  it('ignores a clear sent for another viewer owner', async () => {
    await showPreviousPdf()

    act(() => { dispatchPDFViewerDocumentCleared(buildCurationPDFViewerOwner('session-1')) })

    expect(screen.getByText('previous.pdf')).toBeInTheDocument()
  })

  it('a resumed chat on a text paper leaves no PDF and no loading overlay, and shows its text tab', async () => {
    await showPreviousPdf()
    const textFile = singleMainTextFile({ documentId: 'doc-text', displayName: 'provider-paper.md' })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/files')) {
        return jsonResponse({ document_id: 'doc-text', files: [textFile] })
      }
      if (url === textFile.text_url) {
        return jsonResponse({
          document_id: 'doc-text',
          file_id: textFile.file_id,
          display_name: 'provider-paper.md',
          blocks: [{ kind: 'paragraph', text: 'Made-up paper text.', level: null, section_title: null, parent_section: null, subsection: null, section_path: [], rows: null }],
        })
      }
      return jsonResponse({ filename: 'provider-paper.md', page_count: null })
    }))
    act(() => { dispatchChatDocumentLoaded('doc-text') })

    await act(async () => {
      await rehydrateChatDocument({
        document: { id: 'doc-text', filename: 'provider-paper.md' },
        chatStorageKeys: getChatLocalStorageKeys('user-1'),
        ownerToken: HOME_PDF_VIEWER_OWNER,
      })
    })

    await waitFor(() => expect(screen.queryByText('Loading PDF...')).not.toBeInTheDocument())
    expect(screen.queryByText('previous.pdf')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'provider-paper.md, file 1 of 1, converted text' })).toBeInTheDocument()
    expect(await screen.findByText('Made-up paper text.')).toBeInTheDocument()
  })
})

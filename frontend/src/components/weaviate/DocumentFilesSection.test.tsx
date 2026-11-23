import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DOCUMENT_ID, FOUR_FILES_PAYLOAD } from '@/components/pdfViewer/viewerFilesFixtures'
import { PAPER_LISTING } from '@/features/documents/paperSourceFilesFixtures'

import DocumentFilesSection from './DocumentFilesSection'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

function stubApi(listing: { status: number; payload: unknown }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === `/api/pdf-viewer/documents/${DOCUMENT_ID}/files`) return jsonResponse(FOUR_FILES_PAYLOAD)
    if (url === `/api/weaviate/documents/${DOCUMENT_ID}/source-files`) return jsonResponse(listing.payload, listing.status)
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderSection(providerLabel: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <DocumentFilesSection documentId={DOCUMENT_ID} providerLabel={providerLabel} open />
    </QueryClientProvider>,
  )
}

describe('DocumentFilesSection (read only)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('lists the paper\'s files with their role, kind and state', async () => {
    stubApi({ status: 200, payload: PAPER_LISTING })
    renderSection('ABC')

    const list = await screen.findByRole('list', { name: 'Files in this paper' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows.map((row) => row.textContent)).toEqual([
      'made-up-paper.pdfMain paperPDFReady',
      'Table_S1.xlsxSupplementTextReady',
      'Figure_S2.pdfSupplementPDFReady',
      'Table_S3.docxSupplementTextBeing added',
    ])
  })

  it('lists the provider\'s files not in the paper, each with why, its last observation and the counts', async () => {
    stubApi({ status: 200, payload: PAPER_LISTING })
    renderSection('ABC')

    const list = await screen.findByRole('list', { name: 'Files in ABC not in this paper' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('Converted by ABC; not added to this paper')
    expect(rows[0]).toHaveTextContent('Mostly images: about 120 characters of text')
    expect(rows[1]).toHaveTextContent('A table in its converted text has rows that do not match its header')
    expect(rows[2]).toHaveTextContent('Not converted by ABC yet')
    expect(rows[2]).toHaveTextContent(
      `ABC had not converted this file when the import stopped waiting (${new Date('2026-11-22T14:05:00+00:00').toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })})`,
    )
    expect(rows[3]).toHaveTextContent('Large data file; these are not read')
    expect(screen.getByText('2 more files in ABC are not listed because you cannot open them.')).toBeInTheDocument()
    expect(screen.getByText('1 further copy of a listed file is not listed again.')).toBeInTheDocument()
    // Read only: nothing here adds or removes a file.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('does not ask the provider about an uploaded paper', async () => {
    const fetchMock = stubApi({ status: 200, payload: PAPER_LISTING })
    renderSection(null)

    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContain(`/api/weaviate/documents/${DOCUMENT_ID}/source-files`)
    expect(screen.queryByText(/Also in/)).not.toBeInTheDocument()
  })

  it('says why the provider\'s files cannot be listed when the paper\'s entry was merged', async () => {
    stubApi({ status: 409, payload: { detail: { error: 'paper_reference_mismatch' } } })
    renderSection('ABC')

    expect(await screen.findByText(
      'This paper\'s entry in ABC has been merged into another entry, so its files cannot be listed here. Import the paper again from its current entry.',
    )).toBeInTheDocument()
  })

  it('says plainly when the provider could not be checked', async () => {
    stubApi({ status: 500, payload: { detail: { error: 'file_listing_failed' } } })
    renderSection('ABC')

    expect(await screen.findByText('Could not check ABC for this paper\'s files.')).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Files in this paper' })).toBeInTheDocument()
  })
})

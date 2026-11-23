import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ViewerFilesPayload } from '@/components/pdfViewer/viewerFiles'
import { DOCUMENT_ID, FOUR_FILES_PAYLOAD } from '@/components/pdfViewer/viewerFilesFixtures'
import { PAPER_LISTING } from '@/features/documents/paperSourceFilesFixtures'

import DocumentFilesSection, { listedProviderLabel } from './DocumentFilesSection'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const PROVIDER = 'ABC Literature'
const SOURCE_FILES_URL = `/api/weaviate/documents/${DOCUMENT_ID}/source-files`
const PAPER_FILES_URL = `/api/pdf-viewer/documents/${DOCUMENT_ID}/files`

/**
 * A failed supplement in the files route's real shape: a file that is not ready has no
 * page count and no URLs (`viewer_file_refs`; #1105 leaves failed rows like this).
 */
const FAILED_SUPPLEMENT_PAYLOAD: ViewerFilesPayload = {
  ...FOUR_FILES_PAYLOAD,
  files: [
    ...FOUR_FILES_PAYLOAD.files,
    {
      file_id: 'aaaaaaaa-0000-0000-0000-000000000005',
      role: 'supplement',
      display_name: 'Table_S4.xlsx',
      display_mode: 'text',
      status: 'failed',
      tab_order: 4,
      page_count: null,
      content_url: null,
      text_url: null,
    },
  ],
}

function listingWith(files: Array<Record<string, unknown>>) {
  return { ...PAPER_LISTING, files }
}

function stubApi(listing: { status: number; payload: unknown }, paperFiles: ViewerFilesPayload = FOUR_FILES_PAYLOAD) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === PAPER_FILES_URL) return jsonResponse(paperFiles)
    if (url === `/api/weaviate/documents/${DOCUMENT_ID}/source-files`) return jsonResponse(listing.payload, listing.status)
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderSection(providerLabel: string | null, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
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
    renderSection(PROVIDER)

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
    renderSection(PROVIDER)

    const list = await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent('Converted by ABC Literature; not added to this paper')
    expect(rows[0]).toHaveTextContent('Little text: about 120 characters')
    expect(rows[1]).toHaveTextContent('A table in its converted text has rows that do not match its header')
    expect(rows[2]).toHaveTextContent('Not converted by ABC Literature yet')
    expect(rows[2]).toHaveTextContent(
      `ABC Literature had not converted this file when the import stopped waiting (${new Date('2026-11-22T14:05:00+00:00').toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })})`,
    )
    expect(rows[3]).toHaveTextContent('Large data file. Files like this are not read.')
    expect(screen.getByText('2 more files in ABC Literature are not listed because you cannot open them.')).toBeInTheDocument()
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
    renderSection(PROVIDER)

    expect(await screen.findByText(
      'This paper\'s entry in ABC Literature has been merged into another entry, so its files cannot be listed here.',
    )).toBeInTheDocument()
  })

  it('says plainly when the provider could not be checked', async () => {
    stubApi({ status: 500, payload: { detail: { error: 'file_listing_failed' } } })
    renderSection(PROVIDER)

    expect(await screen.findByText('Could not check ABC Literature for this paper\'s files.')).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Files in this paper' })).toBeInTheDocument()
  })

  it('shows a file that could not be added as such, in the files route\'s real shape', async () => {
    stubApi({ status: 200, payload: PAPER_LISTING }, FAILED_SUPPLEMENT_PAYLOAD)
    renderSection(PROVIDER)

    const list = await screen.findByRole('list', { name: 'Files in this paper' })
    expect(within(list).getAllByRole('listitem')[4]).toHaveTextContent('Table_S4.xlsxSupplementTextCould not be added')
  })

  it('words each state of a paper file differently', async () => {
    stubApi({ status: 200, payload: PAPER_LISTING }, FAILED_SUPPLEMENT_PAYLOAD)
    renderSection(PROVIDER)

    const list = await screen.findByRole('list', { name: 'Files in this paper' })
    const states = within(list).getAllByRole('listitem').map((row) => row.lastElementChild?.textContent)
    expect(states).toEqual(['Ready', 'Ready', 'Ready', 'Being added', 'Could not be added'])
  })

  it('words the main paper by what is true, never as a converted supplement', async () => {
    // ABC Literature now names another main file than the paper's stored one (P6f's code).
    const main = { ...PAPER_LISTING.files[0], in_this_paper: false, tickable: false, not_tickable_reason: 'main_differs_from_paper', converted_text_id: null, converted_md5: null }
    stubApi({ status: 200, payload: listingWith([main]) })
    renderSection(PROVIDER)

    const list = await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    const row = within(list).getByRole('listitem')
    expect(row).toHaveTextContent('Main paper')
    expect(row).toHaveTextContent('ABC Literature now has a different main file for this paper than the one in the Library')
    expect(row).not.toHaveTextContent(/Converted by/)
  })

  it('words a file with an unknown code on its own line and still shows the rest', async () => {
    const unknown = { ...PAPER_LISTING.files[4], not_tickable_reason: 'pdf_supplement_later' }
    stubApi({ status: 200, payload: listingWith([PAPER_LISTING.files[1], unknown]) })
    renderSection(PROVIDER)

    const list = await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Converted by ABC Literature; not added to this paper')
    expect(rows[1]).toHaveTextContent('Made_up_S50')
    expect(rows[1]).toHaveTextContent('This screen cannot describe this file yet.')
    expect(screen.queryByText(/pdf_supplement_later/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Could not check/)).not.toBeInTheDocument()
  })

  it('does not blame the provider when its answer cannot be read', async () => {
    stubApi({ status: 200, payload: { ...PAPER_LISTING, files: 'not a list' } })
    renderSection(PROVIDER)

    expect(await screen.findByText('This paper\'s files in ABC Literature could not be shown.')).toBeInTheDocument()
    expect(screen.queryByText(/Could not check/)).not.toBeInTheDocument()
  })

  it('keeps the dialog whole when a file is neither tickable nor gives a reason', async () => {
    stubApi({ status: 200, payload: listingWith([{ ...PAPER_LISTING.files[4], not_tickable_reason: null }]) })
    renderSection(PROVIDER)

    expect(await screen.findByText('This paper\'s files in ABC Literature could not be shown.')).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Files in this paper' })).toBeInTheDocument()
  })

  it('asks to sign in again only when the sign-in is missing', async () => {
    stubApi({ status: 409, payload: { detail: { error: 'document_source_curator_token_unavailable' } } })
    renderSection(PROVIDER)

    expect(await screen.findByText('Sign in again to see this paper\'s files in ABC Literature.')).toBeInTheDocument()
  })

  it('says the provider cannot be reached when its reader is down, not to sign in', async () => {
    stubApi({ status: 503, payload: { detail: { error: 'document_source_curator_token_unavailable' } } })
    renderSection(PROVIDER)

    expect(await screen.findByText('This server cannot reach ABC Literature just now, so this paper\'s files there cannot be listed.')).toBeInTheDocument()
    expect(screen.queryByText(/Sign in again/)).not.toBeInTheDocument()
  })

  it('does not ask the provider again when the same paper is reopened soon after', async () => {
    const fetchMock = stubApi({ status: 200, payload: PAPER_LISTING })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const first = renderSection(PROVIDER, client)
    await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    first.unmount()

    renderSection(PROVIDER, client)
    await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([url]) => String(url) === PAPER_FILES_URL)).toHaveLength(2)
    })
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === SOURCE_FILES_URL)).toHaveLength(1)
  })
})

describe('listedProviderLabel', () => {
  it('uses only the provider\'s configured label, never its raw id', () => {
    expect(listedProviderLabel({ provider: 'abc_literature', providerMetadata: { displayLabel: 'ABC Literature', referenceLabelPriority: null } })).toBe('ABC Literature')
    expect(listedProviderLabel({ provider: 'abc_literature', providerMetadata: null })).toBeNull()
    expect(listedProviderLabel(null)).toBeNull()
  })
})

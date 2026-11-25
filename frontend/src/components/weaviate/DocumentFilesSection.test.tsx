import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PAPER_FILES_REFRESH_CHANNEL,
  PAPER_FILES_REFRESH_STORAGE_KEY,
  onPDFDocumentChanged,
  type PDFViewerDocumentChangedDetail,
} from '@/components/pdfViewer/pdfEvents'
import type { ViewerFilesPayload } from '@/components/pdfViewer/viewerFiles'
import {
  DOCUMENT_ID,
  FOUR_FILES_PAYLOAD,
  MAIN_FILE_ID,
  PDF_SUPPLEMENT_FILE_ID,
  SUPPLEMENTS_ONLY_PAYLOAD,
  TEXT_SUPPLEMENT_FILE_ID,
  singleMainPdfFile,
} from '@/components/pdfViewer/viewerFilesFixtures'
import { WAITING_FOR_SUPPLEMENTS_STAGE } from '@/features/documents/supplementProgress'
import {
  subscribeDocumentsListInvalidated,
  type PdfProcessingJob,
} from '@/services/weaviate'
import fileListingFixture from '@/test/fixtures/supplements/file_listing.json'
import removalCheckFixture from '@/test/fixtures/supplements/removal_check.json'
import supplementAddMetadataFixture from '@/test/fixtures/supplements/supplement_add_metadata.json'
import supplementWaitMetadataFixture from '@/test/fixtures/supplements/supplement_wait_metadata.json'

import DocumentFilesSection, {
  PAPER_SOURCE_FILES_STALE_MS,
  clearPendingAddJobIds,
  hasPendingViewerRefresh,
  listedProviderLabel,
  paperActiveJobsQueryKey,
  paperFilesQueryKey,
} from './DocumentFilesSection'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const PROVIDER = 'ABC Literature'
const PROVIDER_SHORT = 'ABC'
const SOURCE_FILES_URL = `/api/weaviate/documents/${DOCUMENT_ID}/source-files`
const PAPER_FILES_URL = `/api/pdf-viewer/documents/${DOCUMENT_ID}/files`
const SOURCE_PROVIDER_URL = '/api/weaviate/documents/source-provider'
const ACTIVE_JOBS_URL_PREFIX = '/api/weaviate/pdf-jobs?'

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

const UPDATED_FIVE_FILES_PAYLOAD: ViewerFilesPayload = {
  ...FOUR_FILES_PAYLOAD,
  files: [
    ...FOUR_FILES_PAYLOAD.files,
    {
      file_id: '22222222-2222-2222-2222-222222222223',
      role: 'supplement',
      display_name: 'Made_up_S20.xlsx',
      display_mode: 'text',
      status: 'ready',
      tab_order: 4,
      page_count: null,
      content_url: null,
      text_url: `/api/pdf-viewer/documents/${DOCUMENT_ID}/files/22222222-2222-2222-2222-222222222223/text`,
    },
  ],
}

const SECOND_TICKABLE_FILE = {
  ...fileListingFixture.files[1],
  provider_file_id: '22',
  converted_text_id: '23',
  display_name: 'Made_up_S22',
  md5: null,
  converted_md5: 'converted-md5-22',
  check: {
    status: 'passed',
    reason: null,
    measured_value: null,
    limit_value: null,
    few_characters_count: null,
  },
}

function makeJob(overrides: Partial<PdfProcessingJob> = {}): PdfProcessingJob {
  return {
    job_id: 'job-add-1',
    document_id: DOCUMENT_ID,
    user_id: 7,
    filename: 'made-up-paper.pdf',
    status: 'pending',
    current_stage: null,
    progress_percentage: 0,
    message: null,
    cancel_requested: false,
    error_message: null,
    metadata: { job_kind: 'supplement_add' },
    created_at: '2026-11-24T12:00:00Z',
    updated_at: '2026-11-24T12:00:00Z',
    completed_at: null,
    ...overrides,
  }
}

function listingWith(files: Array<Record<string, unknown>>) {
  return { ...fileListingFixture, files }
}

interface StubApiOptions {
  listing?: { status: number; payload: unknown } | ((url: string) => Response | Promise<Response>)
  paperFiles?: ViewerFilesPayload | ((url: string, init?: RequestInit) => Response | Promise<Response>)
  activeJobs?: PdfProcessingJob[] | ((url: string) => Response | Promise<Response>)
  sourceProvider?: Record<string, unknown>
  onAddFiles?: (body: unknown, init?: RequestInit, url?: string) => Response | Promise<Response>
  onFetchJob?: (jobId: string, init?: RequestInit) => Response | Promise<Response>
  onRemovalCheck?: (fileId: string, init?: RequestInit, url?: string) => Response | Promise<Response>
  onRemoveFile?: (fileId: string, init?: RequestInit, url?: string) => Response | Promise<Response>
}

function stubApi(options: StubApiOptions = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()

    if (/^\/api\/pdf-viewer\/documents\/[^/]+\/files$/.test(url) && method === 'GET') {
      if (typeof options.paperFiles === 'function') return await options.paperFiles(url, init)
      return jsonResponse(options.paperFiles ?? FOUR_FILES_PAYLOAD)
    }
    if (/^\/api\/weaviate\/documents\/[^/]+\/source-files$/.test(url) && method === 'GET') {
      if (typeof options.listing === 'function') return await options.listing(url)
      const listing = options.listing ?? { status: 200, payload: fileListingFixture }
      return jsonResponse(listing.payload, listing.status)
    }
    if (url.startsWith(ACTIVE_JOBS_URL_PREFIX) && method === 'GET') {
      if (typeof options.activeJobs === 'function') return await options.activeJobs(url)
      const jobs = options.activeJobs ?? []
      return jsonResponse({ jobs, total: jobs.length, limit: 50, offset: 0 })
    }
    if (url === SOURCE_PROVIDER_URL && method === 'GET') {
      return jsonResponse(
        options.sourceProvider ?? {
          provider: 'abc_literature',
          import_enabled: true,
          presentation: {
            display_label: 'ABC Literature',
            short_label: 'ABC',
          },
        },
      )
    }
    if (/^\/api\/weaviate\/documents\/[^/]+\/files$/.test(url) && method === 'POST') {
      if (!options.onAddFiles) {
        throw new Error('Unexpected POST to add files without handler')
      }
      const parsedBody = typeof init?.body === 'string' ? JSON.parse(init.body) : null
      return await options.onAddFiles(parsedBody, init, url)
    }
    if (url.startsWith('/api/weaviate/pdf-jobs/') && method === 'GET') {
      if (!options.onFetchJob) {
        throw new Error(`Unexpected job poll without handler: ${url}`)
      }
      const jobId = decodeURIComponent(url.slice('/api/weaviate/pdf-jobs/'.length))
      return await options.onFetchJob(jobId, init)
    }
    const removalCheckMatch = /^\/api\/weaviate\/documents\/[^/]+\/files\/([^/]+)\/removal-check$/.exec(url)
    if (removalCheckMatch && method === 'GET') {
      if (!options.onRemovalCheck) {
        throw new Error(`Unexpected removal check without handler: ${url}`)
      }
      return await options.onRemovalCheck(decodeURIComponent(removalCheckMatch[1]!), init, url)
    }
    const removeFileMatch = /^\/api\/weaviate\/documents\/[^/]+\/files\/([^/]+)$/.exec(url)
    if (removeFileMatch && method === 'DELETE') {
      if (!options.onRemoveFile) {
        throw new Error(`Unexpected file removal without handler: ${url}`)
      }
      return await options.onRemoveFile(decodeURIComponent(removeFileMatch[1]!), init, url)
    }
    throw new Error(`Unexpected fetch: ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderSection(
  props: {
    documentId?: string
    providerLabel: string | null
    providerShortLabel?: string | null
    configuredProviderShortLabel?: string | null
    processingStatus?: string | null
    open?: boolean
    onFilesChanged?: () => void
    pollIntervalMs?: number
  },
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return render(
    <QueryClientProvider client={client}>
      <DocumentFilesSection
        documentId={props.documentId ?? DOCUMENT_ID}
        providerLabel={props.providerLabel}
        providerShortLabel={props.providerShortLabel}
        configuredProviderShortLabel={props.configuredProviderShortLabel}
        processingStatus={props.processingStatus === undefined ? 'completed' : props.processingStatus}
        open={props.open ?? true}
        onFilesChanged={props.onFilesChanged}
        pollIntervalMs={props.pollIntervalMs ?? 20}
      />
    </QueryClientProvider>,
  )
}

describe('DocumentFilesSection (Library Files + Add)', () => {
  afterEach(() => {
    clearPendingAddJobIds()
    vi.unstubAllGlobals()
  })

  it('lists the paper\'s files with their role, kind and state', async () => {
    stubApi()
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    const list = await screen.findByRole('list', { name: 'Files in this paper' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows.map((row) => row.textContent)).toEqual([
      'made-up-paper.pdfMain paperPDFReady',
      'Table_S1.xlsxSupplementTextReadyRemove',
      'Figure_S2.pdfSupplementPDFReadyRemove',
      'Table_S3.docxSupplementTextBeing added',
    ])
  })

  it('lists the provider\'s other files with tickboxes only on addable supplements, keeping descriptions and counts', async () => {
    stubApi()
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    const list = await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(5)
    expect(rows[0]).toHaveTextContent('Converted by ABC Literature; not added to this paper')
    expect(rows[0]).toHaveTextContent('Little text: about 120 characters')
    expect(rows[1]).toHaveTextContent('A table in its converted text has rows that do not match its header')
    expect(rows[2]).toHaveTextContent('Not converted by ABC Literature yet')
    expect(rows[2]).toHaveTextContent(
      `ABC Literature had not converted this file when the import stopped waiting (${new Date('2026-11-22T14:05:00+00:00').toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })})`,
    )
    expect(rows[3]).toHaveTextContent('Large data file. Files like this are not read.')
    expect(rows[4]).toHaveTextContent('File type not supported')
    expect(screen.getByText('2 more files in ABC Literature are not listed because you cannot open them.')).toBeInTheDocument()
    expect(screen.getByText('1 further copy of a listed file is not listed again.')).toBeInTheDocument()

    // Only the tickable converted supplement gets a checkbox, and the Add button starts disabled at 0.
    const checkboxes = await screen.findAllByRole('checkbox')
    expect(checkboxes).toHaveLength(1)
    expect(screen.getByRole('checkbox', { name: 'Made_up_S20' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Add 0 files' })).toBeDisabled()
  })

  it('ticks multiple files without duplicates, posts exact {files:[{provider_file_id,md5,converted_md5}]}, polls to terminal, renders SupplementFilesList, refreshes both lists, notifies parent, and refreshes open viewer only for this paper', async () => {
    const user = userEvent.setup()
    const onFilesChanged = vi.fn()
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))

    let listingCalls = 0
    let paperFilesCalls = 0
    let pollCalls = 0
    let submittedBody: unknown = null

    const initialListing = listingWith([
      fileListingFixture.files[0],
      fileListingFixture.files[1],
      SECOND_TICKABLE_FILE,
      fileListingFixture.files[3],
    ])
    const refreshedListing = listingWith([
      fileListingFixture.files[0],
      { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
      fileListingFixture.files[3],
    ])

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : UPDATED_FIVE_FILES_PAYLOAD)
      },
      listing: () => {
        listingCalls += 1
        return jsonResponse(listingCalls === 1 ? initialListing : refreshedListing)
      },
      onAddFiles: (body) => {
        submittedBody = body
        return jsonResponse(makeJob({ status: 'pending' }), 202)
      },
      onFetchJob: () => {
        pollCalls += 1
        if (pollCalls === 1) {
          return jsonResponse(makeJob({ status: 'running', current_stage: 'checking_files', message: 'Checking files...' }))
        }
        return jsonResponse(
          makeJob({
            status: 'completed',
            message: 'Added 1 of 2 files.',
            metadata: supplementAddMetadataFixture,
          }),
        )
      },
    })

    try {
      renderSection({
        providerLabel: PROVIDER,
        providerShortLabel: PROVIDER_SHORT,
        onFilesChanged,
      })

      const firstBox = await screen.findByRole('checkbox', { name: 'Made_up_S20' })
      const secondBox = screen.getByRole('checkbox', { name: 'Made_up_S22' })
      expect(screen.getByRole('button', { name: 'Add 0 files' })).toBeDisabled()

      // Toggle twice on firstBox to verify selection deduplication / toggle behavior.
      await user.click(firstBox)
      expect(screen.getByRole('button', { name: 'Add 1 file' })).toBeEnabled()
      await user.click(firstBox)
      expect(screen.getByRole('button', { name: 'Add 0 files' })).toBeDisabled()

      await user.click(firstBox)
      await user.click(secondBox)
      const addButton = screen.getByRole('button', { name: 'Add 2 files' })
      expect(addButton).toBeEnabled()

      await user.click(addButton)

      // Exact backend AddFilesRequest schema: ONLY provider_file_id, md5, converted_md5.
      await waitFor(() => {
        expect(submittedBody).toEqual({
          files: [
            {
              provider_file_id: '20',
              md5: 'original-md5-20',
              converted_md5: 'converted-md5-20',
            },
            {
              provider_file_id: '22',
              md5: null,
              converted_md5: 'converted-md5-22',
            },
          ],
        })
      })

      // N37 heading appears immediately and polling reaches terminal SupplementFilesList outcomes.
      expect(await screen.findByText('Adding files')).toBeInTheDocument()
      const progressList = await screen.findByRole('list', { name: 'Supplement files' })
      expect(within(progressList).getByText('Table_S1.xlsx')).toBeInTheDocument()
      expect(within(progressList).getByText('This PDF is larger than 20 MB, so it was not added.')).toBeInTheDocument()
      expect(within(progressList).getByText('Not added: ABC no longer lists this file for the paper.')).toBeInTheDocument()

      // Both lists refreshed, parent notified, and open viewer notified with refreshOnlyIfOpen: true.
      await waitFor(() => {
        expect(paperFilesCalls).toBe(2)
        expect(listingCalls).toBe(2)
        expect(onFilesChanged).toHaveBeenCalledTimes(1)
        expect(viewerEvents).toHaveLength(1)
      })
      expect(screen.getByText('Made_up_S20.xlsx')).toBeInTheDocument()
      expect(screen.queryByRole('checkbox', { name: 'Made_up_S20' })).not.toBeInTheDocument()
      expect(viewerEvents[0]).toMatchObject({
        documentId: DOCUMENT_ID,
        refreshOnlyIfOpen: true,
      })
      expect(viewerEvents[0]!.files.map((file) => file.file_id)).toContain('22222222-2222-2222-2222-222222222223')
    } finally {
      unsubscribeViewer()
    }
  })

  it('shows N34 and renders NO tickboxes when an active job exists on mount, follows SupplementFilesList, and refreshes when terminal', async () => {
    let activeJobPhase: 'active' | 'done' = 'active'
    let listingCalls = 0
    let paperFilesCalls = 0

    const waitingJob = makeJob({
      job_id: 'job-wait-1',
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      metadata: supplementWaitMetadataFixture.mixed_waiting,
    })

    stubApi({
      activeJobs: () =>
        jsonResponse({
          jobs: activeJobPhase === 'active' ? [waitingJob] : [],
          total: activeJobPhase === 'active' ? 1 : 0,
          limit: 50,
          offset: 0,
        }),
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : UPDATED_FIVE_FILES_PAYLOAD)
      },
      listing: () => {
        listingCalls += 1
        return jsonResponse(fileListingFixture)
      },
      onFetchJob: () => {
        activeJobPhase = 'done'
        return jsonResponse(
          makeJob({
            job_id: 'job-wait-1',
            status: 'completed',
            current_stage: 'completed',
            metadata: supplementWaitMetadataFixture.finished_with_wait_timestamp,
          }),
        )
      },
    })

    renderSection({
      providerLabel: PROVIDER,
      providerShortLabel: PROVIDER_SHORT,
      pollIntervalMs: 30,
    })

    expect(
      await screen.findByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add \d+ file/ })).not.toBeInTheDocument()
    expect(await screen.findByText('Waiting for ABC')).toBeInTheDocument()

    // Once the active job finishes, both lists refresh and tickboxes appear.
    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    await waitFor(() => {
      expect(paperFilesCalls).toBe(2)
      expect(listingCalls).toBe(2)
    })
  })

  it('ignores active jobs belonging to other documents so tickboxes stay available for this paper', async () => {
    stubApi({
      activeJobs: [
        makeJob({
          job_id: 'job-other-doc',
          document_id: 'bbbbbbbb-0000-0000-0000-000000000099',
          status: 'running',
        }),
      ],
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    expect(
      screen.queryByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).not.toBeInTheDocument()
  })

  it('blocks selecting and adding files when the paper processingStatus is not completed', async () => {
    for (const status of ['failed', 'processing', 'pending', null] as const) {
      stubApi()
      const view = renderSection({
        providerLabel: PROVIDER,
        providerShortLabel: PROVIDER_SHORT,
        processingStatus: status,
      })

      await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Add \d+ file/ })).not.toBeInTheDocument()
      view.unmount()
    }
  })

  it('finds an active job on page 2 of paginated active jobs and hides tickboxes with N34', async () => {
    const otherJob = makeJob({
      job_id: 'job-other-page1',
      document_id: 'bbbbbbbb-0000-0000-0000-000000000099',
      status: 'running',
      metadata: {},
    })
    const targetWaitingJob = makeJob({
      job_id: 'job-target-page2',
      document_id: DOCUMENT_ID,
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      metadata: supplementWaitMetadataFixture.mixed_waiting,
    })

    stubApi({
      activeJobs: (url) => {
        const offset = new URL(url, 'http://localhost').searchParams.get('offset')
        if (!offset || offset === '0') {
          return jsonResponse({ jobs: [otherJob], total: 2, limit: 1, offset: 0 })
        }
        return jsonResponse({ jobs: [targetWaitingJob], total: 2, limit: 1, offset: 1 })
      },
      onFetchJob: () => jsonResponse(targetWaitingJob),
    })

    renderSection({
      providerLabel: PROVIDER,
      providerShortLabel: PROVIDER_SHORT,
      pollIntervalMs: 500,
    })

    expect(
      await screen.findByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add \d+ file/ })).not.toBeInTheDocument()
  })

  it('fails closed when active-job lookup fails and offers Try again to restore tickboxes', async () => {
    const user = userEvent.setup()
    let activeJobCalls = 0
    stubApi({
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 1) {
          return jsonResponse({ detail: 'Jobs service unavailable' }, 500)
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    expect(await screen.findByText('Could not check whether this paper has an active import.')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add \d+ file/ })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    expect(activeJobCalls).toBe(2)
  })

  it('reconciles a retained add job when closing the dialog mid-add and reopening after completion, busting the 5-minute listing cache', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let listingCalls = 0
    let paperFilesCalls = 0
    let jobStatus: 'running' | 'completed' = 'running'

    const refreshedListing = listingWith([
      fileListingFixture.files[0],
      { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
      fileListingFixture.files[3],
    ])

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : UPDATED_FIVE_FILES_PAYLOAD)
      },
      listing: () => {
        listingCalls += 1
        return jsonResponse(listingCalls === 1 ? fileListingFixture : refreshedListing)
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-retained-1', status: 'pending' }), 202),
      onFetchJob: () =>
        jsonResponse(
          makeJob({
            job_id: 'job-retained-1',
            status: jobStatus,
            message: jobStatus === 'completed' ? 'Added 1 of 1 files.' : null,
            metadata: jobStatus === 'completed' ? supplementAddMetadataFixture : { job_kind: 'supplement_add' },
          }),
        ),
    })

    const view = renderSection(
      {
        providerLabel: PROVIDER,
        providerShortLabel: PROVIDER_SHORT,
        pollIntervalMs: 500,
      },
      client,
    )

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
    expect(await screen.findByText('Adding files')).toBeInTheDocument()

    // Close the dialog while the add is still running, then the backend finishes the job.
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open={false}
          pollIntervalMs={500}
        />
      </QueryClientProvider>,
    )
    jobStatus = 'completed'

    // Reopen the same paper within the 5-minute listing cache window.
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={500}
        />
      </QueryClientProvider>,
    )

    // Retained job reconciliation shows the completed outcomes, busts /source-files, and never re-offers Made_up_S20.
    expect(await screen.findByText('Added 1 of 1 files.')).toBeInTheDocument()
    await waitFor(() => {
      expect(listingCalls).toBe(2)
      expect(paperFilesCalls).toBeGreaterThanOrEqual(2)
    })
    expect(screen.getByText('Made_up_S20.xlsx')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Made_up_S20' })).not.toBeInTheDocument()
  })

  it('does not leak state or viewer refresh when switching documentId or closing while POST /files or polling is in flight', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const OTHER_DOC_ID = '33333333-3333-3333-3333-333333333333'
    const onFilesChanged = vi.fn()
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))

    let resolveAdd!: (response: Response) => void
    let addAborted = false
    stubApi({
      paperFiles: (url) => {
        const docId = url.includes(OTHER_DOC_ID) ? OTHER_DOC_ID : DOCUMENT_ID
        return jsonResponse({ ...FOUR_FILES_PAYLOAD, document_id: docId })
      },
      onAddFiles: (_body, init) =>
        new Promise<Response>((resolve) => {
          init?.signal?.addEventListener('abort', () => {
            addAborted = true
          })
          resolveAdd = resolve
        }),
    })

    try {
      const view = renderSection(
        {
          documentId: DOCUMENT_ID,
          providerLabel: PROVIDER,
          providerShortLabel: PROVIDER_SHORT,
          onFilesChanged,
        },
        client,
      )

      await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
      await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

      // Switch to Paper B while Paper A's POST /files is still in flight.
      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={OTHER_DOC_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open
            onFilesChanged={onFilesChanged}
            pollIntervalMs={20}
          />
        </QueryClientProvider>,
      )

      expect(addAborted).toBe(true)
      // Even if the in-flight promise later resolves, Paper B stays clean with zero cross-paper bleed.
      resolveAdd(jsonResponse(makeJob({ document_id: DOCUMENT_ID, status: 'completed' }), 202))
      await screen.findByRole('checkbox', { name: 'Made_up_S20' })
      expect(screen.queryByText('Adding files')).not.toBeInTheDocument()
      expect(onFilesChanged).not.toHaveBeenCalled()
      expect(viewerEvents).toHaveLength(0)

      // Now verify closing the dialog (open: true -> false) with unchanged documentId also aborts in-flight POST.
      addAborted = false
      await user.click(screen.getByRole('checkbox', { name: 'Made_up_S20' }))
      await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={OTHER_DOC_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open={false}
            onFilesChanged={onFilesChanged}
            pollIntervalMs={20}
          />
        </QueryClientProvider>,
      )
      expect(addAborted).toBe(true)
      resolveAdd(jsonResponse({ detail: { error: 'paper_file_limit', limit_value: 20 } }, 422))
      await new Promise((resolve) => setTimeout(resolve, 30))
      expect(onFilesChanged).not.toHaveBeenCalled()
      expect(viewerEvents).toHaveLength(0)
    } finally {
      unsubscribeViewer()
    }
  })

  it('reconciles HTTP 404 during job polling against active jobs and releases Add when no active job remains', async () => {
    const user = userEvent.setup()
    let activeJobCalls = 0
    let paperFilesCalls = 0
    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(FOUR_FILES_PAYLOAD)
      },
      activeJobs: () => {
        activeJobCalls += 1
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-vanished-404', status: 'pending' }), 202),
      onFetchJob: () => jsonResponse({ detail: 'PDF job job-vanished-404 not found' }, 404),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    // After 404 reconciliation finds no active job, Add is released cleanly (checkboxes return) and paper files refresh.
    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    await waitFor(() => {
      expect(activeJobCalls).toBeGreaterThanOrEqual(2)
      expect(paperFilesCalls).toBeGreaterThanOrEqual(2)
    })
    expect(screen.queryByText('Could not check progress for adding files.')).not.toBeInTheDocument()
  })

  it('keeps "Adding files" without popping up N34 across Refresh details and dialog reopen during an active supplement_add job', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const activeAddJob = makeJob({
      job_id: 'job-add-live',
      status: 'running',
      current_stage: 'checking_files',
      metadata: { job_kind: 'supplement_add' },
    })
    let jobActiveOnServer = false

    stubApi({
      activeJobs: () =>
        jsonResponse({
          jobs: jobActiveOnServer ? [activeAddJob] : [],
          total: jobActiveOnServer ? 1 : 0,
          limit: 50,
          offset: 0,
        }),
      onAddFiles: () => {
        jobActiveOnServer = true
        return jsonResponse(activeAddJob, 202)
      },
      onFetchJob: () => jsonResponse(activeAddJob),
    })

    const view = renderSection(
      { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 500 },
      client,
    )

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
    expect(await screen.findByText('Adding files')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()

    // Simulate clicking "Refresh details" (invalidates active jobs while supplement_add is active).
    await client.invalidateQueries({ queryKey: paperActiveJobsQueryKey(DOCUMENT_ID) })
    await waitFor(() => expect(screen.getByText('Adding files')).toBeInTheDocument())
    expect(
      screen.queryByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).not.toBeInTheDocument()

    // Unmount and mount a fresh section while the supplement_add job is still active on the server.
    view.unmount()
    clearPendingAddJobIds()
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 500 })

    expect(await screen.findByText('Adding files')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(
      screen.queryByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).not.toBeInTheDocument()
  })

  it('clears a stale 409 import_in_progress (N35) refusal once the discovered active job finishes', async () => {
    const user = userEvent.setup()
    let phase: 'idle' | 'active' | 'done' = 'idle'
    const discoveredImportJob = makeJob({
      job_id: 'job-race-import',
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      metadata: supplementWaitMetadataFixture.mixed_waiting,
    })

    stubApi({
      activeJobs: () =>
        jsonResponse({
          jobs: phase === 'active' ? [discoveredImportJob] : [],
          total: phase === 'active' ? 1 : 0,
          limit: 50,
          offset: 0,
        }),
      onAddFiles: () => {
        phase = 'active'
        return jsonResponse({ detail: { error: 'import_in_progress' } }, 409)
      },
      onFetchJob: () => {
        phase = 'done'
        return jsonResponse(
          makeJob({
            job_id: 'job-race-import',
            status: 'completed',
            current_stage: 'completed',
            metadata: supplementWaitMetadataFixture.finished_with_wait_timestamp,
          }),
        )
      },
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 30 })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    // Once the discovered job finishes, N35/N34 is gone and checkboxes return cleanly.
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    })
    expect(
      screen.queryByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).not.toBeInTheDocument()
  })

  it.each([
    {
      label: 'failed add job without supplement entries',
      terminalJob: makeJob({
        job_id: 'job-failed',
        status: 'failed',
        message: 'Adding files failed.',
        metadata: { job_kind: 'supplement_add', error_code: 'add_failed' },
      }),
      expectedText: 'Adding files failed.',
    },
    {
      label: 'timed-out add job without supplement entries',
      terminalJob: makeJob({
        job_id: 'job-timeout',
        status: 'failed',
        current_stage: 'timed_out',
        message: 'Adding files took too long and was stopped.',
        metadata: { job_kind: 'supplement_add', error_code: 'add_timed_out' },
      }),
      expectedText: 'Adding files took too long and was stopped.',
    },
    {
      label: 'cancelled add job without supplement entries',
      terminalJob: makeJob({
        job_id: 'job-cancelled',
        status: 'cancelled',
        message: null,
        metadata: { job_kind: 'supplement_add' },
      }),
      expectedText: 'Cancelled',
    },
  ])('renders terminal status cleanly for $label', async ({ terminalJob, expectedText }) => {
    const user = userEvent.setup()
    stubApi({
      onAddFiles: () => jsonResponse(makeJob({ job_id: terminalJob.job_id, status: 'pending' }), 202),
      onFetchJob: () => jsonResponse(terminalJob),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    expect(await screen.findByText('Adding files')).toBeInTheDocument()
    expect(await screen.findByText(expectedText)).toBeInTheDocument()
  })

  it('offers Try again on a transient 500 poll error and resumes polling to completion', async () => {
    const user = userEvent.setup()
    let pollAttempts = 0
    stubApi({
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-poll-retry', status: 'pending' }), 202),
      onFetchJob: () => {
        pollAttempts += 1
        if (pollAttempts === 1) {
          return jsonResponse({ detail: 'Temporary gateway error' }, 500)
        }
        return jsonResponse(
          makeJob({
            job_id: 'job-poll-retry',
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        )
      },
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    expect(await screen.findByText('Could not check progress for adding files.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByText('Added 1 of 1 files.')).toBeInTheDocument()
    expect(pollAttempts).toBe(2)
  })

  it('handles 401 on POST /files without offering a dead Try again button', async () => {
    const user = userEvent.setup()
    stubApi({
      onAddFiles: () => jsonResponse({ detail: 'Unauthorized' }, 401),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    expect(await screen.findByText("Sign in again to see this paper's files in ABC Literature.")).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('keeps tickboxes hidden while activeJobsQuery is refetching over cached empty data, while retained-job check fails, and while post-add /source-files refresh is pending', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let activeJobCalls = 0
    let resolveActiveRefetch!: (response: Response) => void
    let listingCalls = 0
    let resolveSecondListing!: (response: Response) => void
    let retainedJobPollAttempts = 0

    stubApi({
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 2) {
          return new Promise<Response>((resolve) => {
            resolveActiveRefetch = resolve
          })
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      listing: () => {
        listingCalls += 1
        if (listingCalls === 2) {
          return new Promise<Response>((resolve) => {
            resolveSecondListing = resolve
          })
        }
        return jsonResponse(fileListingFixture)
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-fail-closed', status: 'pending' }), 202),
      onFetchJob: () => {
        retainedJobPollAttempts += 1
        if (retainedJobPollAttempts === 1) {
          // Transient 500 during retained-job reconciliation on reopen.
          return jsonResponse({ detail: 'Transient error' }, 500)
        }
        return jsonResponse(
          makeJob({
            job_id: 'job-fail-closed',
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        )
      },
    })

    const view = renderSection(
      { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 500 },
      client,
    )

    // Initial load settles -> checkbox visible; start an add job and close dialog before polling.
    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
    expect(await screen.findByText('Adding files')).toBeInTheDocument()

    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open={false}
          pollIntervalMs={500}
        />
      </QueryClientProvider>,
    )

    // Reopen: activeJobsQuery is refetching (call 2 pending) AND retained-job check fails with 500.
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={500}
        />
      </QueryClientProvider>,
    )

    // While activeJobsQuery is refetching over cached [] -> NO checkboxes!
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    resolveActiveRefetch(jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 }))

    // Retained-job check failed with 500 -> stays fail closed (NO checkboxes over stale 5-min cache!).
    expect(await screen.findByText('Could not check progress for adding files.')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()

    // Click Try again -> retained job resolves completed, triggering post-add /source-files refetch (pending).
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Added 1 of 1 files.')).toBeInTheDocument()
    // While post-add /source-files refresh is still pending -> NO checkboxes (prevents re-ticking just-added file!).
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()

    // Once refreshed listing resolves without Made_up_S20 -> state settles cleanly.
    resolveSecondListing(
      jsonResponse(
        listingWith([
          fileListingFixture.files[0],
          { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
          SECOND_TICKABLE_FILE,
        ]),
      ),
    )
    expect(await screen.findByRole('checkbox', { name: 'Made_up_S22' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Made_up_S20' })).not.toBeInTheDocument()
  })

  it('publishes viewer refresh even if the dialog closes right as job completes while /source-files is still pending', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const onFilesChanged = vi.fn()
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))

    let listingCalls = 0
    let paperFilesCalls = 0
    let resolveSecondViewerFiles!: (response: Response) => void
    let resolveSecondListing!: (response: Response) => void

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        if (paperFilesCalls === 1) {
          return jsonResponse(FOUR_FILES_PAYLOAD)
        }
        return new Promise<Response>((resolve) => {
          resolveSecondViewerFiles = resolve
        })
      },
      listing: () => {
        listingCalls += 1
        if (listingCalls === 1) {
          return jsonResponse(fileListingFixture)
        }
        return new Promise<Response>((resolve) => {
          resolveSecondListing = resolve
        })
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-close-on-finish', status: 'pending' }), 202),
      onFetchJob: () =>
        jsonResponse(
          makeJob({
            job_id: 'job-close-on-finish',
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        ),
    })

    try {
      const view = renderSection(
        {
          providerLabel: PROVIDER,
          providerShortLabel: PROVIDER_SHORT,
          onFilesChanged,
          pollIntervalMs: 20,
        },
        client,
      )

      await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
      await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

      await waitFor(() => expect(paperFilesCalls).toBe(2))

      // Curator closes the dialog before /files or /source-files finishes resolving.
      view.unmount()

      // /files resolves independently while /source-files is still pending -> viewer STILL refreshes!
      resolveSecondViewerFiles(jsonResponse(UPDATED_FIVE_FILES_PAYLOAD))
      await waitFor(() => expect(viewerEvents).toHaveLength(1))
      expect(viewerEvents[0]).toMatchObject({
        documentId: DOCUMENT_ID,
        refreshOnlyIfOpen: true,
      })
      // Dialog-local callback is NOT invoked after unmount.
      resolveSecondListing(jsonResponse(fileListingFixture))
      await new Promise((resolve) => setTimeout(resolve, 20))
      expect(onFilesChanged).not.toHaveBeenCalled()
    } finally {
      unsubscribeViewer()
    }
  })

  it('fails closed with one single retryable alert and never falsely completes when active-jobs refetch returns 500 after poll 404', async () => {
    const user = userEvent.setup()
    let activeJobCalls = 0
    let paperFilesCalls = 0

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(FOUR_FILES_PAYLOAD)
      },
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 1) {
          return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
        }
        return jsonResponse({ detail: 'Active jobs backend unavailable' }, 500)
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-404-then-500', status: 'pending' }), 202),
      onFetchJob: () => jsonResponse({ detail: 'Not found' }, 404),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    expect(await screen.findByText('Could not check whether this paper has an active import.')).toBeInTheDocument()
    // Exactly ONE warning alert and ONE Try again button; NO false completion refresh; NO checkboxes!
    expect(screen.getAllByRole('button', { name: 'Try again' })).toHaveLength(1)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(paperFilesCalls).toBe(1)
  })

  it('clears N35 when 409 import_in_progress follow-up active-jobs lookup succeeds empty so N35 never sits beside enabled checkboxes', async () => {
    const user = userEvent.setup()
    let activeJobCalls = 0
    stubApi({
      activeJobs: () => {
        activeJobCalls += 1
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      onAddFiles: () => jsonResponse({ detail: { error: 'import_in_progress' } }, 409),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    await waitFor(() => expect(activeJobCalls).toBeGreaterThanOrEqual(2))
    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    expect(
      screen.queryByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).not.toBeInTheDocument()
  })

  it('prunes retained add job entries older than PAPER_SOURCE_FILES_STALE_MS on reopen', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const nowSpy = vi.spyOn(Date, 'now')
    const baseTime = 1_700_000_000_000
    nowSpy.mockReturnValue(baseTime)

    let jobFetchCalls = 0
    stubApi({
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-stale-retained', status: 'pending' }), 202),
      onFetchJob: () => {
        jobFetchCalls += 1
        return jsonResponse(makeJob({ job_id: 'job-stale-retained', status: 'completed' }))
      },
    })

    try {
      const view = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 500 },
        client,
      )

      await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
      await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
      expect(await screen.findByText('Adding files')).toBeInTheDocument()

      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={DOCUMENT_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open={false}
            pollIntervalMs={500}
          />
        </QueryClientProvider>,
      )

      // Advance past PAPER_SOURCE_FILES_STALE_MS and reopen -> retained entry is pruned and never fetched.
      nowSpy.mockReturnValue(baseTime + PAPER_SOURCE_FILES_STALE_MS + 1_000)
      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={DOCUMENT_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open
            pollIntervalMs={500}
          />
        </QueryClientProvider>,
      )

      expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
      expect(jobFetchCalls).toBe(0)
    } finally {
      nowSpy.mockRestore()
    }
  })

  it('shows a single retryable alert when a server-adopted active job hits 404 then 500, and refreshes /source-files when Try again succeeds empty', async () => {
    const user = userEvent.setup()
    let activeJobCalls = 0
    let listingCalls = 0
    let paperFilesCalls = 0

    const adoptedWaitingJob = makeJob({
      job_id: 'job-adopted-404',
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      metadata: supplementWaitMetadataFixture.mixed_waiting,
    })

    stubApi({
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 1) {
          return jsonResponse({ jobs: [adoptedWaitingJob], total: 1, limit: 50, offset: 0 })
        }
        if (activeJobCalls === 2) {
          return jsonResponse({ detail: 'Temporary active-jobs failure' }, 500)
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      listing: () => {
        listingCalls += 1
        return jsonResponse(fileListingFixture)
      },
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : UPDATED_FIVE_FILES_PAYLOAD)
      },
      onFetchJob: () => jsonResponse({ detail: 'Not found' }, 404),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 })

    // Initially adopted -> N34 shown, then poll returns 404 -> active-jobs refetch returns 500 ->
    // stale serverActiveJob is NOT retained; single retryable warning appears instead of deadlock!
    expect(await screen.findByText('Could not check whether this paper has an active import.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Try again' })).toHaveLength(1)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(listingCalls).toBe(1)

    // Clicking Try again succeeds with [] -> deferred 404 reconciliation refreshes /files and /source-files before enabling Add!
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    await waitFor(() => {
      expect(listingCalls).toBe(2)
      expect(paperFilesCalls).toBe(2)
    })
  })

  it('keeps tickboxes hidden on reopen while post-add /source-files is fetching, and clears retainedReconcileFailed when an adopted job finishes', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let listingCalls = 0
    let resolveSecondListing!: (response: Response) => void
    let fetchJobCalls = 0
    let activeJobPhase: 'none' | 'adopted' | 'done' = 'none'

    const runningAddJob = makeJob({
      job_id: 'job-reopen-edge',
      status: 'running',
      metadata: { job_kind: 'supplement_add' },
    })

    stubApi({
      activeJobs: () => {
        const jobs = activeJobPhase === 'adopted' ? [runningAddJob] : []
        return jsonResponse({ jobs, total: jobs.length, limit: 50, offset: 0 })
      },
      listing: () => {
        listingCalls += 1
        if (listingCalls === 2) {
          return new Promise<Response>((resolve) => {
            resolveSecondListing = resolve
          })
        }
        return jsonResponse(fileListingFixture)
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-reopen-edge', status: 'pending' }), 202),
      onFetchJob: () => {
        fetchJobCalls += 1
        if (fetchJobCalls === 1) {
          // Retained-job check fails with 500 on reopen, while activeJobsQuery returns the running job!
          return jsonResponse({ detail: 'Transient retained check 500' }, 500)
        }
        activeJobPhase = 'done'
        return jsonResponse(
          makeJob({
            job_id: 'job-reopen-edge',
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        )
      },
    })

    const view = renderSection(
      { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 },
      client,
    )

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
    expect(await screen.findByText('Adding files')).toBeInTheDocument()

    // Close dialog mid-add, then reopen while server still reports the running job (adopted path).
    activeJobPhase = 'adopted'
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open={false}
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )

    // Retained check hit 500, active job was adopted, polled to completed, and triggered listing call 2 (still pending).
    expect(await screen.findByText('Added 1 of 1 files.')).toBeInTheDocument()
    await waitFor(() => expect(listingCalls).toBe(2))
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()

    // Close and reopen while listing call 2 is STILL in flight -> checkboxes MUST stay hidden (`!listingQuery.isFetching`).
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open={false}
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()

    // Once listing call 2 finishes without Made_up_S20, Add is cleanly enabled for Made_up_S22 (retainedReconcileFailed cleared!).
    resolveSecondListing(
      jsonResponse(
        listingWith([
          fileListingFixture.files[0],
          { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
          SECOND_TICKABLE_FILE,
        ]),
      ),
    )
    expect(await screen.findByRole('checkbox', { name: 'Made_up_S22' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Made_up_S20' })).not.toBeInTheDocument()
  })

  it('fires a fresh uncancelled viewer /files GET and publishes viewer refresh even when the initial viewer /files observer GET is still in flight on completion + unmount', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))

    let paperFilesCalls = 0
    let resolveCompletionViewerFetch!: (response: Response) => void

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        if (paperFilesCalls === 1) {
          // Initial viewer files observer GET stays pending until unmount aborts it.
          return new Promise<Response>(() => undefined)
        }
        return new Promise<Response>((resolve) => {
          resolveCompletionViewerFetch = resolve
        })
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-fresh-viewer-get', status: 'pending' }), 202),
      onFetchJob: () =>
        jsonResponse(
          makeJob({
            job_id: 'job-fresh-viewer-get',
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        ),
    })

    try {
      const view = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 },
        client,
      )

      await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
      await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

      // Completion fires a SECOND, independent /files GET even though call 1 is still pending!
      await waitFor(() => expect(paperFilesCalls).toBe(2))

      // Unmount the dialog immediately; call 2 is NOT bound to the observer signal so it still resolves and refreshes the viewer.
      view.unmount()
      resolveCompletionViewerFetch(jsonResponse(UPDATED_FIVE_FILES_PAYLOAD))

      await waitFor(() => expect(viewerEvents).toHaveLength(1))
      expect(viewerEvents[0]).toMatchObject({
        documentId: DOCUMENT_ID,
        refreshOnlyIfOpen: true,
      })
      expect(viewerEvents[0]!.files.map((file) => file.file_id)).toContain('22222222-2222-2222-2222-222222222223')
    } finally {
      unsubscribeViewer()
    }
  })

  it('clears N35 after 409 import_in_progress -> 500 active-jobs failure -> Try again empty lookup', async () => {
    const user = userEvent.setup()
    let activeJobCalls = 0

    stubApi({
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 2) {
          return jsonResponse({ detail: 'Temporary active-jobs failure' }, 500)
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      onAddFiles: () => jsonResponse({ detail: { error: 'import_in_progress' } }, 409),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    // After 409 -> 500, checkboxes are hidden (fail closed), N35 and active-jobs retry warning are shown.
    expect(
      await screen.findByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).toBeInTheDocument()
    expect(screen.getByText('Could not check whether this paper has an active import.')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()

    // Clicking Try again succeeds with [] -> N35 clears cleanly and checkboxes return!
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
    expect(
      screen.queryByText("This paper's import is still running. You can add or remove files when it has finished."),
    ).not.toBeInTheDocument()
  })

  it('ignores a late retained-job 500 after the adopted active job already finished and cleared the retained entry', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let activeJobPhase: 'idle' | 'adopted' | 'finished' = 'idle'
    let listingCalls = 0
    let fetchJobCalls = 0
    let resolveLateRetainedCheck!: (response: Response) => void

    stubApi({
      listing: () => {
        listingCalls += 1
        if (listingCalls === 1) {
          return jsonResponse(
            listingWith([fileListingFixture.files[0], fileListingFixture.files[1], SECOND_TICKABLE_FILE]),
          )
        }
        return jsonResponse(
          listingWith([
            fileListingFixture.files[0],
            { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
            SECOND_TICKABLE_FILE,
          ]),
        )
      },
      activeJobs: () => {
        if (activeJobPhase === 'adopted') {
          return jsonResponse({
            jobs: [makeJob({ job_id: 'job-late-retained-500', status: 'running', current_stage: 'checking_files' })],
            total: 1,
            limit: 50,
            offset: 0,
          })
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-late-retained-500', status: 'pending' }), 202),
      onFetchJob: () => {
        fetchJobCalls += 1
        if (fetchJobCalls === 1) {
          // Hold the initial retained-job check open until after the adopted poll finishes.
          return new Promise<Response>((resolve) => {
            resolveLateRetainedCheck = resolve
          })
        }
        activeJobPhase = 'finished'
        return jsonResponse(
          makeJob({
            job_id: 'job-late-retained-500',
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        )
      },
    })

    const view = renderSection(
      { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 },
      client,
    )

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
    expect(await screen.findByText('Adding files')).toBeInTheDocument()

    // Close dialog before polling fires, then reopen with the server reporting the active job.
    activeJobPhase = 'adopted'
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open={false}
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )

    // Adopted job polls to completion, deletes the retained entry, refreshes listing, and enables Add.
    expect(await screen.findByRole('checkbox', { name: 'Made_up_S22' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Made_up_S20' })).not.toBeInTheDocument()

    // Now the earlier retained-job check arrives late with 500 -> must be ignored cleanly.
    resolveLateRetainedCheck(jsonResponse({ detail: 'Late retained check failure' }, 500))
    await new Promise((resolve) => setTimeout(resolve, 40))

    expect(screen.queryByText('Could not check progress for adding files.')).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Made_up_S22' })).toBeEnabled()
  })

  it('cancels an older in-flight viewer /files query before completion fetch so a late stale payload cannot overwrite the refreshed files list while the dialog stays open', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let paperFilesCalls = 0
    let firstCallSignal: AbortSignal | null | undefined
    let resolveInitialPaperFiles!: (response: Response) => void

    stubApi({
      paperFiles: (_url, init) => {
        paperFilesCalls += 1
        if (paperFilesCalls === 1) {
          firstCallSignal = init?.signal
          // Intentionally ignore signal and resolve later with the stale 4-file payload.
          return new Promise<Response>((resolve) => {
            resolveInitialPaperFiles = resolve
          })
        }
        return jsonResponse(UPDATED_FIVE_FILES_PAYLOAD)
      },
      onAddFiles: () => jsonResponse(makeJob({ job_id: 'job-cancel-stale-viewer', status: 'pending' }), 202),
      onFetchJob: () =>
        jsonResponse(
          makeJob({
            job_id: 'job-cancel-stale-viewer',
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        ),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 }, client)

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    // Completion cancels call 1 and writes the fresh 5-file list into the dialog while open.
    const filesList = await screen.findByRole('list', { name: 'Files in this paper' })
    await waitFor(() => {
      expect(within(filesList).getAllByRole('listitem')).toHaveLength(5)
    })
    expect(within(filesList).getByText('Made_up_S20.xlsx')).toBeInTheDocument()
    expect(firstCallSignal?.aborted).toBe(true)

    // Now the older call 1 resolves late with stale 4 files -> cancelled query must NOT overwrite cache.
    resolveInitialPaperFiles(jsonResponse(FOUR_FILES_PAYLOAD))
    await new Promise((resolve) => setTimeout(resolve, 40))

    expect(within(screen.getByRole('list', { name: 'Files in this paper' })).getAllByRole('listitem')).toHaveLength(5)
    expect(within(screen.getByRole('list', { name: 'Files in this paper' })).getByText('Made_up_S20.xlsx')).toBeInTheDocument()
    expect(client.getQueryData<ViewerFilesPayload>(paperFilesQueryKey(DOCUMENT_ID))?.files).toHaveLength(5)
  })

  it('preserves pending404Refresh across dialog close and reopen so reopening triggers the deferred /files and /source-files refresh once active jobs return empty', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))
    let activeJobCalls = 0
    let listingCalls = 0
    let paperFilesCalls = 0

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls <= 2 ? FOUR_FILES_PAYLOAD : UPDATED_FIVE_FILES_PAYLOAD)
      },
      listing: () => {
        listingCalls += 1
        if (listingCalls === 1) {
          return jsonResponse(
            listingWith([fileListingFixture.files[0], fileListingFixture.files[1], SECOND_TICKABLE_FILE]),
          )
        }
        return jsonResponse(
          listingWith([
            fileListingFixture.files[0],
            { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
            SECOND_TICKABLE_FILE,
          ]),
        )
      },
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 1) {
          return jsonResponse({
            jobs: [makeJob({ job_id: 'job-server-404-reopen', status: 'running', current_stage: 'checking_files' })],
            total: 1,
            limit: 50,
            offset: 0,
          })
        }
        if (activeJobCalls === 2) {
          return jsonResponse({ detail: 'Temporary active-jobs failure' }, 500)
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      onFetchJob: () => jsonResponse({ detail: 'Job not found' }, 404),
    })

    try {
      const view = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 },
        client,
      )

      // Server-adopted job hits 404 -> active-jobs refetch hits 500 -> retry warning shown.
      expect(await screen.findByText('Could not check whether this paper has an active import.')).toBeInTheDocument()
      expect(listingCalls).toBe(1)

      // Close and reopen the dialog instead of clicking "Try again" (5-minute /source-files cache is still warm!).
      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={DOCUMENT_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open={false}
            pollIntervalMs={20}
          />
        </QueryClientProvider>,
      )
      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={DOCUMENT_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open
            pollIntervalMs={20}
          />
        </QueryClientProvider>,
      )

      // Reopen sees empty active jobs and still triggers deferred refresh busting /source-files cache and refreshing viewer.
      await waitFor(() => expect(listingCalls).toBe(2))
      expect(await screen.findByRole('checkbox', { name: 'Made_up_S22' })).toBeInTheDocument()
      expect(screen.queryByRole('checkbox', { name: 'Made_up_S20' })).not.toBeInTheDocument()
      expect(await screen.findByText('Made_up_S20.xlsx')).toBeInTheDocument()
      await waitFor(() => expect(viewerEvents).toHaveLength(1))
      expect(viewerEvents[0]).toMatchObject({ documentId: DOCUMENT_ID, refreshOnlyIfOpen: true })
    } finally {
      unsubscribeViewer()
    }
  })

  it('marks pending404Refresh before awaiting the post-404 active-jobs lookup so closing mid-refetch and reopening still refreshes /files and /source-files', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))
    let activeJobCalls = 0
    let listingCalls = 0
    let paperFilesCalls = 0
    let resolveInFlightPost404ActiveJobs!: (response: Response) => void

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls <= 2 ? FOUR_FILES_PAYLOAD : UPDATED_FIVE_FILES_PAYLOAD)
      },
      listing: () => {
        listingCalls += 1
        if (listingCalls === 1) {
          return jsonResponse(
            listingWith([fileListingFixture.files[0], fileListingFixture.files[1], SECOND_TICKABLE_FILE]),
          )
        }
        return jsonResponse(
          listingWith([
            fileListingFixture.files[0],
            { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
            SECOND_TICKABLE_FILE,
          ]),
        )
      },
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 1) {
          return jsonResponse({
            jobs: [makeJob({ job_id: 'job-server-404-mid-refetch', status: 'running', current_stage: 'checking_files' })],
            total: 1,
            limit: 50,
            offset: 0,
          })
        }
        if (activeJobCalls === 2) {
          // Keep the post-404 active-jobs refetch in flight while the dialog closes.
          return new Promise<Response>((resolve) => {
            resolveInFlightPost404ActiveJobs = resolve
          })
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
      onFetchJob: () => jsonResponse({ detail: 'Job not found' }, 404),
    })

    try {
      const view = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 20 },
        client,
      )

      // Poll hits 404 and kicks off active-jobs call 2, which stays pending.
      await waitFor(() => expect(activeJobCalls).toBe(2))
      expect(listingCalls).toBe(1)

      // Close the dialog while call 2 is STILL pending, then resolve call 2 and reopen.
      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={DOCUMENT_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open={false}
            pollIntervalMs={20}
          />
        </QueryClientProvider>,
      )
      resolveInFlightPost404ActiveJobs(jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 }))

      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={DOCUMENT_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open
            pollIntervalMs={20}
          />
        </QueryClientProvider>,
      )

      // Reopen sees empty active jobs and still triggers deferred refresh busting /source-files cache and refreshing viewer.
      await waitFor(() => expect(listingCalls).toBe(2))
      expect(await screen.findByRole('checkbox', { name: 'Made_up_S22' })).toBeInTheDocument()
      expect(screen.queryByRole('checkbox', { name: 'Made_up_S20' })).not.toBeInTheDocument()
      expect(await screen.findByText('Made_up_S20.xlsx')).toBeInTheDocument()
      await waitFor(() => expect(viewerEvents).toHaveLength(1))
      expect(viewerEvents[0]).toMatchObject({ documentId: DOCUMENT_ID, refreshOnlyIfOpen: true })
    } finally {
      unsubscribeViewer()
    }
  })

  it('shows Card 34 on an uploaded paper without asking the provider listing, using configured short label (Alliance & non-ABC) and hides when import disabled', async () => {
    const fetchMock = stubApi({
      sourceProvider: {
        provider: 'abc_literature',
        import_enabled: true,
        presentation: { display_label: 'ABC Literature', short_label: 'ABC' },
      },
    })
    const first = renderSection({ providerLabel: null })

    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(await screen.findByText('Files can be added only to papers imported from ABC.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContain(SOURCE_FILES_URL)
    expect(screen.queryByText(/Also in/)).not.toBeInTheDocument()
    first.unmount()

    // Non-ABC deployment configured provider label (zero hardcoded ABC).
    stubApi({
      sourceProvider: {
        provider: 'genome_archive',
        import_enabled: true,
        presentation: { display_label: 'Genome Archive', short_label: 'GA' },
      },
    })
    const second = renderSection({ providerLabel: null })
    expect(await screen.findByText('Files can be added only to papers imported from GA.')).toBeInTheDocument()
    expect(screen.queryByText(/ABC/)).not.toBeInTheDocument()
    second.unmount()

    // When external import is disabled on this server, nothing is shown for uploaded papers.
    stubApi({
      sourceProvider: {
        provider: null,
        import_enabled: false,
        presentation: null,
      },
    })
    renderSection({ providerLabel: null })
    await screen.findByRole('list', { name: 'Files in this paper' })
    await waitFor(() => {
      expect(screen.queryByText(/Files can be added only/)).not.toBeInTheDocument()
    })
  })

  it('shows Card 34 with configured short provider label when source-files listing returns not_imported_from_provider', async () => {
    stubApi({
      listing: { status: 409, payload: { detail: { error: 'not_imported_from_provider' } } },
    })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText('Files can be added only to papers imported from ABC.')).toBeInTheDocument()
  })

  it.each([
    {
      name: '422 paper_file_limit with limit_value (Card 41)',
      status: 422,
      payload: { detail: { error: 'paper_file_limit', limit_value: 20 } },
      expectedText: 'Too many files for one paper: at most 20, including the main paper.',
      expectTryAgain: false,
    },
    {
      name: '409 import_in_progress (N35 while follow-up active-job lookup sees active import)',
      status: 409,
      payload: { detail: { error: 'import_in_progress' } },
      expectedText: "This paper's import is still running. You can add or remove files when it has finished.",
      expectTryAgain: false,
      activeJobAfterPost: makeJob({ job_id: 'job-conflict-running', status: 'running', metadata: {} }),
    },
    {
      name: '409 file_removal_unfinished (N36)',
      status: 409,
      payload: { detail: { error: 'file_removal_unfinished' } },
      expectedText: 'A file of this paper is still being removed. Try again in a moment.',
      expectTryAgain: true,
    },
    {
      name: '409 document_source_curator_token_unavailable (Card 9 sign-in)',
      status: 409,
      payload: { detail: { error: 'document_source_curator_token_unavailable' } },
      expectedText: "Sign in again to see this paper's files in ABC Literature.",
      expectTryAgain: false,
    },
    {
      name: '503 document_source_curator_token_unavailable (Card 9 reader down)',
      status: 503,
      payload: { detail: { error: 'document_source_curator_token_unavailable' } },
      expectedText: "This server cannot reach ABC Literature just now, so this paper's files there cannot be listed.",
      expectTryAgain: true,
    },
    {
      name: '500 generic failure with Card 38 Try again',
      status: 500,
      payload: { detail: 'Internal error' },
      expectedText: 'Could not add files to this paper.',
      expectTryAgain: true,
    },
  ])('renders approved refusal text for $name', async ({ status, payload, expectedText, expectTryAgain, activeJobAfterPost }) => {
    const user = userEvent.setup()
    let attempts = 0
    let postedOnce = false
    stubApi({
      activeJobs: () => {
        const jobs = postedOnce && activeJobAfterPost ? [activeJobAfterPost] : []
        return jsonResponse({ jobs, total: jobs.length, limit: 50, offset: 0 })
      },
      onAddFiles: () => {
        attempts += 1
        postedOnce = true
        return jsonResponse(payload, status)
      },
      onFetchJob: () => jsonResponse(activeJobAfterPost ?? makeJob({ status: 'running' })),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, pollIntervalMs: 500 })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    expect(await screen.findByText(expectedText)).toBeInTheDocument()
    expect(attempts).toBe(1)

    if (expectTryAgain) {
      const tryAgain = screen.getByRole('button', { name: 'Try again' })
      await user.click(tryAgain)
      await waitFor(() => expect(attempts).toBe(2))
    } else {
      expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    }
  })

  it('hides the provider add UI cleanly when POST /files returns 503 document_source_import_disabled', async () => {
    const user = userEvent.setup()
    stubApi({
      onAddFiles: () => jsonResponse({ detail: { error: 'document_source_import_disabled' } }, 503),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    await waitFor(() => {
      expect(screen.queryByText('Also in ABC Literature')).not.toBeInTheDocument()
    })
    expect(screen.queryByText(/document_source_import_disabled/)).not.toBeInTheDocument()
    expect(screen.queryByText('This screen cannot describe this file yet.')).not.toBeInTheDocument()
  })

  it('cancels polling timers and in-flight poll requests cleanly on unmount', async () => {
    const user = userEvent.setup()
    let pollCalls = 0
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')

    stubApi({
      onAddFiles: () => jsonResponse(makeJob({ status: 'pending' }), 202),
      onFetchJob: () => {
        pollCalls += 1
        return jsonResponse(makeJob({ status: 'running', current_stage: 'checking_files' }))
      },
    })

    try {
      const view = renderSection({
        providerLabel: PROVIDER,
        providerShortLabel: PROVIDER_SHORT,
        pollIntervalMs: 30,
      })

      await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }))
      await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

      await waitFor(() => expect(pollCalls).toBe(1))

      view.unmount()
      const callsAtUnmount = pollCalls
      await new Promise((resolve) => setTimeout(resolve, 120))

      expect(pollCalls).toBe(callsAtUnmount)
      expect(clearTimeoutSpy).toHaveBeenCalled()
    } finally {
      clearTimeoutSpy.mockRestore()
    }
  })

  it('renders every label, progress entry, and refusal with a non-ABC provider label and zero ABC text', async () => {
    const user = userEvent.setup()
    stubApi({
      onAddFiles: () => jsonResponse(makeJob({ status: 'pending' }), 202),
      onFetchJob: () =>
        jsonResponse(
          makeJob({
            status: 'completed',
            message: 'Added 1 of 1 files.',
            metadata: supplementAddMetadataFixture,
          }),
        ),
    })

    const { container } = renderSection({
      providerLabel: 'Genome Archive',
      providerShortLabel: 'GA',
    })

    expect(await screen.findByText('Also in Genome Archive')).toBeInTheDocument()
    expect(screen.getByText('Converted by Genome Archive; not added to this paper')).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Made_up_S20' }))
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))

    expect(await screen.findByText('Not added: GA no longer lists this file for the paper.')).toBeInTheDocument()
    expect(screen.getByText("You can read this file's text in GA but not its PDF, so it was not added.")).toBeInTheDocument()
    expect(container.textContent).not.toContain('ABC')
  })

  it('says why the provider\'s files cannot be listed when the paper\'s entry was merged', async () => {
    stubApi({ listing: { status: 409, payload: { detail: { error: 'paper_reference_mismatch' } } } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText(
      'This paper\'s entry in ABC Literature has been merged into another entry, so its files cannot be listed here.',
    )).toBeInTheDocument()
  })

  it('says plainly when the provider could not be checked and offers Try again', async () => {
    const user = userEvent.setup()
    let listingCalls = 0
    stubApi({
      listing: () => {
        listingCalls += 1
        return jsonResponse({ detail: { error: 'file_listing_failed' } }, 500)
      },
    })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText('Could not check ABC Literature for this paper\'s files.')).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Files in this paper' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(listingCalls).toBe(2))
  })

  it('shows a file that could not be added as such, in the files route\'s real shape', async () => {
    stubApi({ paperFiles: FAILED_SUPPLEMENT_PAYLOAD })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    const list = await screen.findByRole('list', { name: 'Files in this paper' })
    expect(within(list).getAllByRole('listitem')[4]).toHaveTextContent('Table_S4.xlsxSupplementTextCould not be added')
  })

  it('words each state of a paper file differently', async () => {
    stubApi({ paperFiles: FAILED_SUPPLEMENT_PAYLOAD })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    const list = await screen.findByRole('list', { name: 'Files in this paper' })
    const states = within(list)
      .getAllByRole('listitem')
      .map((row) => row.querySelectorAll('.MuiChip-label')[2]?.textContent)
    expect(states).toEqual(['Ready', 'Ready', 'Ready', 'Being added', 'Could not be added'])
  })

  it('words the main paper by what is true, never as a converted supplement', async () => {
    const main = {
      ...fileListingFixture.files[0],
      in_this_paper: false,
      tickable: false,
      not_tickable_reason: 'main_differs_from_paper',
      converted_text_id: null,
      converted_md5: null,
    }
    stubApi({ listing: { status: 200, payload: listingWith([main]) } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    const list = await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    const row = within(list).getByRole('listitem')
    expect(row).toHaveTextContent('Main paper')
    expect(row).toHaveTextContent('ABC Literature now has a different main file for this paper than the one in the Library')
    expect(row).not.toHaveTextContent(/Converted by/)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('treats retired pdf_supplement_not_supported_yet on a listing as unrecognized (ALL-1738 item 1)', async () => {
    const pdf = {
      ...fileListingFixture.files[1],
      format: 'pdf',
      tickable: false,
      not_tickable_reason: 'pdf_supplement_not_supported_yet',
    }
    stubApi({ listing: { status: 200, payload: listingWith([pdf]) } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    const list = await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    const row = within(list).getByRole('listitem')
    expect(row).toHaveTextContent('This screen cannot describe this file yet.')
    expect(row).not.toHaveTextContent('PDF supplements cannot be added yet.')
    expect(row).not.toHaveTextContent(/Converted by/)
  })

  it('refuses a tickable main file as a contract error and keeps paper files intact', async () => {
    const main = {
      ...fileListingFixture.files[0],
      in_this_paper: false,
      tickable: true,
      not_tickable_reason: null,
      converted_text_id: null,
      converted_md5: null,
    }
    stubApi({ listing: { status: 200, payload: listingWith([main, fileListingFixture.files[1]]) } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText('This paper\'s files in ABC Literature could not be shown.')).toBeInTheDocument()
    expect(screen.queryByText(/Converted by/)).not.toBeInTheDocument()
    expect(screen.queryByText('Made_up_S20')).not.toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Files in this paper' })).toBeInTheDocument()
  })

  it('words a file with an unknown code on its own line and still shows the rest', async () => {
    const unknown = { ...fileListingFixture.files[4], not_tickable_reason: 'pdf_supplement_later' }
    stubApi({ listing: { status: 200, payload: listingWith([fileListingFixture.files[1], unknown]) } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    const list = await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('Converted by ABC Literature; not added to this paper')
    expect(rows[1]).toHaveTextContent('Made_up_S50')
    expect(rows[1]).toHaveTextContent('This screen cannot describe this file yet.')
    expect(screen.queryByText(/pdf_supplement_later/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Could not check/)).not.toBeInTheDocument()
  })

  it('does not blame the provider when its answer cannot be read', async () => {
    stubApi({ listing: { status: 200, payload: { ...fileListingFixture, files: 'not a list' } } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText('This paper\'s files in ABC Literature could not be shown.')).toBeInTheDocument()
    expect(screen.queryByText(/Could not check/)).not.toBeInTheDocument()
  })

  it('keeps the dialog whole when a file is neither tickable nor gives a reason', async () => {
    stubApi({
      listing: {
        status: 200,
        payload: listingWith([{ ...fileListingFixture.files[4], not_tickable_reason: null }]),
      },
    })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText('This paper\'s files in ABC Literature could not be shown.')).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Files in this paper' })).toBeInTheDocument()
  })

  it('asks to sign in again only when the sign-in is missing', async () => {
    stubApi({ listing: { status: 409, payload: { detail: { error: 'document_source_curator_token_unavailable' } } } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText('Sign in again to see this paper\'s files in ABC Literature.')).toBeInTheDocument()
  })

  it('says the provider cannot be reached when its reader is down, not to sign in', async () => {
    stubApi({ listing: { status: 503, payload: { detail: { error: 'document_source_curator_token_unavailable' } } } })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    expect(await screen.findByText('This server cannot reach ABC Literature just now, so this paper\'s files there cannot be listed.')).toBeInTheDocument()
    expect(screen.queryByText(/Sign in again/)).not.toBeInTheDocument()
  })

  it('does not ask the provider again when the same paper is reopened soon after', async () => {
    const fetchMock = stubApi()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const first = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT }, client)
    await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    first.unmount()

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT }, client)
    await screen.findByRole('list', { name: 'Files in ABC Literature not in this paper' })
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([url]) => String(url) === PAPER_FILES_URL)).toHaveLength(2)
    })
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === SOURCE_FILES_URL)).toHaveLength(1)
  })
})

describe('DocumentFilesSection (Library Remove)', () => {
  afterEach(() => {
    clearPendingAddJobIds()
    vi.unstubAllGlobals()
  })

  const THREE_FILES_AFTER_REMOVAL: ViewerFilesPayload = {
    document_id: DOCUMENT_ID,
    files: FOUR_FILES_PAYLOAD.files.filter((file) => file.file_id !== TEXT_SUPPLEMENT_FILE_ID),
  }

  function removalCheckFor(
    fixtureKey: keyof typeof removalCheckFixture,
    overrides: Record<string, unknown> = {},
  ) {
    return {
      ...removalCheckFixture[fixtureKey],
      document_id: DOCUMENT_ID,
      file_id: TEXT_SUPPLEMENT_FILE_ID,
      ...overrides,
    }
  }

  it('shows Remove only on non-pending supplements when another ready file exists, never on main or a paper\'s only file', async () => {
    // Standard multi-file paper: main + 2 ready supplements + 1 pending supplement.
    stubApi()
    const multiView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(screen.queryByRole('button', { name: 'Remove made-up-paper.pdf' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove Table_S3.docx' })).not.toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Remove Table_S1.xlsx' })).toBeEnabled()
      expect(screen.getByRole('button', { name: 'Remove Figure_S2.pdf' })).toBeEnabled()
    })
    multiView.unmount()

    // Single main file only: zero Remove buttons.
    stubApi({
      paperFiles: { document_id: DOCUMENT_ID, files: [singleMainPdfFile()] },
    })
    const mainOnlyView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
    mainOnlyView.unmount()

    // Supplements-only paper with a single ready supplement: zero Remove buttons.
    stubApi({ paperFiles: SUPPLEMENTS_ONLY_PAYLOAD })
    const singleSuppView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
    singleSuppView.unmount()

    // 1 failed main + 1 ready supplement (only 1 ready file total): zero Remove buttons.
    stubApi({
      paperFiles: {
        document_id: DOCUMENT_ID,
        files: [
          { ...FOUR_FILES_PAYLOAD.files[0]!, status: 'failed', page_count: null, content_url: null },
          FOUR_FILES_PAYLOAD.files[1]!,
        ],
      },
    })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
  })

  it('disables Remove while active jobs are loading, running, or errored, while paper is not completed, or while an add is in flight, and blocks Add during removal', async () => {
    const user = userEvent.setup()

    // 1) Active job lookup still pending -> Remove fails closed (disabled) until settled empty.
    let resolveActiveJobs!: (response: Response) => void
    stubApi({
      activeJobs: () => new Promise<Response>((resolve) => {
        resolveActiveJobs = resolve
      }),
    })
    const loadingView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const pendingRemoveButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    expect(pendingRemoveButton).toBeDisabled()
    resolveActiveJobs(jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 }))
    await waitFor(() => expect(pendingRemoveButton).toBeEnabled())
    loadingView.unmount()

    // 2) Active job running on paper -> Remove disabled.
    const runningJob = makeJob({ status: 'running', current_stage: WAITING_FOR_SUPPLEMENTS_STAGE })
    stubApi({
      activeJobs: [runningJob],
      onFetchJob: () => jsonResponse(runningJob),
    })
    const activeJobView = renderSection({
      providerLabel: PROVIDER,
      providerShortLabel: PROVIDER_SHORT,
      pollIntervalMs: 500,
    })
    const activeJobRemoveButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await screen.findByText('Adding files')
    expect(activeJobRemoveButton).toBeDisabled()
    activeJobView.unmount()

    // 3) Active job lookup failed -> Remove fails closed (disabled) until Try again succeeds.
    let activeJobsAttempts = 0
    stubApi({
      activeJobs: () => {
        activeJobsAttempts += 1
        if (activeJobsAttempts === 1) {
          return jsonResponse({ detail: 'Jobs service unavailable' }, 500)
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
    })
    const errorView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const erroredRemoveButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    expect(await screen.findByText('Could not check whether this paper has an active import.')).toBeInTheDocument()
    expect(erroredRemoveButton).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(erroredRemoveButton).toBeEnabled())
    errorView.unmount()

    // 4) Non-completed paper -> Remove disabled.
    stubApi()
    const nonCompletedView = renderSection({
      providerLabel: PROVIDER,
      providerShortLabel: PROVIDER_SHORT,
      processingStatus: 'processing',
    })
    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(screen.getByRole('button', { name: 'Remove Table_S1.xlsx' })).toBeDisabled()
    nonCompletedView.unmount()

    // 5) Mutual exclusion: in-flight removal pre-check disables Add checkboxes and Add button.
    let resolvePrecheck!: (response: Response) => void
    stubApi({
      onRemovalCheck: () => new Promise<Response>((resolve) => {
        resolvePrecheck = resolve
      }),
    })
    const mutualView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const checkbox = await screen.findByRole('checkbox', { name: 'Made_up_S20' })
    await user.click(checkbox)
    const addButton = screen.getByRole('button', { name: 'Add 1 file' })
    expect(addButton).toBeEnabled()
    const removeButton = screen.getByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(removeButton).toBeEnabled())
    await user.click(removeButton)

    expect(checkbox).toBeDisabled()
    expect(addButton).toBeDisabled()
    resolvePrecheck(jsonResponse(removalCheckFor('removable_plain')))
    expect(await screen.findByRole('button', { name: 'Remove file' })).toBeInTheDocument()
    mutualView.unmount()

    // 6) Mutual exclusion: in-flight Add POST disables Remove immediately.
    let resolveAdd!: (response: Response) => void
    stubApi({
      onAddFiles: () => new Promise<Response>((resolve) => {
        resolveAdd = resolve
      }),
    })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const addCheckbox = await screen.findByRole('checkbox', { name: 'Made_up_S20' })
    const removeDuringAdd = screen.getByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(removeDuringAdd).toBeEnabled())
    await user.click(addCheckbox)
    await user.click(screen.getByRole('button', { name: 'Add 1 file' }))
    expect(removeDuringAdd).toBeDisabled()
    resolveAdd(jsonResponse(makeJob({ status: 'completed' }), 202))
  })

  it('protects Remove with active-job checks on non-provider papers that still have removable supplements', async () => {
    const user = userEvent.setup()
    let activeJobCalls = 0
    stubApi({
      activeJobs: () => {
        activeJobCalls += 1
        if (activeJobCalls === 1) {
          return jsonResponse({ detail: 'Temporary active-jobs failure' }, 500)
        }
        return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
      },
    })

    renderSection({ providerLabel: null })

    const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    expect(await screen.findByText('Could not check whether this paper has an active import.')).toBeInTheDocument()
    expect(removeButton).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(removeButton).toBeEnabled())
  })

  it('shows confirmation dialog with and without N40 warning based on pre-check response', async () => {
    const user = userEvent.setup()

    // Case 1: removable_plain -> N39 body, NO N40 warning.
    stubApi({
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
    })
    const plainView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const plainRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(plainRemove).toBeEnabled())
    await user.click(plainRemove)

    const plainDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(
      within(plainDialog).getByText(
        'AI Curation will no longer read this file or show it in the viewer. Evidence already saved stays as it is.',
      ),
    ).toBeInTheDocument()
    expect(
      within(plainDialog).queryByText(
        "This paper's newest result quotes this file, so Review & Curate will ask you to run the extraction again.",
      ),
    ).not.toBeInTheDocument()
    expect(within(plainDialog).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(within(plainDialog).getByRole('button', { name: 'Remove file' })).toBeInTheDocument()
    plainView.unmount()

    // Case 2: removable_with_review_warning -> shows N40 warning.
    stubApi({
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_with_review_warning')),
    })
    const warningView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const warningRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(warningRemove).toBeEnabled())
    await user.click(warningRemove)

    const warningDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(
      within(warningDialog).getByText(
        "This paper's newest result quotes this file, so Review & Curate will ask you to run the extraction again.",
      ),
    ).toBeInTheDocument()
    warningView.unmount()

    // Case 3: newest_result_quotes_file=true but paper already has a review session -> review_and_curate_would_refuse=false -> NO N40 warning.
    stubApi({
      onRemovalCheck: () =>
        jsonResponse(
          removalCheckFor('removable_with_review_warning', {
            has_review_session: true,
            review_and_curate_would_refuse: false,
          }),
        ),
    })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const sessionRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(sessionRemove).toBeEnabled())
    await user.click(sessionRemove)

    const sessionDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(within(sessionDialog).getByRole('button', { name: 'Remove file' })).toBeInTheDocument()
    expect(
      within(sessionDialog).queryByText(
        "This paper's newest result quotes this file, so Review & Curate will ask you to run the extraction again.",
      ),
    ).not.toBeInTheDocument()
  })

  it.each([
    {
      name: 'single review session (Card 2 W-D1, count only, no session names)',
      payload: () => removalCheckFor('refused_file_quoted_in_review_single'),
      expectedText: 'This file is quoted in 1 review session. To remove it, delete the paper.',
      forbiddenTokens: ['session-1'],
    },
    {
      name: 'multiple review sessions (Card 2 W-D1, count only, no session names)',
      payload: () => removalCheckFor('refused_file_quoted_in_review_multiple'),
      expectedText: 'This file is quoted in 2 review sessions. To remove it, delete the paper.',
      forbiddenTokens: ['session-1', 'session-2'],
    },
    {
      name: 'main_file_not_removable',
      payload: () => removalCheckFor('refused_main_file'),
      expectedText: 'The main paper cannot be removed. To remove it, delete the paper.',
      forbiddenTokens: ['main_file_not_removable'],
    },
    {
      name: 'last_file_not_removable',
      payload: () => removalCheckFor('refused_last_file'),
      expectedText: "A paper's only file cannot be removed. To remove it, delete the paper.",
      forbiddenTokens: ['last_file_not_removable'],
    },
    {
      name: 'import_in_progress',
      payload: () => removalCheckFor('refused_import_in_progress'),
      expectedText: "This paper's import is still running. You can add or remove files when it has finished.",
      forbiddenTokens: ['import_in_progress'],
    },
    {
      name: 'paper_not_completed',
      payload: () => removalCheckFor('refused_paper_not_completed'),
      expectedText: 'Files can be removed once the paper has finished processing.',
      forbiddenTokens: ['paper_not_completed'],
    },
    {
      name: 'file_not_in_paper',
      payload: () => removalCheckFor('refused_file_not_in_paper'),
      expectedText: 'This file is no longer in the paper.',
      forbiddenTokens: ['file_not_in_paper'],
    },
    {
      name: 'unknown future refusal code -> N29 fallback',
      payload: () => removalCheckFor('refused_main_file', { error: 'future_unrecognized_refusal' }),
      expectedText: 'This screen cannot describe this file yet.',
      forbiddenTokens: ['future_unrecognized_refusal'],
    },
  ])('renders pre-check refusal for $name with Close button and NO confirm button', async ({ payload, expectedText, forbiddenTokens }) => {
    const user = userEvent.setup()
    stubApi({
      onRemovalCheck: () => jsonResponse(payload()),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(removeButton).toBeEnabled())
    await user.click(removeButton)

    const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(await within(dialog).findByText(expectedText)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Remove file' })).not.toBeInTheDocument()
    for (const token of forbiddenTokens) {
      expect(dialog.textContent).not.toContain(token)
    }

    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('shows Card 38 Try again on pre-check network, 409 busy, and 503 busy failures without leaking raw server text, and aborts cleanly on cancel', async () => {
    const user = userEvent.setup()
    let attempts = 0
    const signals: Array<AbortSignal | null | undefined> = []

    stubApi({
      onRemovalCheck: (_fileId, init) => {
        attempts += 1
        signals.push(init?.signal)
        if (attempts === 1) {
          return jsonResponse({ detail: 'Raw internal 409 lock string' }, 409)
        }
        if (attempts === 2) {
          return jsonResponse({ detail: 'Raw internal 503 pool string' }, 503)
        }
        if (attempts === 3) {
          return Promise.reject(new TypeError('Failed to fetch'))
        }
        return jsonResponse(removalCheckFor('removable_plain'))
      },
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(removeButton).toBeEnabled())
    await user.click(removeButton)

    const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(
      await within(dialog).findByText('Another operation on this paper is in progress; try again in a moment.'),
    ).toBeInTheDocument()
    expect(dialog.textContent).not.toContain('Raw internal 409 lock string')

    await user.click(within(dialog).getByRole('button', { name: 'Try again' }))
    expect(
      await within(dialog).findByText('The server is busy right now; try removing the file again in a moment.'),
    ).toBeInTheDocument()
    expect(dialog.textContent).not.toContain('Raw internal 503 pool string')

    await user.click(within(dialog).getByRole('button', { name: 'Try again' }))
    expect(
      await within(dialog).findByText('Could not check whether this file can be removed.'),
    ).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Try again' }))
    expect(await within(dialog).findByRole('button', { name: 'Remove file' })).toBeInTheDocument()
    expect(attempts).toBe(4)

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // Cancelling while pre-check is still in flight aborts its AbortSignal cleanly.
    let cancelSignal: AbortSignal | null | undefined
    stubApi({
      onRemovalCheck: (_fileId, init) => {
        cancelSignal = init?.signal
        return new Promise<Response>(() => undefined)
      },
    })
    const cancelView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const removeButtons = await screen.findAllByRole('button', { name: 'Remove Table_S1.xlsx' })
    const secondRemove = removeButtons[removeButtons.length - 1]!
    await waitFor(() => expect(secondRemove).toBeEnabled())
    await user.click(secondRemove)
    const checkingDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(cancelSignal?.aborted).toBe(false)
    await user.click(within(checkingDialog).getByRole('button', { name: 'Cancel' }))
    expect(cancelSignal?.aborted).toBe(true)
    cancelView.unmount()
  })

  it('removes a supplement on confirmation and refreshes attached files, source listing, Library documents row, and same-tab + cross-tab open viewer', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const onFilesChanged = vi.fn()
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))
    const broadcastMessages: Array<{ channel: string; data: unknown }> = []
    const originalBroadcastChannel = globalThis.BroadcastChannel

    class MockBroadcastChannel {
      name: string
      constructor(name: string) {
        this.name = name
      }
      postMessage(data: unknown) {
        broadcastMessages.push({ channel: this.name, data })
      }
      close() {}
      addEventListener() {}
      removeEventListener() {}
    }

    vi.stubGlobal('BroadcastChannel', MockBroadcastChannel)
    client.setQueryData(['documents'], { documents: [{ id: DOCUMENT_ID, ready_file_count: 3 }] })
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    let paperFilesCalls = 0
    let listingCalls = 0
    let deletedFileId: string | null = null

    const listingBeforeRemoval = listingWith([
      fileListingFixture.files[0],
      { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
    ])
    const listingAfterRemoval = listingWith([
      fileListingFixture.files[0],
      fileListingFixture.files[1],
    ])

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : THREE_FILES_AFTER_REMOVAL)
      },
      listing: () => {
        listingCalls += 1
        return jsonResponse(listingCalls === 1 ? listingBeforeRemoval : listingAfterRemoval)
      },
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: (fileId) => {
        deletedFileId = fileId
        return jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: fileId,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 2,
        })
      },
    })

    try {
      renderSection(
        {
          providerLabel: PROVIDER,
          providerShortLabel: PROVIDER_SHORT,
          onFilesChanged,
        },
        client,
      )

      const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(removeButton).toBeEnabled())
      await user.click(removeButton)

      const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      })
      expect(deletedFileId).toBe(TEXT_SUPPLEMENT_FILE_ID)

      await waitFor(() => {
        expect(paperFilesCalls).toBe(2)
        expect(listingCalls).toBe(2)
        expect(onFilesChanged).toHaveBeenCalledTimes(1)
        expect(viewerEvents).toHaveLength(1)
      })

      // 1) Attached files list removed Table_S1.xlsx and source listing re-offers Made_up_S20.
      const filesList = screen.getByRole('list', { name: 'Files in this paper' })
      expect(within(filesList).queryByText('Table_S1.xlsx')).not.toBeInTheDocument()
      expect(within(filesList).getByText('Figure_S2.pdf')).toBeInTheDocument()
      expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()

      // 2) Library documents query invalidated alongside onFilesChanged callback.
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['documents'] })

      // 3) Open viewer refreshed with refreshOnlyIfOpen: true (same-tab) + cross-tab BroadcastChannel.
      expect(viewerEvents[0]).toMatchObject({
        documentId: DOCUMENT_ID,
        refreshOnlyIfOpen: true,
      })
      expect(viewerEvents[0]!.files.map((file) => file.file_id)).toEqual([
        MAIN_FILE_ID,
        PDF_SUPPLEMENT_FILE_ID,
      ])
      expect(broadcastMessages).toContainEqual(
        expect.objectContaining({
          channel: PAPER_FILES_REFRESH_CHANNEL,
          data: expect.objectContaining({ documentId: DOCUMENT_ID }),
        }),
      )
    } finally {
      unsubscribeViewer()
      invalidateSpy.mockRestore()
      vi.stubGlobal('BroadcastChannel', originalBroadcastChannel)
    }
  })

  it('falls back to localStorage storage refresh signal when BroadcastChannel is unavailable and shows NOTHING extra when cleanup_finished is false (Card 3 W-D2)', async () => {
    const user = userEvent.setup()
    const onFilesChanged = vi.fn()
    const originalBroadcastChannel = globalThis.BroadcastChannel
    vi.stubGlobal('BroadcastChannel', undefined)
    window.localStorage.removeItem(PAPER_FILES_REFRESH_STORAGE_KEY)

    let paperFilesCalls = 0
    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : THREE_FILES_AFTER_REMOVAL)
      },
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: (fileId) =>
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: fileId,
          removed: true,
          cleanup_finished: false,
          ready_file_count: 2,
        }),
    })

    try {
      const { container } = renderSection({
        providerLabel: PROVIDER,
        providerShortLabel: PROVIDER_SHORT,
        onFilesChanged,
      })

      const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(removeButton).toBeEnabled())
      await user.click(removeButton)

      const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        expect(paperFilesCalls).toBe(2)
        expect(onFilesChanged).toHaveBeenCalledTimes(1)
      })

      // Card 3 (W-D2): ZERO extra alerts, warnings, or cleanup copy in UI when cleanup_finished is false.
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(container.textContent).not.toMatch(/clean|clearing|unfinished|stored copies/i)

      // Cross-tab localStorage fallback signal published for this document.
      const storedSignal = window.localStorage.getItem(PAPER_FILES_REFRESH_STORAGE_KEY)
      expect(storedSignal).not.toBeNull()
      expect(JSON.parse(storedSignal!)).toMatchObject({ documentId: DOCUMENT_ID })
    } finally {
      window.localStorage.removeItem(PAPER_FILES_REFRESH_STORAGE_KEY)
      vi.stubGlobal('BroadcastChannel', originalBroadcastChannel)
    }
  })

  it('handles DELETE refusals strictly by code/status: 409 file_quoted_in_review, 409/503 busy with Try again, 404 file_not_in_paper with refresh, and unknown code', async () => {
    const user = userEvent.setup()

    // 1) DELETE 409 file_quoted_in_review (multiple sessions, zero session names) -> Close only, NO confirm button.
    stubApi({
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: () =>
        jsonResponse(
          { detail: { error: 'file_quoted_in_review', session_ids: ['secret-session-a', 'secret-session-b'] } },
          409,
        ),
    })
    const quotedView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const quotedRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(quotedRemove).toBeEnabled())
    await user.click(quotedRemove)
    const quotedDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    await user.click(within(quotedDialog).getByRole('button', { name: 'Remove file' }))

    expect(
      await within(quotedDialog).findByText(
        'This file is quoted in 2 review sessions. To remove it, delete the paper.',
      ),
    ).toBeInTheDocument()
    expect(quotedDialog.textContent).not.toContain('secret-session-a')
    expect(quotedDialog.textContent).not.toContain('secret-session-b')
    expect(within(quotedDialog).getByRole('button', { name: 'Close' })).toBeInTheDocument()
    expect(within(quotedDialog).queryByRole('button', { name: 'Remove file' })).not.toBeInTheDocument()
    quotedView.unmount()

    // 2) DELETE 409 busy -> 503 busy -> Try again succeeds (mapping strictly by status, never raw detail string).
    let deleteAttempts = 0
    stubApi({
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: (fileId) => {
        deleteAttempts += 1
        if (deleteAttempts === 1) {
          return jsonResponse({ detail: 'Raw lock error string must not render' }, 409)
        }
        if (deleteAttempts === 2) {
          return jsonResponse({ detail: 'Raw pool timeout string must not render' }, 503)
        }
        return jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: fileId,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 2,
        })
      },
    })
    const busyView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const busyRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(busyRemove).toBeEnabled())
    await user.click(busyRemove)
    const busyDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    await user.click(within(busyDialog).getByRole('button', { name: 'Remove file' }))

    expect(
      await within(busyDialog).findByText('Another operation on this paper is in progress; try again in a moment.'),
    ).toBeInTheDocument()
    expect(busyDialog.textContent).not.toContain('Raw lock error string')

    await user.click(within(busyDialog).getByRole('button', { name: 'Try again' }))
    expect(
      await within(busyDialog).findByText('The server is busy right now; try removing the file again in a moment.'),
    ).toBeInTheDocument()
    expect(busyDialog.textContent).not.toContain('Raw pool timeout string')

    await user.click(within(busyDialog).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(deleteAttempts).toBe(3)
    busyView.unmount()

    // 3) DELETE 404 file_not_in_paper -> shows N44 refusal AND refreshes files/source/parent/viewer because file is already gone!
    let filesCallsFor404 = 0
    let listingCallsFor404 = 0
    const onFilesChangedFor404 = vi.fn()
    const viewerEventsFor404: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewerFor404 = onPDFDocumentChanged((event) => viewerEventsFor404.push(event.detail))
    stubApi({
      paperFiles: () => {
        filesCallsFor404 += 1
        return jsonResponse(filesCallsFor404 === 1 ? FOUR_FILES_PAYLOAD : THREE_FILES_AFTER_REMOVAL)
      },
      listing: () => {
        listingCallsFor404 += 1
        return jsonResponse(fileListingFixture)
      },
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: () => jsonResponse({ detail: { error: 'file_not_in_paper' } }, 404),
    })
    const missingView = renderSection({
      providerLabel: PROVIDER,
      providerShortLabel: PROVIDER_SHORT,
      onFilesChanged: onFilesChangedFor404,
    })
    try {
      const missingRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(missingRemove).toBeEnabled())
      await user.click(missingRemove)
      const missingDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(missingDialog).getByRole('button', { name: 'Remove file' }))

      expect(await within(missingDialog).findByText('This file is no longer in the paper.')).toBeInTheDocument()
      expect(within(missingDialog).getByRole('button', { name: 'Close' })).toBeInTheDocument()
      await waitFor(() => {
        expect(filesCallsFor404).toBe(2)
        expect(listingCallsFor404).toBe(2)
        expect(onFilesChangedFor404).toHaveBeenCalledTimes(1)
        expect(viewerEventsFor404).toHaveLength(1)
      })
      expect(viewerEventsFor404[0]).toMatchObject({
        documentId: DOCUMENT_ID,
        refreshOnlyIfOpen: true,
      })
      expect(viewerEventsFor404[0]!.files.map((file) => file.file_id)).toEqual([
        MAIN_FILE_ID,
        PDF_SUPPLEMENT_FILE_ID,
      ])
    } finally {
      unsubscribeViewerFor404()
      missingView.unmount()
    }

    // 4) DELETE unknown structured code -> N29 fallback without leaking code.
    stubApi({
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: () => jsonResponse({ detail: { error: 'brand_new_server_refusal' } }, 409),
    })
    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const unknownRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(unknownRemove).toBeEnabled())
    await user.click(unknownRemove)
    const unknownDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    await user.click(within(unknownDialog).getByRole('button', { name: 'Remove file' }))

    expect(
      await within(unknownDialog).findByText('This screen cannot describe this file yet.'),
    ).toBeInTheDocument()
    expect(unknownDialog.textContent).not.toContain('brand_new_server_refusal')
  })

  it('shows Remove on failed supplements with a ready main file and on 2-supplement papers (hiding Remove when 1 remains)', async () => {
    const user = userEvent.setup()

    // 1 ready main + 1 failed supplement -> failed supplement gets Remove; main does not.
    stubApi({
      paperFiles: {
        document_id: DOCUMENT_ID,
        files: [
          FOUR_FILES_PAYLOAD.files[0]!,
          {
            file_id: 'aaaaaaaa-0000-0000-0000-000000000005',
            role: 'supplement',
            display_name: 'Table_S4.xlsx',
            display_mode: 'text',
            status: 'failed',
            tab_order: 1,
            page_count: null,
            content_url: null,
            text_url: null,
          },
        ],
      },
    })
    const failedSuppView = renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    await screen.findByRole('list', { name: 'Files in this paper' })
    expect(screen.queryByRole('button', { name: 'Remove made-up-paper.pdf' })).not.toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Remove Table_S4.xlsx' })).toBeEnabled()
    })
    failedSuppView.unmount()

    // 2 ready supplements (supplements-only paper) -> both show Remove; removing one hides Remove on the remaining sole file.
    let paperFilesCalls = 0
    const twoSupplements: ViewerFilesPayload = {
      document_id: DOCUMENT_ID,
      files: [FOUR_FILES_PAYLOAD.files[1]!, FOUR_FILES_PAYLOAD.files[2]!],
    }
    const oneRemainingSupplement: ViewerFilesPayload = {
      document_id: DOCUMENT_ID,
      files: [FOUR_FILES_PAYLOAD.files[2]!],
    }
    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? twoSupplements : oneRemainingSupplement)
      },
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: (fileId) =>
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: fileId,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 1,
        }),
    })

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })
    const removeFirst = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => {
      expect(removeFirst).toBeEnabled()
      expect(screen.getByRole('button', { name: 'Remove Figure_S2.pdf' })).toBeEnabled()
    })
    await user.click(removeFirst)
    const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.queryByText('Table_S1.xlsx')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Figure_S2.pdf')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
  })

  it('never deadlocks Remove when providerLabel updates or when reopening over warm cached activeJobsQuery', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: PAPER_SOURCE_FILES_STALE_MS } },
    })
    stubApi()

    const view = renderSection(
      { providerLabel: 'ABC', providerShortLabel: 'ABC' },
      client,
    )
    const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(removeButton).toBeEnabled())

    // Updating providerLabel while mounted must NOT deadlock Remove or reset busy state.
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByRole('button', { name: 'Remove Table_S1.xlsx' })).toBeEnabled()

    // Closing and reopening over a warm (non-refetching) activeJobsQuery cache must NOT deadlock Remove.
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open={false}
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    view.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Remove Table_S1.xlsx' })).toBeEnabled()
    })
  })

  it('completes an already-issued DELETE and 3-surface refresh after unmount/paper switch while still aborting pre-checks on paper switch', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const onFilesChanged = vi.fn()
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    const OTHER_DOC_ID = '33333333-3333-3333-3333-333333333333'
    let precheckSignal: AbortSignal | null | undefined
    let deleteSignal: AbortSignal | null | undefined
    let resolveDelete!: (response: Response) => void
    let paperFilesCalls = 0

    // 1) Pre-check IS aborted on paper switch.
    stubApi({
      paperFiles: (url) => {
        if (url.includes(OTHER_DOC_ID)) {
          return jsonResponse({ ...FOUR_FILES_PAYLOAD, document_id: OTHER_DOC_ID })
        }
        return jsonResponse(FOUR_FILES_PAYLOAD)
      },
      onRemovalCheck: (_fileId, init) => {
        precheckSignal = init?.signal
        return new Promise<Response>(() => undefined)
      },
    })

    const precheckView = renderSection(
      { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT },
      client,
    )
    const precheckRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(precheckRemove).toBeEnabled())
    await user.click(precheckRemove)
    expect(precheckSignal?.aborted).toBe(false)

    precheckView.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={OTHER_DOC_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="completed"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    expect(precheckSignal?.aborted).toBe(true)
    precheckView.unmount()

    // 2) Already-issued DELETE is NOT aborted on unmount and completes 3-surface refresh for the original document.
    const deleteClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const deleteInvalidateSpy = vi.spyOn(deleteClient, 'invalidateQueries')
    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : THREE_FILES_AFTER_REMOVAL)
      },
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: (_fileId, init) => {
        deleteSignal = init?.signal
        return new Promise<Response>((resolve) => {
          resolveDelete = resolve
        })
      },
    })

    try {
      const deleteView = renderSection(
        {
          providerLabel: PROVIDER,
          providerShortLabel: PROVIDER_SHORT,
          onFilesChanged,
        },
        deleteClient,
      )
      const deleteRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(deleteRemove).toBeEnabled())
      await user.click(deleteRemove)
      const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))

      // Unmount while DELETE is in flight -> signal must NOT be attached/aborted!
      deleteView.unmount()
      expect(deleteSignal).toBeUndefined()

      resolveDelete(
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 2,
        }),
      )

      await waitFor(() => {
        expect(paperFilesCalls).toBe(2)
        expect(onFilesChanged).toHaveBeenCalledTimes(1)
        expect(viewerEvents).toHaveLength(1)
      })
      expect(viewerEvents[0]).toMatchObject({ documentId: DOCUMENT_ID, refreshOnlyIfOpen: true })
      expect(deleteClient.getQueryData<ViewerFilesPayload>(paperFilesQueryKey(DOCUMENT_ID))?.files).toHaveLength(3)
      expect(deleteInvalidateSpy).toHaveBeenCalledWith({ queryKey: ['documents'] })
      expect(deleteInvalidateSpy).toHaveBeenCalledWith({ queryKey: ['document', DOCUMENT_ID] })
    } finally {
      unsubscribeViewer()
      invalidateSpy.mockRestore()
      deleteInvalidateSpy.mockRestore()
    }
  })

  it('cancels a slow in-flight /source-files fetch before invalidating so stale pre-removal listings cannot mask the removed supplement', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let listingCalls = 0
    let firstListingSignal: AbortSignal | null | undefined
    let resolveSlowListing!: (response: Response) => void
    let paperFilesCalls = 0

    const stalePreRemovalListing = listingWith([
      fileListingFixture.files[0],
      { ...fileListingFixture.files[1], in_this_paper: true, tickable: false, not_tickable_reason: 'already_in_paper' },
    ])
    const freshPostRemovalListing = listingWith([
      fileListingFixture.files[0],
      fileListingFixture.files[1],
    ])

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        return jsonResponse(paperFilesCalls === 1 ? FOUR_FILES_PAYLOAD : THREE_FILES_AFTER_REMOVAL)
      },
      listing: (_url) => {
        listingCalls += 1
        if (listingCalls === 1) {
          return new Promise<Response>((resolve) => {
            resolveSlowListing = resolve
          })
        }
        return jsonResponse(freshPostRemovalListing)
      },
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: (fileId) =>
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: fileId,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 2,
        }),
    })

    // Wrap fetch to capture the first /source-files AbortSignal cleanly.
    const baseFetch = global.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === SOURCE_FILES_URL && !firstListingSignal) {
        firstListingSignal = init?.signal
      }
      return baseFetch(input, init)
    }))

    renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT }, client)

    const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(removeButton).toBeEnabled())
    await user.click(removeButton)
    const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))

    await waitFor(() => expect(listingCalls).toBe(2))
    expect(firstListingSignal?.aborted).toBe(true)
    expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()

    // Late resolution of the cancelled slow listing must NOT overwrite the fresh post-removal listing.
    resolveSlowListing(jsonResponse(stalePreRemovalListing))
    await new Promise((resolve) => setTimeout(resolve, 40))
    expect(screen.getByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument()
  })

  it('publishes refreshOnlyIfOpen viewer refresh on recovery refetch when the first post-removal /files GET fails with 503', async () => {
    const user = userEvent.setup()
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))
    let paperFilesCalls = 0

    stubApi({
      paperFiles: () => {
        paperFilesCalls += 1
        if (paperFilesCalls === 1) {
          return jsonResponse(FOUR_FILES_PAYLOAD)
        }
        // Both direct post-removal fetch (call 2) and immediate invalidation refetch (call 3) fail with 503;
        // manual Try again (call 4) recovers with the post-removal payload.
        if (paperFilesCalls <= 3) {
          return jsonResponse({ detail: 'Temporarily unavailable' }, 503)
        }
        return jsonResponse(THREE_FILES_AFTER_REMOVAL)
      },
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: (fileId) =>
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: fileId,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 2,
        }),
    })

    try {
      renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT })

      const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(removeButton).toBeEnabled())
      await user.click(removeButton)
      const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))

      expect(await screen.findByText("Could not load this paper's files.")).toBeInTheDocument()
      expect(viewerEvents).toHaveLength(0)

      await user.click(await screen.findByRole('button', { name: 'Try again' }))

      await waitFor(() => {
        expect(paperFilesCalls).toBe(4)
        expect(viewerEvents).toHaveLength(1)
      })
      expect(viewerEvents[0]).toMatchObject({
        documentId: DOCUMENT_ID,
        refreshOnlyIfOpen: true,
      })
      expect(viewerEvents[0]!.files.map((file) => file.file_id)).toEqual([
        MAIN_FILE_ID,
        PDF_SUPPLEMENT_FILE_ID,
      ])
    } finally {
      unsubscribeViewer()
    }
  })

  it('keeps Remove disabled while activeJobsQuery is paused on cached empty results (provider and non-provider) and rechecks running jobs on non-provider papers', async () => {
    // 1) Provider paper: paused refetch on cached empty results keeps Remove disabled until online & idle.
    const providerClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    stubApi()

    try {
      const providerView = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT },
        providerClient,
      )
      const providerRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(providerRemove).toBeEnabled())

      onlineManager.setOnline(false)
      void providerClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(DOCUMENT_ID) })

      await waitFor(() => expect(providerRemove).toBeDisabled())

      onlineManager.setOnline(true)
      await waitFor(() => expect(providerRemove).toBeEnabled())
      providerView.unmount()

      // 2) Non-provider paper: paused refetch keeps Remove disabled, and an active job rechecks automatically until clear.
      const nonProviderClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      let nonProviderJobCalls = 0
      stubApi({
        activeJobs: () => {
          nonProviderJobCalls += 1
          if (nonProviderJobCalls === 1) {
            return jsonResponse({
              jobs: [makeJob({ job_id: 'job-uploaded-running', status: 'running', metadata: {} })],
              total: 1,
              limit: 50,
              offset: 0,
            })
          }
          return jsonResponse({ jobs: [], total: 0, limit: 50, offset: 0 })
        },
      })

      renderSection({ providerLabel: null, pollIntervalMs: 25 }, nonProviderClient)
      const nonProviderRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      expect(
        await screen.findByText("This paper's import is still running. You can add or remove files when it has finished."),
      ).toBeInTheDocument()
      expect(nonProviderRemove).toBeDisabled()

      // Automatically rechecks and enables Remove once the running job finishes.
      await waitFor(() => {
        expect(nonProviderJobCalls).toBeGreaterThanOrEqual(2)
        expect(nonProviderRemove).toBeEnabled()
      })

      // Pausing refetch on the non-provider paper also disables Remove until online & idle.
      onlineManager.setOnline(false)
      void nonProviderClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(DOCUMENT_ID) })
      await waitFor(() => expect(nonProviderRemove).toBeDisabled())

      onlineManager.setOnline(true)
      await waitFor(() => expect(nonProviderRemove).toBeEnabled())
    } finally {
      onlineManager.setOnline(true)
    }
  })

  it('disables FileRemovalDialog action buttons when baseRemovalBlocked becomes true while the dialog is open', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    // 1) Confirmation phase: "Remove file" disables when processingStatus becomes non-completed.
    stubApi({
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
    })
    const confirmView = renderSection(
      { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, processingStatus: 'completed' },
      client,
    )
    const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(removeButton).toBeEnabled())
    await user.click(removeButton)

    const confirmDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(within(confirmDialog).getByRole('button', { name: 'Remove file' })).toBeEnabled()

    confirmView.rerender(
      <QueryClientProvider client={client}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="processing"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    expect(within(confirmDialog).getByRole('button', { name: 'Remove file' })).toBeDisabled()
    confirmView.unmount()

    // 2) Pre-check error phase: "Try again" disables when processingStatus becomes non-completed.
    const retryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    stubApi({
      onRemovalCheck: () => jsonResponse({ detail: 'Temporary error' }, 503),
    })
    const errorView = renderSection(
      { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT, processingStatus: 'completed' },
      retryClient,
    )
    const retryRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
    await waitFor(() => expect(retryRemove).toBeEnabled())
    await user.click(retryRemove)

    const errorDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
    expect(await within(errorDialog).findByRole('button', { name: 'Try again' })).toBeEnabled()

    errorView.rerender(
      <QueryClientProvider client={retryClient}>
        <DocumentFilesSection
          documentId={DOCUMENT_ID}
          providerLabel={PROVIDER}
          providerShortLabel={PROVIDER_SHORT}
          processingStatus="processing"
          open
          pollIntervalMs={20}
        />
      </QueryClientProvider>,
    )
    expect(within(errorDialog).getByRole('button', { name: 'Try again' })).toBeDisabled()
  })

  it('discards an older delayed /files refresh so it cannot overwrite a newer removal in cache or the open viewer', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))
    const MAIN_ONLY_PAYLOAD: ViewerFilesPayload = {
      ...FOUR_FILES_PAYLOAD,
      files: [FOUR_FILES_PAYLOAD.files[0]!],
    }

    let paperFilesCalls = 0
    let firstPostRemovalSignal: AbortSignal | undefined
    let resolveFirstPostRemovalFiles!: (response: Response) => void

    stubApi({
      paperFiles: (_url, init) => {
        paperFilesCalls += 1
        if (paperFilesCalls === 1) {
          return jsonResponse(FOUR_FILES_PAYLOAD)
        }
        if (paperFilesCalls === 2) {
          // Direct post-removal #1 `/files` stays pending (and later resolves even if aborted).
          firstPostRemovalSignal = init?.signal ?? undefined
          return new Promise<Response>((resolve) => {
            resolveFirstPostRemovalFiles = resolve
          })
        }
        if (paperFilesCalls === 3) {
          // Reopened mount loads the intermediate 2-ready-file list before removal #2.
          return jsonResponse(THREE_FILES_AFTER_REMOVAL)
        }
        // Removal #2 direct `/files` resolves immediately with main-only payload.
        return jsonResponse(MAIN_ONLY_PAYLOAD)
      },
      onRemovalCheck: (fileId) => jsonResponse(removalCheckFor('removable_plain', { file_id: fileId })),
      onRemoveFile: (fileId) =>
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: fileId,
          removed: true,
          cleanup_finished: true,
          ready_file_count: fileId === TEXT_SUPPLEMENT_FILE_ID ? 2 : 1,
        }),
    })

    try {
      // Removal #1: remove Table_S1.xlsx and unmount before its direct `/files` refresh completes.
      const firstView = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT },
        client,
      )
      const firstRemove = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(firstRemove).toBeEnabled())
      await user.click(firstRemove)
      const firstDialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(firstDialog).getByRole('button', { name: 'Remove file' }))
      await waitFor(() => expect(paperFilesCalls).toBe(2))
      firstView.unmount()

      // Removal #2 on the same paper finishes and publishes MAIN_ONLY_PAYLOAD first.
      renderSection({ providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT }, client)
      const secondRemove = await screen.findByRole('button', { name: 'Remove Figure_S2.pdf' })
      await waitFor(() => expect(secondRemove).toBeEnabled())
      await user.click(secondRemove)
      const secondDialog = await screen.findByRole('dialog', { name: 'Remove Figure_S2.pdf?' })
      await user.click(within(secondDialog).getByRole('button', { name: 'Remove file' }))

      await waitFor(() => {
        expect(viewerEvents).toHaveLength(1)
        expect(viewerEvents[0]!.files.map((file) => file.file_id)).toEqual([MAIN_FILE_ID])
      })
      expect(firstPostRemovalSignal?.aborted).toBe(true)

      // Late resolution of removal #1's stale `/files` payload must NEVER overwrite cache or viewer.
      resolveFirstPostRemovalFiles(jsonResponse(THREE_FILES_AFTER_REMOVAL))
      await new Promise((resolve) => setTimeout(resolve, 40))

      const cached = client.getQueryData<ViewerFilesPayload>(paperFilesQueryKey(DOCUMENT_ID))
      expect(cached?.files.map((file) => file.file_id)).toEqual([MAIN_FILE_ID])
      expect(viewerEvents).toHaveLength(1)
      expect(viewerEvents[0]!.files.map((file) => file.file_id)).toEqual([MAIN_FILE_ID])
    } finally {
      unsubscribeViewer()
    }
  })

  it('recovers an inactive paper viewer when direct /files fails after switching papers and expires failed markers by TTL', async () => {
    const user = userEvent.setup()
    const PAPER_B_ID = '22222222-2222-2222-2222-222222222222'
    const PAPER_B_FILES: ViewerFilesPayload = {
      document_id: PAPER_B_ID,
      files: [{ ...FOUR_FILES_PAYLOAD.files[0]!, file_id: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb' }],
    }
    const viewerEvents: PDFViewerDocumentChangedDetail[] = []
    const unsubscribeViewer = onPDFDocumentChanged((event) => viewerEvents.push(event.detail))

    // Part 1: Paper A DELETE finishes after switching to Paper B; first direct `/files` fails 503,
    // independent recovery `/files` succeeds and updates Paper A's open viewer without retaining A's id.
    let paperAFilesCalls = 0
    let resolveDeleteA!: (response: Response) => void
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
        const method = (init?.method ?? 'GET').toUpperCase()
        if (url === `/api/pdf-viewer/documents/${DOCUMENT_ID}/files`) {
          paperAFilesCalls += 1
          if (paperAFilesCalls === 1) return jsonResponse(FOUR_FILES_PAYLOAD)
          if (paperAFilesCalls === 2) return jsonResponse({ detail: 'Transient 503' }, 503)
          return jsonResponse(THREE_FILES_AFTER_REMOVAL)
        }
        if (url === `/api/pdf-viewer/documents/${PAPER_B_ID}/files`) {
          return jsonResponse(PAPER_B_FILES)
        }
        if (url.startsWith('/api/weaviate/pdf-jobs?')) {
          return jsonResponse({ jobs: [] })
        }
        if (url.endsWith('/source-files')) {
          return jsonResponse(fileListingFixture)
        }
        if (url.endsWith('/removal-check')) {
          return jsonResponse(removalCheckFor('removable_plain'))
        }
        if (method === 'DELETE' && url.includes(`/api/weaviate/documents/${DOCUMENT_ID}/files/`)) {
          return await new Promise<Response>((resolve) => {
            resolveDeleteA = resolve
          })
        }
        throw new Error(`Unexpected fetch: ${method} ${url}`)
      }),
    )

    try {
      const view = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT },
        client,
      )
      const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(removeButton).toBeEnabled())
      await user.click(removeButton)
      const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))
      await waitFor(() => expect(resolveDeleteA).toBeTypeOf('function'))

      // Switch dialog to Paper B while Paper A's DELETE is still in flight.
      view.rerender(
        <QueryClientProvider client={client}>
          <DocumentFilesSection
            documentId={PAPER_B_ID}
            providerLabel={PROVIDER}
            providerShortLabel={PROVIDER_SHORT}
            processingStatus="completed"
            open
            pollIntervalMs={20}
          />
        </QueryClientProvider>,
      )
      await waitFor(() => {
        expect(client.getQueryData(paperFilesQueryKey(PAPER_B_ID))).toBeDefined()
      })

      resolveDeleteA(
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 2,
        }),
      )

      await waitFor(() => {
        expect(paperAFilesCalls).toBe(3)
        expect(viewerEvents).toHaveLength(1)
      })
      expect(viewerEvents[0]).toMatchObject({
        documentId: DOCUMENT_ID,
        refreshOnlyIfOpen: true,
      })
      expect(viewerEvents[0]!.files.map((file) => file.file_id)).toEqual([
        MAIN_FILE_ID,
        PDF_SUPPLEMENT_FILE_ID,
      ])
      expect(hasPendingViewerRefresh(DOCUMENT_ID)).toBe(false)
      view.unmount()

      // Part 2: When BOTH direct and recovery `/files` fail 503 while Paper A is inactive,
      // the pending marker is TTL-bounded and expires after PAPER_SOURCE_FILES_STALE_MS.
      let persistentFailCalls = 0
      let resolveSecondDelete!: (response: Response) => void
      const client2 = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
          const method = (init?.method ?? 'GET').toUpperCase()
          if (url === `/api/pdf-viewer/documents/${DOCUMENT_ID}/files`) {
            persistentFailCalls += 1
            if (persistentFailCalls === 1) return jsonResponse(FOUR_FILES_PAYLOAD)
            return jsonResponse({ detail: 'Still unavailable' }, 503)
          }
          if (url === `/api/pdf-viewer/documents/${PAPER_B_ID}/files`) {
            return jsonResponse(PAPER_B_FILES)
          }
          if (url.startsWith('/api/weaviate/pdf-jobs?')) {
            return jsonResponse({ jobs: [] })
          }
          if (url.endsWith('/source-files')) {
            return jsonResponse(fileListingFixture)
          }
          if (url.endsWith('/removal-check')) {
            return jsonResponse(removalCheckFor('removable_plain'))
          }
          if (method === 'DELETE' && url.includes(`/api/weaviate/documents/${DOCUMENT_ID}/files/`)) {
            return await new Promise<Response>((resolve) => {
              resolveSecondDelete = resolve
            })
          }
          throw new Error(`Unexpected fetch: ${method} ${url}`)
        }),
      )

      const view2 = renderSection(
        { providerLabel: PROVIDER, providerShortLabel: PROVIDER_SHORT },
        client2,
      )
      const remove2 = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(remove2).toBeEnabled())
      await user.click(remove2)
      const dialog2 = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(dialog2).getByRole('button', { name: 'Remove file' }))
      await waitFor(() => expect(resolveSecondDelete).toBeTypeOf('function'))
      view2.unmount()

      const baseNow = Date.now()
      const dateNowSpy = vi.spyOn(Date, 'now').mockReturnValue(baseNow)
      try {
        resolveSecondDelete(
          jsonResponse({
            document_id: DOCUMENT_ID,
            file_id: TEXT_SUPPLEMENT_FILE_ID,
            removed: true,
            cleanup_finished: true,
            ready_file_count: 2,
          }),
        )
        await waitFor(() => expect(persistentFailCalls).toBe(3))
        expect(hasPendingViewerRefresh(DOCUMENT_ID)).toBe(true)

        dateNowSpy.mockReturnValue(baseNow + PAPER_SOURCE_FILES_STALE_MS + 1)
        expect(hasPendingViewerRefresh(DOCUMENT_ID)).toBe(false)
      } finally {
        dateNowSpy.mockRestore()
      }
    } finally {
      unsubscribeViewer()
    }
  })

  it('notifies subscribed Library listeners when an issued DELETE finishes after unmount without a live direct callback', async () => {
    const user = userEvent.setup()
    const libraryListener = vi.fn()
    const unsubscribeLibrary = subscribeDocumentsListInvalidated(libraryListener)
    const unmountedDirectCallback = vi.fn()
    let resolveDelete!: (response: Response) => void

    stubApi({
      onRemovalCheck: () => jsonResponse(removalCheckFor('removable_plain')),
      onRemoveFile: () =>
        new Promise<Response>((resolve) => {
          resolveDelete = resolve
        }),
    })

    try {
      const view = renderSection({
        providerLabel: PROVIDER,
        providerShortLabel: PROVIDER_SHORT,
        onFilesChanged: unmountedDirectCallback,
      })
      const removeButton = await screen.findByRole('button', { name: 'Remove Table_S1.xlsx' })
      await waitFor(() => expect(removeButton).toBeEnabled())
      await user.click(removeButton)
      const dialog = await screen.findByRole('dialog', { name: 'Remove Table_S1.xlsx?' })
      await user.click(within(dialog).getByRole('button', { name: 'Remove file' }))
      await waitFor(() => expect(resolveDelete).toBeTypeOf('function'))

      view.unmount()
      resolveDelete(
        jsonResponse({
          document_id: DOCUMENT_ID,
          file_id: TEXT_SUPPLEMENT_FILE_ID,
          removed: true,
          cleanup_finished: true,
          ready_file_count: 2,
        }),
      )

      await waitFor(() => expect(libraryListener).toHaveBeenCalledTimes(1))
      expect(unmountedDirectCallback).toHaveBeenCalledTimes(1)
    } finally {
      unsubscribeLibrary()
    }
  })
})

describe('listedProviderLabel', () => {
  it('uses only the provider\'s configured label, never its raw id', () => {
    expect(
      listedProviderLabel({
        provider: 'abc_literature',
        providerMetadata: { displayLabel: 'ABC Literature', referenceLabelPriority: null },
      }),
    ).toBe('ABC Literature')
    expect(listedProviderLabel({ provider: 'abc_literature', providerMetadata: null })).toBeNull()
    expect(listedProviderLabel(null)).toBeNull()
  })
})

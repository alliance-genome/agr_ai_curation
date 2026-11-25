import { act } from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, screen, within } from '../../test/test-utils';
import { render, userEvent } from '../../test/test-utils';
import fileListingFixture from '@/test/fixtures/supplements/file_listing.json';
import { uploadPdfDocument } from '@/features/documents/pdfUploadFlow';
import { WAITING_FOR_SUPPLEMENTS_STAGE } from '@/features/documents/supplementProgress';
import { emitGlobalToast } from '@/lib/globalNotifications';
import supplementWaitMetadataFixture from '@/test/fixtures/supplements/supplement_wait_metadata.json';
import { formatSupplementWaitEndTime } from '../../components/weaviate/paperFileReasonText';
import AddLiteraturePage from './AddLiteraturePage';

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('@/features/documents/pdfUploadFlow', () => ({
  validatePdfSelection: vi.fn((files: File[]) => ({
    ok: files.length > 0 && files.every((file) => file.type === 'application/pdf' || file.name.endsWith('.pdf')),
    files,
    error: files.length > 0 ? undefined : 'Please select a PDF file to upload.',
  })),
  uploadPdfDocument: vi.fn(),
}));

vi.mock('@/lib/globalNotifications', () => ({
  emitGlobalToast: vi.fn(),
}));

const okJson = (payload: unknown) => new Response(JSON.stringify(payload), {
  status: 200,
  headers: { 'Content-Type': 'application/json' },
});

const emptyPdfJobsResponse = {
  jobs: [],
  pagination: { total: 0, limit: 50, offset: 0 },
};

const SOURCE_MD5 = ['000c0dd769dd7326', '8e3c752102337c96'].join('');

const defaultProviderConfiguration = {
  provider: 'abc_literature',
  import_enabled: true,
  presentation: {
    display_label: 'ABC Literature',
    identifier_help_label: 'Use configured literature identifiers.',
    identifier_examples: ['SOURCE:123', 'SOURCE:456'],
  },
};

const defaultImportResponse = {
  imported_count: 1,
  results: [
    {
      identifier: 'PMID:23970418',
      normalized_identifier: 'PMID:23970418',
      status: 'imported',
      message: 'Import queued for background processing.',
      document_id: 'doc-api-1',
      job_id: 'job-api-1',
      filename: 'paper-from-api.pdf',
      source_provenance: {
        provider: 'abc_literature',
        viewer_mode: 'local_pdf',
        pdf_artifact_id: '4040596',
        converted_artifact_id: '4672234',
        source_md5: SOURCE_MD5,
      },
    },
  ],
};

class MockEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public readonly url: string) {
    mockEventSources.push(this);
  }

  close = vi.fn();
}

const mockEventSources: MockEventSource[] = [];

const stubRoutedFetch = (
  identifierResponses: Array<unknown | Response | Promise<Response>> = [defaultImportResponse],
  providerConfiguration: unknown = defaultProviderConfiguration,
  pdfJobsResponse: unknown = emptyPdfJobsResponse,
  fileListingHandler?: (
    identifier: string,
    init?: RequestInit,
  ) => unknown | Response | Promise<Response>,
) => {
  const responses = [...identifierResponses];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/weaviate/documents/source-provider')) {
      if (providerConfiguration instanceof Response) {
        return providerConfiguration;
      }
      return okJson(providerConfiguration);
    }
    if (url.includes('/api/weaviate/pdf-jobs')) {
      return okJson(pdfJobsResponse);
    }
    if (url.startsWith('/api/weaviate/document-sources/files')) {
      if (!fileListingHandler) {
        throw new Error(`Unexpected file listing request: ${url}`);
      }
      const parsed = new URL(url, 'http://localhost');
      const requestedIdentifier = parsed.searchParams.get('identifier') ?? '';
      const listingResponse = await fileListingHandler(requestedIdentifier, init);
      if (listingResponse instanceof Response) {
        return listingResponse;
      }
      return okJson(listingResponse);
    }

    const nextResponse = responses.length > 0 ? responses.shift() : defaultImportResponse;
    const resolvedResponse = await nextResponse;
    if (resolvedResponse instanceof Response) {
      return resolvedResponse;
    }
    return okJson(resolvedResponse);
  }));
};

describe('AddLiteraturePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockEventSources.length = 0;
    vi.mocked(uploadPdfDocument).mockResolvedValue('mock-doc-uploaded');
    vi.stubGlobal('EventSource', MockEventSource);
    stubRoutedFetch();
  });

  it('renders production Add Literature controls with an empty shared results table', () => {
    render(<AddLiteraturePage />);

    expect(screen.getByRole('heading', { name: 'Add Literature' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Identifiers/i })).toBeInTheDocument();
    expect(screen.getByText('Add Publication by Identifiers')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resolve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Resolve and Import' })).toBeDisabled();
    expect(screen.getByText('PDF Jobs')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Identifier Results' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Literature import results' })).toBeInTheDocument();
    expect(screen.getByText('No import results yet.')).toBeInTheDocument();
    expect(screen.queryByText(/Successful source retrievals/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Review states')).not.toBeInTheDocument();
  });

  it('renders identifier help and examples owned by provider presentation', async () => {
    render(<AddLiteraturePage />);

    await screen.findByText('Use configured literature identifiers.');
    const input = screen.getByLabelText('Source identifiers');
    expect(input).toHaveAttribute('placeholder', 'SOURCE:123\nSOURCE:456');
    expect(input).toHaveAccessibleDescription('Use configured literature identifiers.');
    expect(screen.queryByText(/PMID|PubMed|AGRKB|ABC identifiers/)).not.toBeInTheDocument();
  });

  it('renders honest generic guidance when no identifier provider is configured', async () => {
    stubRoutedFetch([defaultImportResponse], {
      provider: null,
      import_enabled: false,
      presentation: null,
    });
    render(<AddLiteraturePage />);

    expect(await screen.findByText(
      'Identifier guidance is not configured for the current document source; use commas or new lines to separate values.',
    )).toBeInTheDocument();
    expect(screen.getByLabelText('Source identifiers')).not.toHaveAttribute('placeholder');
    expect(screen.queryByText(/PMID|PubMed|AGRKB|ABC identifiers/)).not.toBeInTheDocument();
  });

  it('distinguishes a provider-guidance request failure from unconfigured guidance', async () => {
    stubRoutedFetch([defaultImportResponse], new Response(null, { status: 500 }));
    render(<AddLiteraturePage />);

    expect(await screen.findByText(
      'Identifier guidance could not be loaded. Try refreshing the page.',
    )).toBeInTheDocument();
    expect(screen.queryByText(
      'Identifier guidance is not configured for the current document source; use commas or new lines to separate values.',
    )).not.toBeInTheDocument();
  });

  it('uses shared job-window, result-limit, and fallback-poll configuration', async () => {
    const setIntervalSpy = vi.spyOn(window, 'setInterval');
    render(<AddLiteraturePage />);

    await act(async () => undefined);
    expect(fetch).toHaveBeenCalledWith(
      '/api/weaviate/pdf-jobs?window_days=7&limit=50',
      expect.objectContaining({ credentials: 'include' }),
    );
    expect(mockEventSources[0]?.url).toBe(
      '/api/weaviate/pdf-jobs/stream?window_days=7&limit=50',
    );

    act(() => mockEventSources[0]?.onerror?.());
    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 5000);
    setIntervalSpy.mockRestore();
  });

  it('resolves identifiers through the dry-run backend endpoint', async () => {
    const user = userEvent.setup();
    stubRoutedFetch([{
      imported_count: 0,
      results: [
        {
          identifier: 'PMID:23970418',
          normalized_identifier: 'PMID:23970418',
          status: 'resolved',
          message: 'Ready to import.',
          filename: 'FBrf0223182.pdf',
          source_provenance: {
            provider: 'abc_literature',
            viewer_mode: 'local_pdf',
            pdf_artifact_id: '4040596',
            converted_artifact_id: '4672234',
            source_md5: SOURCE_MD5,
          },
        },
      ],
    }]);
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:23970418' } });
    await user.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText('Ready to import.')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Resolved 1 of 1 identifier. Review Identifier Results below; no imports were started.',
    );
    expect(screen.getByText('Resolved')).toBeInTheDocument();
    expect(screen.getByText('FBrf0223182.pdf')).toBeInTheDocument();
    expect(screen.getByText('PDF 4040596 / MD 4672234')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith('/api/weaviate/documents/resolve/source-identifiers', expect.objectContaining({
      method: 'POST',
      credentials: 'include',
      body: JSON.stringify({ identifiers: 'PMID:23970418' }),
    }));
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('opens a finished paper in chat straight from PDF Jobs', async () => {
    const now = '2026-10-02T00:00:00.000Z';
    stubRoutedFetch([defaultImportResponse], defaultProviderConfiguration, {
      jobs: [
        {
          job_id: 'job-done',
          document_id: 'doc-done',
          user_id: 1,
          filename: 'finished-paper.pdf',
          status: 'completed',
          current_stage: 'completed',
          progress_percentage: 100,
          message: 'Processing completed',
          cancel_requested: false,
          created_at: now,
          updated_at: now,
          completed_at: now,
        },
      ],
      pagination: { total: 1, limit: 50, offset: 0 },
    });
    render(<AddLiteraturePage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Load for chat' }));

    expect(screen.getByRole('button', { name: 'Load for curation' })).toBeInTheDocument();
    expect(mockNavigate).toHaveBeenCalledWith('/', {
      state: {
        loadForChatDocument: {
          id: 'doc-done',
          filename: 'finished-paper.pdf',
        },
      },
    });
  });

  it('places identifier results before background PDF jobs without moving focus', () => {
    render(<AddLiteraturePage />);

    const resultsHeading = screen.getByRole('heading', { name: 'Identifier Results' });
    const jobsHeading = screen.getByRole('heading', { name: 'PDF Jobs' });
    expect(resultsHeading.compareDocumentPosition(jobsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.activeElement).not.toBe(resultsHeading);
  });

  it('labels provider source-only results without implying converted Markdown is pending', async () => {
    const user = userEvent.setup();
    stubRoutedFetch([{
      imported_count: 0,
      results: [
        {
          identifier: 'PMID:23970418',
          normalized_identifier: 'PMID:23970418',
          status: 'resolved',
          message: 'Ready to import with source PDF.',
          filename: 'source-only.pdf',
          source_provenance: {
            provider: 'abc_literature',
            viewer_mode: 'local_pdf',
            pdf_artifact_id: '4040596',
            source_md5: SOURCE_MD5,
          },
        },
      ],
    }]);
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:23970418' } });
    await user.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText('PDF 4040596 / MD not available')).toBeInTheDocument();
    expect(screen.queryByText('PDF 4040596 / MD pending')).not.toBeInTheDocument();
  });

  it('clears stale dry-run results when identifiers change before import', async () => {
    const user = userEvent.setup();
    stubRoutedFetch([
      {
        imported_count: 0,
        results: [
          {
            identifier: 'PMID:23970418',
            normalized_identifier: 'PMID:23970418',
            status: 'resolved',
            message: 'Ready to import.',
            filename: 'first-paper.pdf',
            source_provenance: {
              provider: 'abc_literature',
              viewer_mode: 'local_pdf',
              pdf_artifact_id: '4040596',
              source_md5: SOURCE_MD5,
            },
          },
        ],
      },
      {
        imported_count: 1,
        results: [
          {
            identifier: 'PMID:1',
            normalized_identifier: 'PMID:1',
            status: 'imported',
            message: 'Import queued for background processing.',
            document_id: 'doc-new',
            job_id: 'job-new',
            filename: 'new-paper.pdf',
            source_provenance: {
              provider: 'abc_literature',
              viewer_mode: 'local_pdf',
              pdf_artifact_id: 'pdf-new',
              source_md5: 'source-md5-new',
            },
          },
        ],
      },
    ]);
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:23970418' } });
    await user.click(screen.getByRole('button', { name: 'Resolve' }));
    expect(await screen.findByText('first-paper.pdf')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:1' } });

    expect(screen.queryByText('first-paper.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('No import results yet.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Resolve and Import' }));

    expect(await screen.findByText('new-paper.pdf')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith('/api/weaviate/documents/import/source-identifiers', expect.objectContaining({
      body: JSON.stringify({ identifiers: 'PMID:1' }),
    }));
  });

  it('ignores in-flight resolve results after identifiers change', async () => {
    const user = userEvent.setup();
    let resolveFetch: ((response: Response) => void) | undefined;
    stubRoutedFetch([new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    })]);
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:23970418' } });
    await user.click(screen.getByRole('button', { name: 'Resolve' }));

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:1' } });

    await act(async () => {
      resolveFetch?.(okJson({
        imported_count: 0,
        results: [
          {
            identifier: 'PMID:23970418',
            normalized_identifier: 'PMID:23970418',
            status: 'resolved',
            message: 'Ready to import.',
            filename: 'stale-paper.pdf',
            source_provenance: {
              provider: 'abc_literature',
              viewer_mode: 'local_pdf',
              pdf_artifact_id: 'stale-pdf',
              source_md5: 'stale-md5',
            },
          },
        ],
      }));
    });

    expect(screen.queryByText('stale-paper.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('No import results yet.')).toBeInTheDocument();
  });

  it('keeps upload results when an older identifier request resolves later', async () => {
    const user = userEvent.setup();
    let resolveFetch: ((response: Response) => void) | undefined;
    stubRoutedFetch([new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    })]);
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:23970418' } });
    await user.click(screen.getByRole('button', { name: 'Resolve' }));
    await user.click(screen.getByRole('tab', { name: /Upload PDFs/i }));

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const pdf = new File(['mock pdf'], 'uploaded-after-resolve.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [pdf] } });
    expect(await screen.findByText('Queued 1 PDF for background processing.')).toBeInTheDocument();

    await act(async () => {
      resolveFetch?.(okJson({
        imported_count: 0,
        results: [
          {
            identifier: 'PMID:23970418',
            normalized_identifier: 'PMID:23970418',
            status: 'resolved',
            message: 'Ready to import.',
            filename: 'late-identifier-result.pdf',
            source_provenance: {
              provider: 'abc_literature',
              viewer_mode: 'local_pdf',
              pdf_artifact_id: 'late-pdf',
              source_md5: 'late-md5',
            },
          },
        ],
      }));
    });

    expect(screen.getAllByText('uploaded-after-resolve.pdf')).toHaveLength(2);
    expect(screen.queryByText('late-identifier-result.pdf')).not.toBeInTheDocument();
  });

  it('keeps newer identifier results when an older upload completes later', async () => {
    const user = userEvent.setup();
    let resolveUpload: ((documentId: string) => void) | undefined;
    vi.mocked(uploadPdfDocument).mockImplementationOnce(() => new Promise<string>((resolve) => {
      resolveUpload = resolve;
    }));
    render(<AddLiteraturePage />);

    await user.click(screen.getByRole('tab', { name: /Upload PDFs/i }));

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const pdf = new File(['mock pdf'], 'late-upload.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [pdf] } });

    await user.click(screen.getByRole('tab', { name: /Identifiers/i }));
    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:23970418' } });
    await user.click(screen.getByRole('button', { name: 'Resolve' }));
    expect(await screen.findByText('paper-from-api.pdf')).toBeInTheDocument();
    const pdfJobsCallsBeforeUploadCompletion = vi.mocked(fetch).mock.calls.filter(
      ([input]) => String(input).includes('/api/weaviate/pdf-jobs'),
    ).length;

    await act(async () => {
      resolveUpload?.('late-upload-document');
    });

    expect(screen.getByText('paper-from-api.pdf')).toBeInTheDocument();
    expect(screen.queryByText('late-upload.pdf')).not.toBeInTheDocument();
    expect(emitGlobalToast).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Your PDFs are processing in the background. You can safely navigate away.',
    }));
    expect(vi.mocked(fetch).mock.calls.filter(
      ([input]) => String(input).includes('/api/weaviate/pdf-jobs'),
    )).toHaveLength(pdfJobsCallsBeforeUploadCompletion + 1);
  });

  it('keeps an in-flight valid upload current after rejecting a newer invalid selection', async () => {
    const user = userEvent.setup();
    let resolveUpload: ((documentId: string) => void) | undefined;
    vi.mocked(uploadPdfDocument).mockImplementationOnce(() => new Promise<string>((resolve) => {
      resolveUpload = resolve;
    }));
    render(<AddLiteraturePage />);

    await user.click(screen.getByRole('tab', { name: /Upload PDFs/i }));

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const pdf = new File(['mock pdf'], 'valid-upload.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [pdf] } });

    const invalidFile = new File(['plain text'], 'not-a-pdf.txt', { type: 'text/plain' });
    fireEvent.change(fileInput, { target: { files: [invalidFile] } });
    expect(await screen.findByText('Please select PDF files only.')).toBeInTheDocument();

    await act(async () => {
      resolveUpload?.('valid-upload-document');
    });

    expect(await screen.findByText('Queued 1 PDF for background processing.')).toBeInTheDocument();
    expect(screen.getAllByText('valid-upload.pdf')).toHaveLength(2);
  });

  it('imports identifiers through the durable import endpoint', async () => {
    const user = userEvent.setup();
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), {
      target: { value: 'PMID:23970418\nAGRKB:101000000055784' },
    });
    await user.click(screen.getByRole('button', { name: 'Resolve and Import' }));

    expect(await screen.findByText('paper-from-api.pdf')).toBeInTheDocument();
    expect(screen.getByText('Job job-api-1')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith('/api/weaviate/documents/import/source-identifiers', expect.objectContaining({
      method: 'POST',
      credentials: 'include',
      body: JSON.stringify({ identifiers: 'PMID:23970418\nAGRKB:101000000055784' }),
    }));
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('maps backend namespaced error codes to production status chips', async () => {
    const user = userEvent.setup();
    stubRoutedFetch([{
      imported_count: 0,
      results: [
        {
          identifier: 'PMID:1',
          normalized_identifier: 'PMID:1',
          status: 'error',
          error_code: 'document_source_access_denied',
          message: 'No source PDF is accessible to this curator.',
        },
        {
          identifier: 'PMID:2',
          normalized_identifier: 'PMID:2',
          status: 'error',
          error_code: 'document_source_unavailable',
          message: 'Document-source lookup is unavailable.',
        },
        {
          identifier: 'PMID:7',
          normalized_identifier: 'PMID:7',
          status: 'error',
          error_code: 'document_source_import_unavailable',
          message: 'Document-source import is unavailable.',
        },
        {
          identifier: 'PMID:3',
          normalized_identifier: 'PMID:3',
          status: 'error',
          error_code: 'document_source_conversion_running',
          message: 'Converted Markdown is not available yet.',
        },
        {
          identifier: 'PMID:4',
          normalized_identifier: 'PMID:4',
          status: 'error',
          error_code: 'document_source_no_source_artifact',
          message: 'No source PDF artifact is available.',
        },
        {
          identifier: 'PMID:5',
          normalized_identifier: 'PMID:5',
          status: 'error',
          error_code: 'document_source_ambiguous_match',
          message: 'Multiple source PDFs require curator selection.',
        },
        {
          identifier: 'PMID:6',
          normalized_identifier: 'PMID:6',
          status: 'error',
          error_code: 'document_source_conversion_failed',
          message: 'Provider conversion failed.',
        },
      ],
    }]);
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:1\nPMID:2\nPMID:3' } });
    await user.click(screen.getByRole('button', { name: 'Resolve and Import' }));

    expect(await screen.findByText('No source PDF is accessible to this curator.')).toBeInTheDocument();
    expect(screen.getByText('Access denied')).toBeInTheDocument();
    expect(screen.getAllByText('Provider unavailable')).toHaveLength(2);
    expect(screen.getByText('Conversion running')).toBeInTheDocument();
    expect(screen.getByText('No source PDF')).toBeInTheDocument();
    expect(screen.getByText('Needs selection')).toBeInTheDocument();
    expect(screen.getByText('Conversion failed')).toBeInTheDocument();
  });

  it('uploads selected PDFs from Add Literature into the shared results table', async () => {
    const user = userEvent.setup();
    render(<AddLiteraturePage />);

    await user.click(screen.getByRole('tab', { name: /Upload PDFs/i }));

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const pdf = new File(['mock pdf'], 'paper.pdf', { type: 'application/pdf' });

    fireEvent.change(fileInput, { target: { files: [pdf] } });

    expect(await screen.findByText('Queued 1 PDF for background processing.')).toBeInTheDocument();
    expect(uploadPdfDocument).toHaveBeenCalledWith(pdf);
    expect(screen.getAllByText('paper.pdf')).toHaveLength(2);
    expect(screen.getByText('Queued upload')).toBeInTheDocument();
    expect(screen.getByText('PDF uploaded PDF / MD pending')).toBeInTheDocument();
    expect(screen.queryByText('No source md5 match')).not.toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('shows failed PDF uploads in the shared results without an existing document label', async () => {
    const user = userEvent.setup();
    vi.mocked(uploadPdfDocument).mockRejectedValueOnce(new Error('Upload failed hard'));
    render(<AddLiteraturePage />);

    await user.click(screen.getByRole('tab', { name: /Upload PDFs/i }));

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const pdf = new File(['mock pdf'], 'bad-paper.pdf', { type: 'application/pdf' });

    fireEvent.change(fileInput, { target: { files: [pdf] } });

    expect(await screen.findByText('Upload failed hard')).toBeInTheDocument();
    expect(screen.getByText('bad-paper.pdf')).toBeInTheDocument();
    expect(screen.getByText('Not queued')).toBeInTheDocument();
    expect(screen.queryByText('Existing document')).not.toBeInTheDocument();
  });

  it('returns a PDF-backed row to the Library inventory', async () => {
    const user = userEvent.setup();
    render(<AddLiteraturePage />);

    fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:23970418' } });
    await user.click(screen.getByRole('button', { name: 'Resolve and Import' }));
    await user.click(await screen.findByRole('button', { name: 'View paper-from-api.pdf in Library' }));

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents');
  });

  it('passes configured provider presentation labels to PdfJobsPanel and updates supplement metadata from SSE', async () => {
    const now = '2026-11-24T15:00:00.000Z';
    const initialJob = {
      job_id: 'job-supp-wait',
      document_id: 'doc-supp-1',
      user_id: 1,
      filename: 'paper-with-supplements.pdf',
      status: 'running',
      current_stage: WAITING_FOR_SUPPLEMENTS_STAGE,
      progress_percentage: 0,
      message: 'Main paper ready. Waiting for EXS to convert 1 supplement; you can use the paper and leave this page.',
      cancel_requested: false,
      metadata: supplementWaitMetadataFixture.mixed_waiting,
      created_at: now,
      updated_at: now,
      completed_at: null,
    };
    stubRoutedFetch(
      [defaultImportResponse],
      {
        provider: 'example_source',
        import_enabled: true,
        presentation: {
          display_label: 'Example Source',
          short_label: 'EXS',
          identifier_help_label: 'Use Example Source identifiers.',
          identifier_examples: ['EXS:123'],
        },
      },
      {
        jobs: [initialJob],
        pagination: { total: 1, limit: 50, offset: 0 },
      },
    );

    render(<AddLiteraturePage />);

    const expectedTime = formatSupplementWaitEndTime(
      supplementWaitMetadataFixture.mixed_waiting.supplement_wait_ends_at,
    );
    expect(await screen.findByText(`EXS has until ${expectedTime} to convert them.`)).toBeInTheDocument();
    expect(screen.getByText('Waiting for EXS')).toBeInTheDocument();
    expect(screen.getByText('Converted_S1.xlsx')).toBeInTheDocument();
    expect(screen.getByText('Waiting to be added')).toBeInTheDocument();
    expect(screen.queryByText(/ABC/)).not.toBeInTheDocument();

    // Verify metadata-only SSE updates re-render supplement entries even when updated_at matches.
    act(() => {
      mockEventSources[0]?.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            jobs: [
              {
                ...initialJob,
                metadata: {
                  ...supplementWaitMetadataFixture.mixed_waiting,
                  supplements: [
                    {
                      ...supplementWaitMetadataFixture.mixed_waiting.supplements[0],
                      state: 'added',
                      file_id: '22222222-2222-2222-2222-222222222223',
                    },
                    supplementWaitMetadataFixture.mixed_waiting.supplements[1],
                  ],
                },
              },
            ],
          }),
        }),
      );
    });

    expect(screen.getByText('Added')).toBeInTheDocument();
  });

  describe('per-paper supplement selection (ALL-1746)', () => {
    const allianceProviderConfig = {
      provider: 'abc_literature',
      import_enabled: true,
      presentation: {
        display_label: 'ABC Literature',
        short_label: 'ABC',
        identifier_help_label: 'Use configured literature identifiers.',
        identifier_examples: ['SOURCE:123', 'SOURCE:456'],
      },
    };

    const nonAbcProviderConfig = {
      provider: 'genome_archive',
      import_enabled: true,
      presentation: {
        display_label: 'Genome Archive',
        short_label: 'GA',
        identifier_help_label: 'Use Genome Archive identifiers.',
        identifier_examples: ['GA:123'],
      },
    };

    const buildIdentifierListingFromFixture = () => ({
      ...fileListingFixture,
      files: [
        {
          ...fileListingFixture.files[0],
          in_this_paper: false,
          converted_text_id: null,
          converted_md5: null,
          tickable: true,
          not_tickable_reason: null,
          check: { ...fileListingFixture.files[0]!.check, status: 'not_checked' },
        },
        fileListingFixture.files[1],
        fileListingFixture.files[2],
        {
          ...fileListingFixture.files[3],
          tickable: true,
          not_tickable_reason: null,
          last_provider_observation: null,
        },
        fileListingFixture.files[4],
        fileListingFixture.files[5],
      ],
    });

    const resolvedRow = (
      identifier: string,
      filename: string,
      normalizedIdentifier: string = identifier,
    ) => ({
      identifier,
      normalized_identifier: normalizedIdentifier,
      status: 'resolved',
      message: 'Ready to import.',
      filename,
      source_provenance: {
        provider: 'abc_literature',
        viewer_mode: 'local_pdf',
        pdf_artifact_id: '4040596',
        converted_artifact_id: '4672234',
        source_md5: SOURCE_MD5,
      },
    });

    it('keeps file listing strictly lazy on Resolve and lists only the clicked paper with caching', async () => {
      const user = userEvent.setup();
      const listedIdentifiers: string[] = [];
      stubRoutedFetch(
        [{
          imported_count: 0,
          results: [
            resolvedRow('PMID:101', 'paper-101.pdf'),
            resolvedRow('PMID:202', 'paper-202.pdf'),
          ],
        }],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        (identifier) => {
          listedIdentifiers.push(identifier);
          return buildIdentifierListingFromFixture();
        },
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), {
        target: { value: 'PMID:101\nPMID:202' },
      });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));

      await screen.findByText('paper-101.pdf');
      expect(listedIdentifiers).toEqual([]);
      expect(screen.queryByRole('button', { name: /list all/i })).not.toBeInTheDocument();

      const chooseButtons = screen.getAllByRole('button', { name: /^Choose supplements for / });
      expect(chooseButtons).toHaveLength(2);
      const firstChooseButton = screen.getByRole('button', { name: 'Choose supplements for PMID:101' });
      expect(firstChooseButton).toHaveTextContent('Choose supplements');
      expect(firstChooseButton).toHaveAttribute('aria-expanded', 'false');
      expect(firstChooseButton).toHaveAttribute('aria-controls');

      await user.click(firstChooseButton);

      const fileList = await screen.findByRole('list', { name: 'Files for PMID:101' });
      expect(listedIdentifiers).toEqual(['PMID:101']);
      expect(firstChooseButton).toHaveAttribute('aria-expanded', 'true');
      const controlledPanelId = firstChooseButton.getAttribute('aria-controls');
      expect(controlledPanelId).toBeTruthy();
      expect(document.getElementById(controlledPanelId!)).toContainElement(fileList);

      // Main paper is checked and disabled with N2
      const mainCheckbox = within(fileList).getByRole('checkbox', { name: 'Made_up_main' });
      expect(mainCheckbox).toBeChecked();
      expect(mainCheckbox).toBeDisabled();
      expect(within(fileList).getByText('Main paper (always imported)')).toBeInTheDocument();

      // Converted tickable supplement (Card 8 + few-characters note)
      const convertedCheckbox = within(fileList).getByRole('checkbox', { name: 'Made_up_S20' });
      expect(convertedCheckbox).not.toBeChecked();
      expect(convertedCheckbox).toBeEnabled();
      expect(within(fileList).getByText('Converted by ABC Literature; not added to this paper')).toBeInTheDocument();
      expect(within(fileList).getByText('Little text: about 120 characters')).toBeInTheDocument();

      // Unconverted tickable supplement (N3) wired via aria-describedby
      const unconvertedCheckbox = within(fileList).getByRole('checkbox', { name: 'Made_up_S40' });
      expect(unconvertedCheckbox).not.toBeChecked();
      expect(unconvertedCheckbox).toBeEnabled();
      const n3Description = within(fileList).getByText(
        'Not converted by ABC Literature yet. If you choose it, the import asks ABC Literature to convert it; the main paper does not wait for it.',
      );
      expect(n3Description).toBeInTheDocument();
      expect(unconvertedCheckbox).toHaveAccessibleDescription(
        'Not converted by ABC Literature yet. If you choose it, the import asks ABC Literature to convert it; the main paper does not wait for it.',
      );

      // Non-tickable supplements have no checkboxes and show Card 8 reason texts
      expect(within(fileList).getAllByRole('checkbox')).toHaveLength(3);
      expect(within(fileList).getByText('Made_up_S30')).toBeInTheDocument();
      expect(within(fileList).getByText('A table in its converted text has rows that do not match its header')).toBeInTheDocument();
      expect(within(fileList).getByText('Made_up_S50')).toBeInTheDocument();
      expect(within(fileList).getByText('Large data file. Files like this are not read.')).toBeInTheDocument();
      expect(within(fileList).getByText('Made_up_S60')).toBeInTheDocument();
      expect(within(fileList).getByText('File type not supported')).toBeInTheDocument();

      // Counts (Card 8)
      expect(screen.getByText('2 more files in ABC Literature are not listed because you cannot open them.')).toBeInTheDocument();
      expect(screen.getByText('1 further copy of a listed file is not listed again.')).toBeInTheDocument();

      // Collapsing and re-expanding uses cached listing without re-fetching
      await user.click(firstChooseButton);
      expect(screen.queryByRole('list', { name: 'Files for PMID:101' })).not.toBeInTheDocument();
      await user.click(firstChooseButton);
      expect(screen.getByRole('list', { name: 'Files for PMID:101' })).toBeInTheDocument();
      expect(listedIdentifiers).toEqual(['PMID:101']);
    });

    it('updates the Import button with N4 counts and posts exact I6 supplement fields without extra keys', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [resolvedRow('PMID:101', 'paper-101.pdf')],
          },
          defaultImportResponse,
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        () => buildIdentifierListingFromFixture(),
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:101' } });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));

      const convertedCheckbox = await screen.findByRole('checkbox', { name: 'Made_up_S20' });
      const unconvertedCheckbox = screen.getByRole('checkbox', { name: 'Made_up_S40' });

      expect(screen.getByRole('button', { name: 'Resolve and Import' })).toBeInTheDocument();

      await user.click(convertedCheckbox);
      expect(screen.getByRole('button', { name: 'Import with 1 supplement' })).toBeInTheDocument();

      await user.click(unconvertedCheckbox);
      const importTwoButton = screen.getByRole('button', { name: 'Import with 2 supplements' });
      expect(importTwoButton).toBeInTheDocument();

      await user.click(importTwoButton);
      await screen.findByText('paper-from-api.pdf');

      const importCall = vi.mocked(fetch).mock.calls.find(
        ([input]) => String(input) === '/api/weaviate/documents/import/source-identifiers',
      );
      expect(importCall).toBeDefined();
      const body = JSON.parse((importCall?.[1] as { body: string }).body) as {
        identifiers: string;
        supplements: Array<Record<string, unknown>>;
      };
      expect(body).toEqual({
        identifiers: 'PMID:101',
        supplements: [
          {
            identifier: 'PMID:101',
            provider_file_id: '20',
            md5: 'original-md5-20',
            converted_md5: 'converted-md5-20',
          },
          {
            identifier: 'PMID:101',
            provider_file_id: '40',
            md5: 'original-md5-40',
            converted_md5: null,
          },
        ],
      });
      for (const item of body.supplements) {
        expect(Object.keys(item).sort()).toEqual(['converted_md5', 'identifier', 'md5', 'provider_file_id']);
        expect('display_name' in item).toBe(false);
      }
    });

    it('renders Card 8 and N5 Library link for duplicate rows and paper_already_in_library listings even when collapsed', async () => {
      const user = userEvent.setup();
      let resolveRacedListing: ((payload: unknown) => void) | undefined;
      stubRoutedFetch(
        [{
          imported_count: 0,
          results: [
            {
              identifier: 'PMID:dup',
              normalized_identifier: 'PMID:dup',
              status: 'duplicate',
              message: 'This provider document has already been imported.',
              existing_document_id: 'doc-existing-1',
              filename: 'already-here.pdf',
            },
            resolvedRow('PMID:raced', 'raced-paper.pdf'),
          ],
        }],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        () => new Promise((resolve) => {
          resolveRacedListing = resolve;
        }),
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), {
        target: { value: 'PMID:dup\nPMID:raced' },
      });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));

      expect(await screen.findByText('You already have this paper in the Library')).toBeInTheDocument();
      const duplicateLink = screen.getByRole('link', { name: 'Open it in the Library to add its files.' });
      expect(duplicateLink).toHaveAttribute('href', '/weaviate/documents/doc-existing-1');

      // Only the resolved row gets "Choose supplements", never the duplicate row
      const chooseRacedButton = screen.getByRole('button', { name: 'Choose supplements for PMID:raced' });
      expect(screen.getAllByRole('button', { name: /^Choose supplements for / })).toHaveLength(1);

      // Expand to start the listing, then collapse BEFORE the listing settles
      await user.click(chooseRacedButton);
      expect(screen.getByText('Loading this paper\'s files...')).toBeInTheDocument();
      await user.click(chooseRacedButton);
      expect(screen.queryByText('Loading this paper\'s files...')).not.toBeInTheDocument();

      // Listing settles with paper_already_in_library while the row is collapsed
      await act(async () => {
        resolveRacedListing?.({
          ...fileListingFixture,
          existing_document_id: 'doc-existing-2',
          files: fileListingFixture.files.map((file) => ({
            ...file,
            tickable: false,
            not_tickable_reason: 'paper_already_in_library',
          })),
        });
      });

      // Parent row shows Card 8 + N5, Existing document (not Ready to import), and enabled Library button
      const allLibraryLinks = await screen.findAllByRole('link', { name: 'Open it in the Library to add its files.' });
      expect(allLibraryLinks.map((link) => link.getAttribute('href'))).toEqual([
        '/weaviate/documents/doc-existing-1',
        '/weaviate/documents/doc-existing-2',
      ]);
      expect(screen.queryByText('Ready to import')).not.toBeInTheDocument();
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Choose supplements for / })).not.toBeInTheDocument();

      const racedLibraryButton = screen.getByRole('button', { name: 'View raced-paper.pdf in Library' });
      expect(racedLibraryButton).toBeEnabled();
      await user.click(racedLibraryButton);
      expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents/doc-existing-2');
    });

    it('renders N6 and Card 38 Try again on listing failure while keeping main-paper import enabled', async () => {
      const user = userEvent.setup();
      let listingAttempts = 0;
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [resolvedRow('PMID:101', 'paper-101.pdf')],
          },
          defaultImportResponse,
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        () => {
          listingAttempts += 1;
          if (listingAttempts === 1) {
            return new Response(JSON.stringify({ detail: { error: 'file_listing_failed' } }), {
              status: 500,
              headers: { 'Content-Type': 'application/json' },
            });
          }
          if (listingAttempts === 2) {
            return new Response(
              JSON.stringify({ detail: { error: 'document_source_curator_token_unavailable' } }),
              {
                status: 409,
                headers: { 'Content-Type': 'application/json' },
              },
            );
          }
          return buildIdentifierListingFromFixture();
        },
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:101' } });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));

      expect(await screen.findByText(
        'Could not list this paper\'s files. You can import the main paper now and add supplements later from the Library.',
      )).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Resolve and Import' })).toBeEnabled();

      // Clicking Try again surfaces Card 9 sign-in refusal cleanly when the backend answers 409
      await user.click(screen.getByRole('button', { name: 'Try again' }));
      expect(await screen.findByText(
        'Sign in again to see this paper\'s files in ABC Literature.',
      )).toBeInTheDocument();
      expect(listingAttempts).toBe(2);

      // Clicking Try again once more refetches and succeeds
      await user.click(screen.getByRole('button', { name: 'Try again' }));
      expect(await screen.findByRole('checkbox', { name: 'Made_up_S20' })).toBeInTheDocument();
      expect(listingAttempts).toBe(3);

      // Main-only import still works with 0 supplements ticked
      await user.click(screen.getByRole('button', { name: 'Resolve and Import' }));
      await screen.findByText('paper-from-api.pdf');
    });

    it('renders Card 41 texts for 422 file_not_tickable and paper_file_limit with Alliance and non-ABC provider labels', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [resolvedRow('GA:101', 'ga-paper.pdf')],
          },
          new Response(JSON.stringify({ detail: { code: 'file_not_tickable' } }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          }),
          new Response(JSON.stringify({ detail: { code: 'paper_file_limit', limit_value: 20 } }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          }),
          new Response(JSON.stringify({ detail: { code: 'paper_file_limit' } }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          }),
          {
            imported_count: 0,
            results: [
              {
                identifier: 'GA:101',
                normalized_identifier: 'GA:101',
                status: 'error',
                error_code: 'file_not_tickable',
                message: 'Backend fallback text.',
              },
            ],
          },
        ],
        nonAbcProviderConfig,
        emptyPdfJobsResponse,
        () => buildIdentifierListingFromFixture(),
      );
      render(<AddLiteraturePage />);

      await screen.findByText('Use Genome Archive identifiers.');
      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'GA:101' } });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for GA:101' }));

      // Non-ABC provider label appears in N3 (never hardcoded ABC Literature)
      expect(await screen.findByText(
        'Not converted by Genome Archive yet. If you choose it, the import asks Genome Archive to convert it; the main paper does not wait for it.',
      )).toBeInTheDocument();
      expect(screen.queryByText(/ABC Literature/)).not.toBeInTheDocument();

      await user.click(screen.getByRole('checkbox', { name: 'Made_up_S40' }));
      const importButton = screen.getByRole('button', { name: 'Import with 1 supplement' });

      // 422 file_not_tickable uses configured short_label ('GA') and preserves the table/selection
      await user.click(importButton);
      expect(await screen.findByText(
        'This file can no longer be chosen: GA has not converted it and this import will not ask GA to.',
      )).toBeInTheDocument();
      expect(screen.getByRole('checkbox', { name: 'Made_up_S40' })).toBeChecked();

      // 422 paper_file_limit with limit_value: 20
      await user.click(screen.getByRole('button', { name: 'Import with 1 supplement' }));
      expect(await screen.findByText(
        'Too many files for one paper: at most 20, including the main paper.',
      )).toBeInTheDocument();

      // 422 paper_file_limit without limit_value falls back cleanly to N29
      await user.click(screen.getByRole('button', { name: 'Import with 1 supplement' }));
      expect(await screen.findByText('This screen cannot describe this file yet.')).toBeInTheDocument();

      // Per-row 200 OK file_not_tickable also renders Card 41 with configured short_label ('GA')
      await user.click(screen.getByRole('button', { name: 'Import with 1 supplement' }));
      expect(await screen.findByText(
        'This file can no longer be chosen: GA has not converted it and this import will not ask GA to.',
      )).toBeInTheDocument();
    });

    it('renders Card 41 422 file_not_tickable with Alliance short_label ABC', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [resolvedRow('PMID:101', 'paper-101.pdf')],
          },
          new Response(JSON.stringify({ detail: { code: 'file_not_tickable' } }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          }),
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        () => buildIdentifierListingFromFixture(),
      );
      render(<AddLiteraturePage />);

      await screen.findByText('Use configured literature identifiers.');
      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:101' } });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));
      await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S40' }));
      await user.click(screen.getByRole('button', { name: 'Import with 1 supplement' }));

      expect(await screen.findByText(
        'This file can no longer be chosen: ABC has not converted it and this import will not ask ABC to.',
      )).toBeInTheDocument();
    });

    it('clears stale supplement selections when identifiers are edited', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [resolvedRow('PMID:101', 'paper-101.pdf')],
          },
          defaultImportResponse,
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        () => buildIdentifierListingFromFixture(),
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:101' } });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));
      await user.click(await screen.findByRole('checkbox', { name: 'Made_up_S20' }));
      expect(screen.getByRole('button', { name: 'Import with 1 supplement' })).toBeInTheDocument();

      // Edit identifiers box: stale selection clears and button reverts
      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:999' } });
      const importButton = screen.getByRole('button', { name: 'Resolve and Import' });
      expect(importButton).toBeInTheDocument();

      await user.click(importButton);
      await screen.findByText('paper-from-api.pdf');
      expect(fetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/import/source-identifiers',
        expect.objectContaining({
          body: JSON.stringify({ identifiers: 'PMID:999' }),
        }),
      );
    });

    it('preserves the refused row choices on a 2-paper HTTP 200 batch with per-row file_not_tickable', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [
              resolvedRow('PMID:101', 'paper-101.pdf'),
              resolvedRow('PMID:202', 'paper-202.pdf'),
            ],
          },
          {
            imported_count: 1,
            results: [
              {
                identifier: 'PMID:101',
                normalized_identifier: 'PMID:101',
                status: 'imported',
                message: 'Import queued for background processing.',
                document_id: 'doc-101',
                job_id: 'job-101',
                filename: 'paper-101.pdf',
              },
              {
                identifier: 'PMID:202',
                normalized_identifier: 'PMID:202',
                status: 'error',
                error_code: 'file_not_tickable',
                message: 'Backend text.',
                filename: 'paper-202.pdf',
              },
            ],
          },
          {
            imported_count: 1,
            results: [
              {
                identifier: 'PMID:101',
                normalized_identifier: 'PMID:101',
                status: 'duplicate',
                message: 'Already imported.',
                existing_document_id: 'doc-101',
                filename: 'paper-101.pdf',
              },
              {
                identifier: 'PMID:202',
                normalized_identifier: 'PMID:202',
                status: 'imported',
                message: 'Import queued for background processing.',
                document_id: 'doc-202',
                job_id: 'job-202',
                filename: 'paper-202.pdf',
              },
            ],
          },
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        () => buildIdentifierListingFromFixture(),
      );
      render(<AddLiteraturePage />);

      await screen.findByText('Use configured literature identifiers.');
      fireEvent.change(screen.getByLabelText('Source identifiers'), {
        target: { value: 'PMID:101\nPMID:202' },
      });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));

      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));
      const list101 = await screen.findByRole('list', { name: 'Files for PMID:101' });
      await user.click(within(list101).getByRole('checkbox', { name: 'Made_up_S20' }));

      await user.click(screen.getByRole('button', { name: 'Choose supplements for PMID:202' }));
      const list202 = await screen.findByRole('list', { name: 'Files for PMID:202' });
      await user.click(within(list202).getByRole('checkbox', { name: 'Made_up_S20' }));
      await user.click(within(list202).getByRole('checkbox', { name: 'Made_up_S40' }));

      await user.click(screen.getByRole('button', { name: 'Import with 3 supplements' }));

      // PMID:101 imported and cleared; PMID:202 refused with Card 41 and keeps its 2 ticked choices editable
      expect(await screen.findByText(
        'This file can no longer be chosen: ABC has not converted it and this import will not ask ABC to.',
      )).toBeInTheDocument();
      expect(screen.queryByRole('list', { name: 'Files for PMID:101' })).not.toBeInTheDocument();
      const preservedList202 = screen.getByRole('list', { name: 'Files for PMID:202' });
      expect(within(preservedList202).getByRole('checkbox', { name: 'Made_up_S20' })).toBeChecked();
      expect(within(preservedList202).getByRole('checkbox', { name: 'Made_up_S40' })).toBeChecked();
      expect(screen.getByRole('button', { name: 'Import with 2 supplements' })).toBeInTheDocument();

      // Untick the unconverted file on PMID:202 and re-import with only PMID:202's remaining supplement
      await user.click(within(preservedList202).getByRole('checkbox', { name: 'Made_up_S40' }));
      await user.click(screen.getByRole('button', { name: 'Import with 1 supplement' }));
      await screen.findByText('Job job-202');

      const importCalls = vi.mocked(fetch).mock.calls.filter(
        ([input]) => String(input) === '/api/weaviate/documents/import/source-identifiers',
      );
      expect(importCalls).toHaveLength(2);
      const retryBody = JSON.parse((importCalls[1]?.[1] as { body: string }).body) as {
        identifiers: string;
        supplements: Array<Record<string, unknown>>;
      };
      expect(retryBody).toEqual({
        identifiers: 'PMID:101\nPMID:202',
        supplements: [
          {
            identifier: 'PMID:202',
            provider_file_id: '20',
            md5: 'original-md5-20',
            converted_md5: 'converted-md5-20',
          },
        ],
      });
    });

    it('lets an in-flight file listing complete cleanly after a 422 import refusal without wedging in loading', async () => {
      const user = userEvent.setup();
      let resolveListing202: ((payload: unknown) => void) | undefined;
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [
              resolvedRow('PMID:101', 'paper-101.pdf'),
              resolvedRow('PMID:202', 'paper-202.pdf'),
            ],
          },
          new Response(JSON.stringify({ detail: { code: 'paper_file_limit', limit_value: 20 } }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          }),
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        (identifier) => {
          if (identifier === 'PMID:202') {
            return new Promise((resolve) => {
              resolveListing202 = resolve;
            });
          }
          return buildIdentifierListingFromFixture();
        },
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), {
        target: { value: 'PMID:101\nPMID:202' },
      });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));

      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));
      const list101 = await screen.findByRole('list', { name: 'Files for PMID:101' });
      await user.click(within(list101).getByRole('checkbox', { name: 'Made_up_S20' }));

      // Start listing PMID:202 (remains in-flight) then trigger an import that fails with 422
      await user.click(screen.getByRole('button', { name: 'Choose supplements for PMID:202' }));
      expect(screen.getByText('Loading this paper\'s files...')).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Import with 1 supplement' }));
      expect(await screen.findByText(
        'Too many files for one paper: at most 20, including the main paper.',
      )).toBeInTheDocument();

      // Now PMID:202's listing completes and transitions out of loading cleanly
      await act(async () => {
        resolveListing202?.(buildIdentifierListingFromFixture());
      });
      expect(await screen.findByRole('list', { name: 'Files for PMID:202' })).toBeInTheDocument();
      expect(screen.queryByText('Loading this paper\'s files...')).not.toBeInTheDocument();
    });

    it('handles document_source_import_disabled 503 on both listing (no N6) and POST (preserves results and selections)', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [
              resolvedRow('PMID:101', 'paper-101.pdf'),
              resolvedRow('PMID:202', 'paper-202.pdf'),
            ],
          },
          new Response(JSON.stringify({ detail: 'Document-source import is disabled' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          }),
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        (identifier) => {
          if (identifier === 'PMID:202') {
            return new Response(
              JSON.stringify({ detail: { error: 'document_source_import_disabled' } }),
              {
                status: 503,
                headers: { 'Content-Type': 'application/json' },
              },
            );
          }
          return buildIdentifierListingFromFixture();
        },
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), {
        target: { value: 'PMID:101\nPMID:202' },
      });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));

      // Listing 503 document_source_import_disabled suppresses the panel & Choose supplements (NEVER N6)
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:202' }));
      await act(async () => undefined);
      expect(screen.queryByText(
        'Could not list this paper\'s files. You can import the main paper now and add supplements later from the Library.',
      )).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Choose supplements for PMID:202' })).not.toBeInTheDocument();

      // Tick a supplement on PMID:101, then hit plain-string 503 disabled on POST
      await user.click(screen.getByRole('button', { name: 'Choose supplements for PMID:101' }));
      const list101 = await screen.findByRole('list', { name: 'Files for PMID:101' });
      await user.click(within(list101).getByRole('checkbox', { name: 'Made_up_S20' }));
      await user.click(screen.getByRole('button', { name: 'Import with 1 supplement' }));

      expect(await screen.findByText('Document-source import is disabled')).toBeInTheDocument();
      // Resolved results and ticked supplement remain intact
      expect(screen.getByText('paper-101.pdf')).toBeInTheDocument();
      expect(within(screen.getByRole('list', { name: 'Files for PMID:101' })).getByRole('checkbox', { name: 'Made_up_S20' })).toBeChecked();
    });

    it('renders paper_not_importable for empty non-ready listings and omits "no other files" when hidden files exist', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [{
          imported_count: 0,
          results: [
            resolvedRow('PMID:missing', 'missing-source.pdf'),
            resolvedRow('PMID:hidden', 'hidden-only.pdf'),
            resolvedRow('PMID:mainonly', 'main-only.pdf'),
          ],
        }],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        (identifier) => {
          if (identifier === 'PMID:missing') {
            return {
              ...fileListingFixture,
              main_status: 'no_source_artifact',
              not_shown_count: 0,
              duplicate_copies_count: 0,
              files: [],
            };
          }
          if (identifier === 'PMID:hidden') {
            return {
              ...fileListingFixture,
              main_status: 'ready',
              not_shown_count: 2,
              duplicate_copies_count: 0,
              files: [
                {
                  ...fileListingFixture.files[0],
                  in_this_paper: false,
                  tickable: true,
                  not_tickable_reason: null,
                },
              ],
            };
          }
          return {
            ...fileListingFixture,
            main_status: 'ready',
            not_shown_count: 0,
            duplicate_copies_count: 0,
            files: [
              {
                ...fileListingFixture.files[0],
                in_this_paper: false,
                tickable: true,
                not_tickable_reason: null,
              },
            ],
          };
        },
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), {
        target: { value: 'PMID:missing\nPMID:hidden\nPMID:mainonly' },
      });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));

      // Empty listing with main_status: no_source_artifact shows paper_not_importable
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:missing' }));
      expect(await screen.findByText('This paper cannot be imported from ABC Literature')).toBeInTheDocument();
      expect(screen.queryByText('ABC Literature has no other files for this paper.')).not.toBeInTheDocument();

      // Main-only listing with not_shown_count: 2 shows hidden count, NOT "has no other files"
      await user.click(screen.getByRole('button', { name: 'Choose supplements for PMID:hidden' }));
      expect(await screen.findByText(
        '2 more files in ABC Literature are not listed because you cannot open them.',
      )).toBeInTheDocument();
      expect(screen.queryByText('ABC Literature has no other files for this paper.')).not.toBeInTheDocument();

      // Main-only listing with zero hidden and zero duplicate copies shows "has no other files"
      await user.click(screen.getByRole('button', { name: 'Choose supplements for PMID:mainonly' }));
      expect(await screen.findByText('ABC Literature has no other files for this paper.')).toBeInTheDocument();
    });

    it('sends raw non-canonical identifiers across multi-paper supplement selections', async () => {
      const user = userEvent.setup();
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [
              resolvedRow('23970418', 'paper-raw.pdf', 'PMID:23970418'),
              resolvedRow('PMID:202', 'paper-202.pdf', 'PMID:202'),
            ],
          },
          defaultImportResponse,
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        () => buildIdentifierListingFromFixture(),
      );
      render(<AddLiteraturePage />);

      fireEvent.change(screen.getByLabelText('Source identifiers'), {
        target: { value: '23970418\nPMID:202' },
      });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));

      await user.click(await screen.findByRole('button', { name: 'Choose supplements for 23970418' }));
      const rawList = await screen.findByRole('list', { name: 'Files for 23970418' });
      await user.click(within(rawList).getByRole('checkbox', { name: 'Made_up_S20' }));

      await user.click(screen.getByRole('button', { name: 'Choose supplements for PMID:202' }));
      const secondList = await screen.findByRole('list', { name: 'Files for PMID:202' });
      await user.click(within(secondList).getByRole('checkbox', { name: 'Made_up_S40' }));

      await user.click(screen.getByRole('button', { name: 'Import with 2 supplements' }));
      await screen.findByText('paper-from-api.pdf');

      const importCall = vi.mocked(fetch).mock.calls.find(
        ([input]) => String(input) === '/api/weaviate/documents/import/source-identifiers',
      );
      const body = JSON.parse((importCall?.[1] as { body: string }).body) as {
        identifiers: string;
        supplements: Array<Record<string, unknown>>;
      };
      expect(body).toEqual({
        identifiers: '23970418\nPMID:202',
        supplements: [
          {
            identifier: '23970418',
            provider_file_id: '20',
            md5: 'original-md5-20',
            converted_md5: 'converted-md5-20',
          },
          {
            identifier: 'PMID:202',
            provider_file_id: '40',
            md5: 'original-md5-40',
            converted_md5: null,
          },
        ],
      });
    });

    it('aborts and ignores stale in-flight listings on identifier edit, re-resolve, and tab switch', async () => {
      const user = userEvent.setup();
      const capturedSignals: AbortSignal[] = [];
      const resolvers: Array<(payload: unknown) => void> = [];
      stubRoutedFetch(
        [
          {
            imported_count: 0,
            results: [resolvedRow('PMID:101', 'paper-101.pdf')],
          },
          {
            imported_count: 0,
            results: [resolvedRow('PMID:101', 'paper-101.pdf')],
          },
          {
            imported_count: 0,
            results: [resolvedRow('PMID:101', 'paper-101.pdf')],
          },
        ],
        allianceProviderConfig,
        emptyPdfJobsResponse,
        (_identifier, init) => {
          if (init?.signal) {
            capturedSignals.push(init.signal);
          }
          return new Promise((resolve) => {
            resolvers.push(resolve);
          });
        },
      );
      render(<AddLiteraturePage />);

      // 1) Re-resolve aborts and ignores prior in-flight listing
      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:101' } });
      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));
      expect(capturedSignals[0]?.aborted).toBe(false);

      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      expect(capturedSignals[0]?.aborted).toBe(true);
      await act(async () => {
        resolvers[0]?.(buildIdentifierListingFromFixture());
      });
      expect(screen.queryByRole('list', { name: 'Files for PMID:101' })).not.toBeInTheDocument();

      // 2) Identifier edit aborts and ignores in-flight listing
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));
      expect(capturedSignals[1]?.aborted).toBe(false);

      fireEvent.change(screen.getByLabelText('Source identifiers'), { target: { value: 'PMID:101 ' } });
      expect(capturedSignals[1]?.aborted).toBe(true);
      await act(async () => {
        resolvers[1]?.(buildIdentifierListingFromFixture());
      });
      expect(screen.queryByRole('list', { name: 'Files for PMID:101' })).not.toBeInTheDocument();

      // 3) Mode/tab switch aborts and ignores in-flight listing
      await user.click(screen.getByRole('button', { name: 'Resolve' }));
      await user.click(await screen.findByRole('button', { name: 'Choose supplements for PMID:101' }));
      expect(capturedSignals[2]?.aborted).toBe(false);

      await user.click(screen.getByRole('tab', { name: /Upload PDFs/i }));
      expect(capturedSignals[2]?.aborted).toBe(true);
      await act(async () => {
        resolvers[2]?.(buildIdentifierListingFromFixture());
      });
      expect(screen.queryByRole('list', { name: 'Files for PMID:101' })).not.toBeInTheDocument();
    });
  });
});

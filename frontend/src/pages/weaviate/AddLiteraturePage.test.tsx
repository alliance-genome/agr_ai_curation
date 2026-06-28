import { act } from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, screen } from '../../test/test-utils';
import { render, userEvent } from '../../test/test-utils';
import { uploadPdfDocument } from '@/features/documents/pdfUploadFlow';
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
  MAX_UPLOAD_FILES_PER_SELECTION: 10,
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

  constructor(public readonly url: string) {}

  close = vi.fn();
}

const stubRoutedFetch = (
  identifierResponses: Array<unknown | Response | Promise<Response>> = [defaultImportResponse],
) => {
  const responses = [...identifierResponses];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/weaviate/pdf-jobs')) {
      return okJson(emptyPdfJobsResponse);
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
    expect(screen.getByRole('table', { name: 'Literature import results' })).toBeInTheDocument();
    expect(screen.getByText('No import results yet.')).toBeInTheDocument();
    expect(screen.queryByText(/Successful source retrievals/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Review states')).not.toBeInTheDocument();
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
    expect(screen.getByText('Provider unavailable')).toBeInTheDocument();
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
});

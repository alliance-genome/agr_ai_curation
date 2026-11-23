import { afterEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../test/test-utils';
import DocumentDetailsDialog from './DocumentDetailsDialog';
import {
  normalizeDocumentDetailResponse,
  type DocumentDetailData,
  type DocumentSummary,
} from '../../services/weaviate';

const useDocumentMock = vi.fn();

vi.mock('../../services/weaviate', async () => {
  const actual = await vi.importActual<typeof import('../../services/weaviate')>('../../services/weaviate');
  return {
    ...actual,
    useDocument: (...args: unknown[]) => useDocumentMock(...args),
  };
});

const providerDocument: DocumentDetailData = normalizeDocumentDetailResponse({
  document_id: 'doc-provider',
  job_id: 'job-provider',
  user_id: 5,
  filename: 'provider.pdf',
  title: null,
  status: 'COMPLETED',
  upload_timestamp: '2026-06-26T00:00:00Z',
  processing_started_at: '2026-06-26T00:01:00Z',
  processing_completed_at: '2026-06-26T00:02:00Z',
  file_size_bytes: 1024,
  weaviate_tenant: 'tenant-user-1',
  chunk_count: 12,
  error_message: null,
  source_provenance: {
    provider: 'archive_gateway',
    provider_metadata: {
      display_label: 'Genome Archive',
      reference_label_priority: ['external_ids.catalog', 'reference_curie'],
    },
    reference_id: 'ref-123',
    reference_curie: 'ARCHIVE:101',
    source_file_id: 'source-pdf-1',
    pdf_artifact_id: 'source-pdf-1',
    converted_artifact_id: 'converted-md-1',
    external_ids: { catalog: 'CAT-123', accession: 'ACC-456' },
    source_md5: 'abc123',
    file_class: 'converted_merged_nxml',
    file_extension: 'md',
    artifact_status: 'ready',
    import_status: 'imported',
    access_scope: 'restricted',
    access_group_ids: ['GROUP'],
    viewer_mode: 'local_pdf',
  },
});

const providerSummary: DocumentSummary = {
  ...providerDocument.document,
  embeddingStatus: 'completed',
  vectorCount: 12,
};

describe('DocumentDetailsDialog', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('refetches the Files section with "Refresh details"', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: { error: 'file_listing_failed' } }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    useDocumentMock.mockReturnValue({
      data: providerDocument,
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<DocumentDetailsDialog open documentId="doc-provider" onClose={vi.fn()} />);
    const calls = (url: string) => fetchMock.mock.calls.filter((call) => String((call as unknown[])[0]) === url).length;
    const paperFiles = '/api/pdf-viewer/documents/doc-provider/files';
    const sourceFiles = '/api/weaviate/documents/doc-provider/source-files';
    await waitFor(() => {
      expect(calls(paperFiles)).toBe(1);
      expect(calls(sourceFiles)).toBe(1);
    });

    await userEvent.click(screen.getByRole('button', { name: 'Refresh details' }));

    await waitFor(() => {
      expect(calls(paperFiles)).toBe(2);
      expect(calls(sourceFiles)).toBe(2);
    });
  });

  it('renders compact provider provenance without raw payload dumps', () => {
    useDocumentMock.mockReturnValue({
      data: providerDocument,
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog
        open
        documentId="doc-provider"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Genome Archive')).toBeInTheDocument();
    expect(screen.getAllByText('CATALOG: CAT-123')).toHaveLength(1);
    expect(screen.queryByText('ARCHIVE:101')).not.toBeInTheDocument();
    expect(screen.getByText('CATALOG: CAT-123 · ACCESSION: ACC-456')).toBeInTheDocument();
    expect(screen.getByText('converted-md-1')).toBeInTheDocument();
    expect(screen.getByText('restricted')).toBeInTheDocument();
    expect(screen.getByText('Access groups')).toBeInTheDocument();
    expect(screen.getByText('GROUP')).toBeInTheDocument();
    expect(screen.getByText('Chunks: 12')).toBeInTheDocument();
    expect(screen.queryByText(/^Embedding:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Vectors:/)).not.toBeInTheDocument();
    expect(screen.queryByText('Processing & Embeddings')).not.toBeInTheDocument();
    expect(screen.queryByText('Chunk Preview')).not.toBeInTheDocument();
    expect(screen.queryByText('Pipeline Stage')).not.toBeInTheDocument();
    expect(screen.queryByText('Related Documents')).not.toBeInTheDocument();
    expect(screen.queryByText('Metadata')).not.toBeInTheDocument();
    expect(screen.queryByText('conversion_request')).not.toBeInTheDocument();
  });

  it('does not relabel access groups as a missing access scope', () => {
    useDocumentMock.mockReturnValue({
      data: {
        ...providerDocument,
        document: {
          ...providerDocument.document,
          sourceProvenance: {
            ...providerDocument.document.sourceProvenance!,
            accessScope: null,
            accessGroupIds: ['GROUP'],
          },
        },
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog open documentId="doc-provider" onClose={vi.fn()} />,
    );

    expect(screen.getByText('Access').parentElement).toHaveTextContent('Access—');
    expect(screen.getByText('Access groups')).toBeInTheDocument();
    expect(screen.getByText('GROUP')).toBeInTheDocument();
  });

  it('shows the backend processing error when detail loading succeeded', () => {
    useDocumentMock.mockReturnValue({
      data: {
        ...providerDocument,
        document: {
          ...providerDocument.document,
          processingStatus: 'failed',
          errorMessage: 'Document parsing failed',
        },
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog
        open
        documentId="doc-provider"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Document parsing failed')).toBeInTheDocument();
  });

  it('disables mutating actions during a stage-specific processing status', () => {
    useDocumentMock.mockReturnValue({
      data: {
        ...providerDocument,
        document: {
          ...providerDocument.document,
          processingStatus: 'embedding',
        },
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog
        open
        documentId="doc-provider"
        onClose={vi.fn()}
        onDelete={vi.fn()}
        onReembed={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Re-embed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
  });

  it('renders ordinary uploaded documents as local PDF provenance', () => {
    useDocumentMock.mockReturnValue({
      data: {
        ...providerDocument,
        document: {
          ...providerDocument.document,
          id: 'doc-local',
          filename: 'local.pdf',
          sourceProvenance: null,
        },
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog
        open
        documentId="doc-local"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Local PDF')).toBeInTheDocument();
    expect(screen.getByText('Uploaded PDF')).toBeInTheDocument();
    expect(screen.queryByText('local_pdf')).not.toBeInTheDocument();
  });

  it('renders sparse provider provenance as a provider import', () => {
    useDocumentMock.mockReturnValue({
      data: {
        ...providerDocument,
        document: {
          ...providerDocument.document,
          id: 'doc-sparse',
          filename: 'sparse.pdf',
          sourceProvenance: {
            provider: 'mock_literature',
            referenceId: null,
            referenceCurie: null,
            sourceFileId: null,
            pdfArtifactId: null,
            convertedArtifactId: null,
            externalIds: null,
            sourceMd5: null,
            fileClass: null,
            fileExtension: null,
            artifactStatus: null,
            importStatus: null,
            importedAt: null,
            accessScope: null,
            accessGroupIds: null,
            viewerMode: null,
          },
        },
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog
        open
        documentId="doc-sparse"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('mock_literature')).toBeInTheDocument();
    expect(screen.getByText('Provider presentation metadata missing')).toBeInTheDocument();
  });

  it('does not show stale summary provenance after loaded detail returns null provenance', () => {
    useDocumentMock.mockReturnValue({
      data: {
        ...providerDocument,
        document: {
          ...providerDocument.document,
          id: 'doc-null',
          filename: 'null.pdf',
          sourceProvenance: null,
        },
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog
        open
        documentId="doc-null"
        documentSummary={providerSummary}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Local PDF')).toBeInTheDocument();
    expect(screen.getByText('Uploaded PDF')).toBeInTheDocument();
    expect(screen.queryByText('Genome Archive')).not.toBeInTheDocument();
  });

  it('shows a paper without a main file with no file size', () => {
    // The detail the backend returns for a paper that holds supplements only.
    const supplementsOnly = normalizeDocumentDetailResponse({
      document_id: 'doc-no-main',
      job_id: null,
      user_id: 5,
      filename: 'supplements-only-paper',
      title: null,
      status: 'COMPLETED',
      upload_timestamp: '2026-11-21T00:00:00Z',
      processing_started_at: null,
      processing_completed_at: null,
      file_size_bytes: null,
      weaviate_tenant: 'tenant-user-1',
      chunk_count: 3,
      error_message: null,
      source_provenance: null,
    });
    useDocumentMock.mockReturnValue({
      data: supplementsOnly,
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <DocumentDetailsDialog
        open
        documentId="doc-no-main"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getAllByText('supplements-only-paper').length).toBeGreaterThan(0);
    expect(screen.getByText('File Size').parentElement).toHaveTextContent('—');
  });
});

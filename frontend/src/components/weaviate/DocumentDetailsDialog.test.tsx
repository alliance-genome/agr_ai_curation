import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';
import DocumentDetailsDialog from './DocumentDetailsDialog';
import {
  normalizeDocumentDetailResponse,
  type DocumentDetailData,
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
    access_mods: { mods: ['GROUP'] },
    viewer_mode: 'local_pdf',
  },
});

describe('DocumentDetailsDialog', () => {
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
    expect(screen.getByText('mods: GROUP')).toBeInTheDocument();
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
            accessMods: null,
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
        documentSummary={providerDocument.document}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Local PDF')).toBeInTheDocument();
    expect(screen.getByText('Uploaded PDF')).toBeInTheDocument();
    expect(screen.queryByText('Genome Archive')).not.toBeInTheDocument();
  });
});

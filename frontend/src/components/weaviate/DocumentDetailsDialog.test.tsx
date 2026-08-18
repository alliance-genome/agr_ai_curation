import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';
import DocumentDetailsDialog from './DocumentDetailsDialog';
import {
  normalizeDocumentDetailResponse,
  type DocumentResponse,
} from '../../services/weaviate';

const useDocumentMock = vi.fn();

vi.mock('../../services/weaviate', async () => {
  const actual = await vi.importActual<typeof import('../../services/weaviate')>('../../services/weaviate');
  return {
    ...actual,
    useDocument: (...args: unknown[]) => useDocumentMock(...args),
  };
});

const flatDocumentResponse: DocumentResponse = {
  document_id: 'doc-provider',
  job_id: null,
  user_id: 5,
  filename: 'provider.pdf',
  title: null,
  status: 'COMPLETED',
  upload_timestamp: '2026-06-26T00:00:00Z',
  processing_started_at: null,
  processing_completed_at: null,
  file_size_bytes: 1024,
  weaviate_tenant: 'tenant-user-5',
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
    imported_at: null,
    access_scope: 'restricted',
    access_mods: { mods: ['GROUP'] },
    viewer_mode: 'local_pdf',
  },
};

const providerDocument = normalizeDocumentDetailResponse(flatDocumentResponse);

describe('DocumentDetailsDialog', () => {
  it('renders a real flat detail response with unavailable embedding metrics', () => {
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
    expect(screen.queryByText('conversion_request')).not.toBeInTheDocument();
    expect(screen.getByText('Vectors: Unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Vectors: 0')).not.toBeInTheDocument();
    expect(screen.getByText('Embedding Coverage').parentElement).toHaveTextContent('Unavailable');
    expect(screen.queryByText('Chunk Preview')).not.toBeInTheDocument();
    expect(screen.queryByText('Related Documents')).not.toBeInTheDocument();
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

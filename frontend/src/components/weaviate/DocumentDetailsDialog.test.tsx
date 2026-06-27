import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';
import DocumentDetailsDialog from './DocumentDetailsDialog';
import type { DocumentDetailData } from '../../services/weaviate';

const useDocumentMock = vi.fn();

vi.mock('../../services/weaviate', async () => {
  const actual = await vi.importActual<typeof import('../../services/weaviate')>('../../services/weaviate');
  return {
    ...actual,
    useDocument: (...args: unknown[]) => useDocumentMock(...args),
  };
});

const providerDocument: DocumentDetailData = {
  document: {
    id: 'doc-provider',
    filename: 'provider.pdf',
    title: null,
    fileSize: 1024,
    creationDate: '2026-06-26T00:00:00Z',
    lastAccessedDate: null,
    processingStatus: 'completed',
    embeddingStatus: 'completed',
    chunkCount: 12,
    vectorCount: 12,
    metadata: null,
    sourceProvenance: {
      provider: 'abc_literature',
      referenceId: 'ref-123',
      referenceCurie: 'AGRKB:101',
      sourceFileId: 'source-pdf-1',
      pdfArtifactId: 'source-pdf-1',
      convertedArtifactId: 'converted-md-1',
      externalIds: { pmid: '12345', doi: '10.5555/example' },
      sourceMd5: 'abc123',
      fileClass: 'converted_merged_nxml',
      fileExtension: 'md',
      artifactStatus: 'ready',
      importStatus: 'imported',
      importedAt: null,
      accessScope: 'restricted',
      accessMods: { mods: ['FB'] },
      viewerMode: 'local_pdf',
    },
  },
  embeddingSummary: undefined,
  pipelineStatus: undefined,
  chunksPreview: [],
  totalChunks: 12,
  relatedDocuments: [],
  schemaVersion: undefined,
};

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
    expect(screen.getByText('ABC Literature')).toBeInTheDocument();
    expect(screen.getByText('AGRKB:101')).toBeInTheDocument();
    expect(screen.getByText('PMID: 12345 · DOI: 10.5555/example')).toBeInTheDocument();
    expect(screen.getByText('converted-md-1')).toBeInTheDocument();
    expect(screen.getByText('restricted')).toBeInTheDocument();
    expect(screen.getByText('mods: FB')).toBeInTheDocument();
    expect(screen.queryByText('conversion_request')).not.toBeInTheDocument();
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

    expect(screen.getByText('Mock Literature')).toBeInTheDocument();
    expect(screen.getByText('Provider import')).toBeInTheDocument();
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
    expect(screen.queryByText('ABC Literature')).not.toBeInTheDocument();
  });
});

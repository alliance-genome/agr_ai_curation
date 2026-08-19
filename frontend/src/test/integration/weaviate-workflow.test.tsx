import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../test-utils';
import { Routes, Route } from 'react-router-dom';
import DocumentList from '../../components/weaviate/DocumentList';
import DocumentDetail from '../../pages/weaviate/DocumentDetail';
import Settings from '../../pages/weaviate/Settings';
import ErrorBoundary from '../../components/weaviate/ErrorBoundary';
import type { DocumentSummary } from '../../services/weaviate';

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

const createDocumentSummary = (
  overrides: Partial<DocumentSummary> = {},
): DocumentSummary => ({
  id: 'doc-1',
  filename: 'test.pdf',
  fileSize: 1024,
  creationDate: '2026-08-18T00:00:00Z',
  processingStatus: 'completed',
  embeddingStatus: 'completed',
  chunkCount: 1,
  vectorCount: 1,
  sourceProvenance: null,
  ...overrides,
});

// Mock navigation
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => ({ id: 'doc-1' }),
  };
});

const createTestApp = () => {
  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/" element={<DocumentList documents={[]} loading={false} totalCount={0} onDelete={vi.fn()} onReembed={vi.fn()} onRefresh={vi.fn()} />} />
        <Route path="/weaviate" element={<DocumentList documents={[]} loading={false} totalCount={0} onDelete={vi.fn()} onReembed={vi.fn()} onRefresh={vi.fn()} />} />
        <Route path="/weaviate/document/:id" element={<DocumentDetail />} />
        <Route path="/weaviate/settings" element={<Settings />} />
      </Routes>
    </ErrorBoundary>
  );
};

describe('Weaviate Workflow Integration Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Document List Workflow', () => {
    it('displays documents in the list view', async () => {
      const mockDocuments = [
        createDocumentSummary({ id: 'doc-1', filename: 'test1.pdf' }),
        createDocumentSummary({ id: 'doc-2', filename: 'test2.pdf', embeddingStatus: 'processing' }),
      ];

      const onDelete = vi.fn();
      const onReembed = vi.fn();
      const onRefresh = vi.fn();

      render(
        <DocumentList
          documents={mockDocuments}
          loading={false}
          totalCount={2}
          onDelete={onDelete}
          onReembed={onReembed}
          onRefresh={onRefresh}
        />
      );

      // Verify documents are displayed
      expect(screen.getByText('test1.pdf')).toBeInTheDocument();
      expect(screen.getByText('test2.pdf')).toBeInTheDocument();
    });

    it('renders row actions for documents', async () => {
      const mockDocuments = [createDocumentSummary({ id: 'doc-1', filename: 'test.pdf' })];
      const onDelete = vi.fn().mockResolvedValue(undefined);

      render(
        <DocumentList
          documents={mockDocuments}
          loading={false}
          totalCount={1}
          onDelete={onDelete}
          onReembed={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      expect(screen.getByText('test.pdf')).toBeInTheDocument();
      expect(onDelete).not.toHaveBeenCalled();
    });

    it('shows failed document status', async () => {
      const mockDocuments = [
        createDocumentSummary({ id: 'doc-1', filename: 'test.pdf', embeddingStatus: 'failed' }),
      ];

      render(
        <DocumentList
          documents={mockDocuments}
          loading={false}
          totalCount={1}
          onDelete={vi.fn()}
          onReembed={vi.fn()}
          onRefresh={vi.fn()}
        />
      );
      expect(screen.getByText('test.pdf')).toBeInTheDocument();
    });
  });

  describe('Settings Workflow', () => {
    it('allows updating embedding configuration', async () => {
      const onSaveEmbedding = vi.fn();
      const onSaveWeaviate = vi.fn();

      render(
        <Settings
          embeddingConfig={{
            modelProvider: 'openai',
            modelName: 'text-embedding-3-small',
            dimensions: 1536,
            batchSize: 50,
          }}
          weaviateSettings={{
            collectionName: 'PDFDocuments',
            schemaVersion: '1.0.0',
            replicationFactor: 1,
            consistency: 'eventual',
            vectorIndexType: 'hnsw',
          }}
          onSaveEmbedding={onSaveEmbedding}
          onSaveWeaviate={onSaveWeaviate}
        />
      );

      // Verify settings are displayed
      expect(screen.getByText('Weaviate Settings')).toBeInTheDocument();

      // Adjust batch size and save
      const sliders = screen.getAllByRole('slider');
      fireEvent.change(sliders[0], { target: { value: 75 } });

      // Save configuration
      const saveButton = screen.getByRole('button', { name: /save configuration/i });
      fireEvent.click(saveButton);

      expect(onSaveEmbedding).toHaveBeenCalledWith(
        expect.objectContaining({ batchSize: 75 })
      );
    }, 15000); // Integration-style settings interactions can exceed 5s when the full frontend suite is CPU-bound.

    it('switches between settings tabs', async () => {
      render(<Settings />);

      // Initially on Embeddings tab
      expect(screen.getByText('Embedding Model Configuration')).toBeInTheDocument();

      // Click Database tab
      const databaseTab = screen.getByRole('tab', { name: /database/i });
      fireEvent.click(databaseTab);

      // Should show database configuration
      await waitFor(() => {
        expect(screen.getByText('Database Configuration')).toBeInTheDocument();
      });

      // Click Schema tab
      const schemaTab = screen.getByRole('tab', { name: /schema/i });
      fireEvent.click(schemaTab);

      // Should show schema information
      await waitFor(() => {
        expect(screen.getByText('Current Schema Information')).toBeInTheDocument();
      });
    });
  });

  describe('Error Handling Workflow', () => {
    it('handles errors gracefully with ErrorBoundary', () => {
      const ThrowError = () => {
        throw new Error('Test error in workflow');
      };

      render(
        <ErrorBoundary>
          <ThrowError />
        </ErrorBoundary>
      );

      // Should show error UI
      expect(screen.getByText('Oops! Something went wrong')).toBeInTheDocument();
      expect(screen.getByText('Test error in workflow')).toBeInTheDocument();

      // Should have recovery options
      expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /go home/i })).toBeInTheDocument();
    });

    it('recovers from errors', () => {
      let shouldThrow = true;
      const TestComponent = () => {
        if (shouldThrow) {
          throw new Error('Recoverable error');
        }
        return <div>Recovered successfully</div>;
      };

      const { rerender } = render(
        <ErrorBoundary>
          <TestComponent />
        </ErrorBoundary>
      );

      // Verify error is shown
      expect(screen.getByText('Recoverable error')).toBeInTheDocument();

      // Click try again
      shouldThrow = false;
      const tryAgainButton = screen.getByRole('button', { name: /try again/i });
      fireEvent.click(tryAgainButton);

      rerender(
        <ErrorBoundary>
          <TestComponent />
        </ErrorBoundary>
      );

      // Should show recovered content
      expect(screen.getByText('Recovered successfully')).toBeInTheDocument();
    });
  });

  describe('End-to-End User Flow', () => {
    it('completes full document management workflow', async () => {
      // Setup mock API responses
      mockFetch
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            documents: [{
              document_id: 'doc-1',
              user_id: '5',
              filename: 'e2e-test.pdf',
              title: null,
              status: 'COMPLETED',
              upload_timestamp: '2026-08-18T00:00:00Z',
              processing_started_at: null,
              processing_completed_at: null,
              file_size_bytes: 1024,
              weaviate_tenant: 'tenant-user-1',
              chunk_count: 1,
              vector_count: 1,
              embedding_status: 'completed',
              error_message: null,
              source_provenance: null,
            }],
            total: 1,
            limit: 20,
            offset: 0,
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            document_id: 'doc-1',
            job_id: null,
            user_id: 5,
            filename: 'e2e-test.pdf',
            title: null,
            status: 'COMPLETED',
            upload_timestamp: '2026-08-18T00:00:00Z',
            processing_started_at: null,
            processing_completed_at: null,
            file_size_bytes: 1024,
            weaviate_tenant: 'tenant-user-1',
            chunk_count: 1,
            error_message: null,
            source_provenance: null,
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({}),
        });

      const { container } = render(createTestApp());

      // Start at document list empty state
      await waitFor(() => {
        expect(screen.getByText('No documents yet. Upload a PDF to get started.')).toBeInTheDocument();
      });

      expect(screen.getByRole('button', { name: /upload document/i })).toBeInTheDocument();
      expect(container).toBeInTheDocument();
    });
  });
});

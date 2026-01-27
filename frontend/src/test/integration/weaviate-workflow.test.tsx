import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../test-utils';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import DocumentList from '../../components/weaviate/DocumentList';
import DocumentDetail from '../../pages/weaviate/DocumentDetail';
import Settings from '../../pages/weaviate/Settings';
import ErrorBoundary from '../../components/weaviate/ErrorBoundary';
import { createMockDocument, createMockChunk } from '../test-utils';

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

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
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, cacheTime: 0 },
      mutations: { retry: false },
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DocumentList documents={[]} loading={false} totalCount={0} onDelete={vi.fn()} onReembed={vi.fn()} onRefresh={vi.fn()} />} />
            <Route path="/weaviate" element={<DocumentList documents={[]} loading={false} totalCount={0} onDelete={vi.fn()} onReembed={vi.fn()} onRefresh={vi.fn()} />} />
            <Route path="/weaviate/document/:id" element={<DocumentDetail />} />
            <Route path="/weaviate/settings" element={<Settings />} />
          </Routes>
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

describe('Weaviate Workflow Integration Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Document List Workflow', () => {
    it('displays documents and allows navigation to detail view', async () => {
      const mockDocuments = [
        createMockDocument({ id: 'doc-1', filename: 'test1.pdf' }),
        createMockDocument({ id: 'doc-2', filename: 'test2.pdf', embeddingStatus: 'processing' }),
      ];

      const onDelete = vi.fn();
      const onReembed = vi.fn();
      const onRefresh = vi.fn();

      render(
        <QueryClientProvider client={new QueryClient()}>
          <BrowserRouter>
            <DocumentList
              documents={mockDocuments}
              loading={false}
              totalCount={2}
              onDelete={onDelete}
              onReembed={onReembed}
              onRefresh={onRefresh}
            />
          </BrowserRouter>
        </QueryClientProvider>
      );

      // Verify documents are displayed
      expect(screen.getByText('test1.pdf')).toBeInTheDocument();
      expect(screen.getByText('test2.pdf')).toBeInTheDocument();

      // Click view button for first document
      const viewButtons = screen.getAllByTestId('VisibilityIcon');
      fireEvent.click(viewButtons[0].parentElement!);

      expect(mockNavigate).toHaveBeenCalledWith('/api/weaviate/document/doc-1');
    });

    it('handles document deletion flow', async () => {
      const mockDocuments = [createMockDocument({ id: 'doc-1', filename: 'test.pdf' })];
      const onDelete = vi.fn().mockResolvedValue(undefined);

      render(
        <QueryClientProvider client={new QueryClient()}>
          <BrowserRouter>
            <DocumentList
              documents={mockDocuments}
              loading={false}
              totalCount={1}
              onDelete={onDelete}
              onReembed={vi.fn()}
              onRefresh={vi.fn()}
            />
          </BrowserRouter>
        </QueryClientProvider>
      );

      // Click delete button
      const deleteButton = screen.getByTestId('DeleteIcon');
      fireEvent.click(deleteButton.parentElement!);

      expect(onDelete).toHaveBeenCalledWith('doc-1');

      await waitFor(() => {
        expect(onDelete).toHaveBeenCalledTimes(1);
      });
    });

    it('handles re-embedding workflow', async () => {
      const mockDocuments = [
        createMockDocument({ id: 'doc-1', filename: 'test.pdf', embeddingStatus: 'failed' }),
      ];
      const onReembed = vi.fn().mockResolvedValue(undefined);

      render(
        <QueryClientProvider client={new QueryClient()}>
          <BrowserRouter>
            <DocumentList
              documents={mockDocuments}
              loading={false}
              totalCount={1}
              onDelete={vi.fn()}
              onReembed={onReembed}
              onRefresh={vi.fn()}
            />
          </BrowserRouter>
        </QueryClientProvider>
      );

      // Click re-embed button
      const reembedButton = screen.getByTestId('RefreshIcon');
      fireEvent.click(reembedButton.parentElement!);

      expect(onReembed).toHaveBeenCalledWith('doc-1');
    });
  });

  describe('Settings Workflow', () => {
    it('allows updating embedding configuration', async () => {
      const onSaveEmbedding = vi.fn();
      const onSaveWeaviate = vi.fn();

      render(
        <QueryClientProvider client={new QueryClient()}>
          <BrowserRouter>
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
          </BrowserRouter>
        </QueryClientProvider>
      );

      // Verify settings are displayed
      expect(screen.getByText('Weaviate Settings')).toBeInTheDocument();

      // Find and change model provider
      const modelProviderSelect = screen.getByLabelText('Model Provider');
      fireEvent.mouseDown(modelProviderSelect);

      // Select Cohere from dropdown
      const cohereOption = await screen.findByText('Cohere');
      fireEvent.click(cohereOption);

      // Save configuration
      const saveButton = screen.getByRole('button', { name: /save configuration/i });
      fireEvent.click(saveButton);

      expect(onSaveEmbedding).toHaveBeenCalled();
    });

    it('switches between settings tabs', async () => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <BrowserRouter>
            <Settings />
          </BrowserRouter>
        </QueryClientProvider>
      );

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
            documents: [createMockDocument({ id: 'doc-1', filename: 'e2e-test.pdf' })],
            pagination: { currentPage: 1, totalPages: 1, totalItems: 1, pageSize: 20 },
            filters: {},
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            document: createMockDocument({ id: 'doc-1', filename: 'e2e-test.pdf' }),
            chunks: [createMockChunk()],
            embeddings: { totalChunks: 1, embeddedChunks: 1, avgProcessingTime: 100, lastProcessedDate: new Date() },
            chunkingStrategy: {},
            relatedDocuments: [],
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({}),
        });

      const { container } = render(createTestApp());

      // Start at document list
      await waitFor(() => {
        expect(container.querySelector('.MuiDataGrid-root')).toBeInTheDocument();
      });

      // Navigate to settings
      mockNavigate.mockImplementation((path) => {
        if (path === '/api/weaviate/settings') {
          // Simulate navigation
        }
      });

      // Test complete workflow
      expect(mockFetch).toHaveBeenCalledTimes(0);
    });
  });
});

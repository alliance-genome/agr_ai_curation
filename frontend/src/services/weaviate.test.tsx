import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fetchDocumentList,
  useDocument,
  useDocumentChunks,
  useWeaviateSettings,
  useDeleteDocument,
  useReembedDocument,
  useReprocessDocument,
  useUpdateEmbeddingSettings,
  useUpdateChunkingStrategy,
  useWeaviateHealth,
  normalizeDocumentListResponse,
  normalizeDocumentDetailResponse,
} from './weaviate';
import { logger } from './logger';
import { createMockDocument } from '../test/test-utils';

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

describe('weaviate service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('fetchDocumentList', () => {
    it('serializes the canonical query, propagates request options, and normalizes the response', async () => {
      const mockResponse = {
        documents: [{
          document_id: 'doc-1',
          user_id: 'user-1',
          filename: 'test-document.pdf',
          title: 'Test Document',
          status: 'COMPLETED',
          upload_timestamp: '2024-01-01T00:00:00Z',
          processing_started_at: null,
          processing_completed_at: null,
          file_size_bytes: 1024000,
          weaviate_tenant: 'tenant-user-1',
          chunk_count: 10,
          vector_count: 100,
          embedding_status: 'completed',
          error_message: null,
          source_provenance: null,
        }],
        total: 1,
        limit: 20,
        offset: 0,
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      });

      const controller = new AbortController();
      const result = await fetchDocumentList({
        page: 1,
        pageSize: 20,
        sortBy: 'filename',
        sortOrder: 'asc',
        search: 'test',
        embeddingStatus: ['completed', 'pending'],
        dateFrom: new Date('2026-08-01T00:00:00Z'),
        dateTo: new Date('2026-08-02T00:00:00Z'),
        minVectorCount: 0,
        maxVectorCount: 10,
      }, {
        signal: controller.signal,
        headers: { Accept: 'application/json' },
      });

      expect(result).toEqual({
        documents: [{
          id: 'doc-1',
          filename: 'test-document.pdf',
          title: 'Test Document',
          fileSize: 1024000,
          creationDate: '2024-01-01T00:00:00Z',
          lastAccessedDate: null,
          processingStatus: 'completed',
          embeddingStatus: 'completed',
          errorMessage: null,
          chunkCount: 10,
          vectorCount: 100,
          metadata: null,
          sourceProvenance: null,
        }],
        total: 1,
        limit: 20,
        offset: 0,
      });
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents?page=2&page_size=20&sort_by=filename&sort_order=asc&search=test&embedding_status=completed&embedding_status=pending&date_from=2026-08-01T00%3A00%3A00.000Z&date_to=2026-08-02T00%3A00%3A00.000Z&min_vector_count=0&max_vector_count=10',
        expect.objectContaining({
          credentials: 'include',
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
        })
      );
    });

    it('propagates shared API errors', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ message: 'Server error' }),
      });

      await expect(fetchDocumentList({ page: 0, pageSize: 20 })).rejects.toThrow('Server error');
    });

    it('propagates request cancellation without logging an expected abort as an API error', async () => {
      const abortError = new DOMException('The operation was aborted', 'AbortError');
      const loggerError = vi.spyOn(logger, 'error');
      const controller = new AbortController();
      controller.abort();
      mockFetch.mockRejectedValueOnce(abortError);

      await expect(fetchDocumentList(
        { page: 0, pageSize: 20 },
        { signal: controller.signal },
      )).rejects.toBe(abortError);
      expect(loggerError).not.toHaveBeenCalled();

      loggerError.mockRestore();
    });
  });

  describe('normalizeDocumentListResponse', () => {
    it('preserves nullable canonical list fields without display fallbacks', () => {
      expect(normalizeDocumentListResponse({
        documents: [{
          document_id: 'doc-nullable',
          user_id: 'user-1',
          filename: 'nullable.pdf',
          title: null,
          status: 'PENDING',
          upload_timestamp: null,
          processing_started_at: null,
          processing_completed_at: null,
          file_size_bytes: null,
          weaviate_tenant: 'tenant-user-1',
          chunk_count: null,
          vector_count: null,
          embedding_status: 'pending',
          error_message: null,
          source_provenance: null,
        }],
        total: 1,
        limit: 20,
        offset: 0,
      }).documents[0]).toMatchObject({
        id: 'doc-nullable',
        filename: 'nullable.pdf',
        title: null,
        fileSize: null,
        creationDate: null,
        processingStatus: 'pending',
        chunkCount: null,
        vectorCount: null,
        sourceProvenance: null,
      });
    });
  });

  describe('useDocument', () => {
    it('fetches a single document by ID', async () => {
      const mockRawResponse = {
        document: {
          id: 'doc-1',
          filename: 'mock.pdf',
          file_size: 2048,
          creation_date: '2024-01-01T00:00:00Z',
          last_accessed_date: '2024-01-02T00:00:00Z',
          processing_status: 'completed',
          embedding_status: 'completed',
          chunk_count: 10,
          vector_count: 8,
          metadata: {
            page_count: 10,
            author: 'Author',
          },
          source_provenance: {
            provider: 'abc_literature',
            provider_metadata: {
              display_label: 'ABC Literature',
              reference_label_priority: ['external_ids.fbrf', 'reference_curie'],
            },
            reference_curie: 'AGRKB:101',
            external_ids: { pmid: '12345' },
            converted_artifact_id: 'converted-md-1',
            source_md5: 'abc123',
            access_mods: { mods: ['FB'] },
            viewer_mode: 'local_pdf',
          },
        },
        chunks_preview: [
          {
            id: 'chunk-1',
            content: 'Hello world',
            chunk_index: 0,
            page_number: 1,
            element_type: 'NarrativeText',
            section_title: 'Intro',
            embedding_model: 'text-embedding-xyz',
          },
        ],
        total_chunks: 10,
        embedding_summary: {
          total_chunks: 10,
          embedded_chunks: 8,
          coverage_percentage: 80,
          last_embedded_at: '2024-01-03T00:00:00Z',
          primary_model: 'text-embedding-xyz',
          models: [{ model: 'text-embedding-xyz', chunk_count: 8 }],
        },
        pipeline_status: {
          current_stage: 'completed',
          progress_percentage: 100,
          message: 'Finished',
          updated_at: '2024-01-04T00:00:00Z',
        },
        related_documents: [
          {
            id: 'doc-2',
            filename: 'secondary.pdf',
            chunk_count: 5,
            vector_count: 5,
          },
        ],
        schema_version: '1.0.0',
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockRawResponse,
      });

      const { result } = renderHook(
        () => useDocument('doc-1'),
        { wrapper: createWrapper() }
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data).toEqual({
        document: {
          id: 'doc-1',
          filename: 'mock.pdf',
          title: null,
          fileSize: 2048,
          creationDate: '2024-01-01T00:00:00Z',
          lastAccessedDate: '2024-01-02T00:00:00Z',
          processingStatus: 'completed',
          embeddingStatus: 'completed',
          chunkCount: 10,
          vectorCount: 8,
          metadata: {
            page_count: 10,
            author: 'Author',
          },
          sourceProvenance: {
            provider: 'abc_literature',
            providerMetadata: {
              displayLabel: 'ABC Literature',
              referenceLabelPriority: ['external_ids.fbrf', 'reference_curie'],
            },
            referenceId: null,
            referenceCurie: 'AGRKB:101',
            sourceFileId: null,
            pdfArtifactId: null,
            convertedArtifactId: 'converted-md-1',
            externalIds: { pmid: '12345' },
            sourceMd5: 'abc123',
            fileClass: null,
            fileExtension: null,
            artifactStatus: null,
            importStatus: null,
            importedAt: null,
            accessScope: null,
            accessMods: { mods: ['FB'] },
            viewerMode: 'local_pdf',
          },
        },
        embeddingSummary: {
          totalChunks: 10,
          embeddedChunks: 8,
          coveragePercentage: 80,
          lastEmbeddedAt: '2024-01-03T00:00:00Z',
          primaryModel: 'text-embedding-xyz',
          models: [{ name: 'text-embedding-xyz', chunkCount: 8 }],
        },
        pipelineStatus: {
          currentStage: 'completed',
          progressPercentage: 100,
          message: 'Finished',
          startedAt: null,
          updatedAt: '2024-01-04T00:00:00Z',
          completedAt: null,
          errorCount: null,
        },
        chunksPreview: [
          {
            id: 'chunk-1',
            chunkIndex: 0,
            content: 'Hello world',
            pageNumber: 1,
            elementType: 'NarrativeText',
            sectionTitle: 'Intro',
            metadata: null,
            embeddingModel: 'text-embedding-xyz',
            embeddingTimestamp: null,
          },
        ],
        totalChunks: 10,
        relatedDocuments: [
          {
            id: 'doc-2',
            filename: 'secondary.pdf',
            title: null,
            fileSize: null,
            creationDate: null,
            lastAccessedDate: null,
            processingStatus: null,
            embeddingStatus: null,
            chunkCount: 5,
            vectorCount: 5,
            metadata: null,
            sourceProvenance: null,
          },
        ],
        schemaVersion: '1.0.0',
      });
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1',
        expect.any(Object)
      );
    });

    it('does not fetch when ID is not provided', () => {
      const { result } = renderHook(
        () => useDocument(''),
        { wrapper: createWrapper() }
      );

      expect(mockFetch).not.toHaveBeenCalled();
      expect(result.current.fetchStatus).toBe('idle');
    });

    it('normalizes flat document contract payloads', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          document_id: 'doc-flat',
          filename: 'flat.pdf',
          status: 'COMPLETED',
          upload_timestamp: '2026-06-26T00:00:00Z',
          file_size_bytes: 4096,
          chunk_count: 14,
          source_provenance: {
            provider: 'abc_literature',
            reference_id: 'ref-flat',
            external_ids: { pmid: '98765' },
            viewer_mode: 'local_pdf',
          },
        }),
      });

      const { result } = renderHook(
        () => useDocument('doc-flat'),
        { wrapper: createWrapper() }
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data?.document).toMatchObject({
        id: 'doc-flat',
        filename: 'flat.pdf',
        processingStatus: 'completed',
        fileSize: 4096,
        creationDate: '2026-06-26T00:00:00Z',
        chunkCount: 14,
        sourceProvenance: expect.objectContaining({
          provider: 'abc_literature',
          referenceId: 'ref-flat',
          externalIds: { pmid: '98765' },
          viewerMode: 'local_pdf',
        }),
      });
      expect(result.current.data?.totalChunks).toBe(14);
    });
  });

  describe('normalizeDocumentDetailResponse', () => {
    it('preserves explicit null provenance instead of falling back to stale summary provenance', () => {
      const normalized = normalizeDocumentDetailResponse(
        {
          document: {
            id: 'doc-null',
            filename: 'null.pdf',
            source_provenance: null,
          },
        },
        {
          fallbackSummary: {
            id: 'doc-null',
            filename: 'stale.pdf',
            title: null,
            fileSize: null,
            creationDate: null,
            lastAccessedDate: null,
            processingStatus: null,
            embeddingStatus: null,
            chunkCount: null,
            vectorCount: null,
            metadata: null,
            sourceProvenance: {
              provider: 'abc_literature',
              referenceId: 'stale-ref',
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
        }
      );

      expect(normalized.document.sourceProvenance).toBeNull();
    });
  });

  describe('useDeleteDocument', () => {
    it('deletes a document and invalidates queries', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

      const { result } = renderHook(
        () => useDeleteDocument(),
        { wrapper: createWrapper() }
      );

      await result.current.mutateAsync('doc-1');

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1',
        expect.objectContaining({ method: 'DELETE' })
      );
    });

    it('handles delete errors', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        json: async () => ({ message: 'Cannot delete' }),
      });

      const { result } = renderHook(
        () => useDeleteDocument(),
        { wrapper: createWrapper() }
      );

      await expect(result.current.mutateAsync('doc-1')).rejects.toThrow('Cannot delete');
    });
  });

  describe('useReembedDocument', () => {
    it('re-embeds a document', async () => {
      const mockDocument = createMockDocument();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockDocument,
      });

      const { result } = renderHook(
        () => useReembedDocument(),
        { wrapper: createWrapper() }
      );

      const response = await result.current.mutateAsync('doc-1');

      expect(response).toEqual(mockDocument);
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1/reembed',
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('useReprocessDocument', () => {
    it('reprocesses a document', async () => {
      const mockDocument = createMockDocument();

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockDocument,
      });

      const { result } = renderHook(
        () => useReprocessDocument(),
        { wrapper: createWrapper() }
      );

      const response = await result.current.mutateAsync('doc-1');

      expect(response).toEqual(mockDocument);
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1/reprocess',
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('useWeaviateSettings', () => {
    it('fetches weaviate settings', async () => {
      const mockSettings = {
        embedding: {
          modelProvider: 'openai',
          modelName: 'text-embedding-3-small',
          dimensions: 1536,
          batchSize: 50,
        },
        database: {
          collectionName: 'Documents',
          schemaVersion: '1.0.0',
          replicationFactor: 1,
          consistency: 'eventual',
          vectorIndexType: 'hnsw',
        },
        availableModels: [],
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockSettings,
      });

      const { result } = renderHook(
        () => useWeaviateSettings(),
        { wrapper: createWrapper() }
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data).toEqual(mockSettings);
    });
  });

  describe('useUpdateEmbeddingSettings', () => {
    it('updates embedding settings', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

      const { result } = renderHook(
        () => useUpdateEmbeddingSettings(),
        { wrapper: createWrapper() }
      );

      const config = {
        modelProvider: 'openai' as const,
        modelName: 'text-embedding-3-small',
        dimensions: 1536,
        batchSize: 50,
      };

      await result.current.mutateAsync(config);

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/settings/embedding',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify(config),
        })
      );
    });
  });

  describe('useWeaviateHealth', () => {
    it('checks weaviate health status', async () => {
      const mockHealth = {
        status: 'healthy',
        message: 'All systems operational',
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockHealth,
      });

      const { result } = renderHook(
        () => useWeaviateHealth(),
        { wrapper: createWrapper() }
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(result.current.data).toEqual(mockHealth);
    });

    it('includes refetch interval', () => {
      const { result } = renderHook(
        () => useWeaviateHealth(),
        { wrapper: createWrapper() }
      );

      // Check that refetchInterval is set
      expect(result.current).toHaveProperty('refetch');
    });
  });

  describe('useDocumentChunks', () => {
    it('fetches document chunks with pagination', async () => {
      const mockChunks = {
        chunks: [],
        totalCount: 0,
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockChunks,
      });

      const { result } = renderHook(
        () => useDocumentChunks('doc-1', 1, 20),
        { wrapper: createWrapper() }
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1/chunks?page=1&pageSize=20',
        expect.any(Object)
      );
    });
  });

  describe('useUpdateChunkingStrategy', () => {
    it('updates chunking strategy', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      });

      const { result } = renderHook(
        () => useUpdateChunkingStrategy(),
        { wrapper: createWrapper() }
      );

      const strategy = {
        strategyName: 'research',
        chunkingMethod: 'by_title',
        maxCharacters: 1500,
        overlapCharacters: 200,
        includeMetadata: true,
        excludeElementTypes: ['Footer', 'Header'],
      };

      await result.current.mutateAsync(strategy);

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/settings/chunking',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify(strategy),
        })
      );
    });
  });

});

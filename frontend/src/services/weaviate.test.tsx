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
  buildPdfJobsStreamUrl,
  fetchConfiguredDocumentSourcePresentation,
  fetchPdfJobs,
  importSourceIdentifiers,
  resolveSourceIdentifiers,
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

  describe('Add Literature contracts', () => {
    it('posts identifiers to the canonical endpoints and normalizes canonical status and artifact provenance', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          requested_count: 3,
          imported_count: 1,
          duplicate_count: 0,
          error_count: 2,
          results: [
            {
              identifier: 'PMID:1',
              normalized_identifier: 'PMID:1',
              status: 'imported',
              message: 'Queued.',
              document_id: 'doc-1',
              job_id: 'job-1',
              filename: 'one.pdf',
              source_provenance: {
                provider: 'abc_literature',
                viewer_mode: 'local_pdf',
                pdf_artifact_id: 'pdf-1',
                converted_artifact_id: 'markdown-1',
                source_md5: 'abc123',
                pdf_referencefile_id: 'retired-pdf',
                converted_referencefile_id: 'retired-markdown',
              },
            },
            {
              identifier: 'PMID:2',
              status: 'error',
              error_code: 'document_source_access_denied',
              message: 'Denied.',
            },
            {
              identifier: 'PMID:3',
              status: 'error',
              error_code: 'access_denied',
              message: 'Retired alias.',
              source_provenance: {
                viewer_mode: 'local_pdf',
                pdf_referencefile_id: 'retired-only',
              },
            },
          ],
        }),
      });

      const result = await importSourceIdentifiers('PMID:1\nPMID:2\nPMID:3');

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/import/source-identifiers',
        expect.objectContaining({
          method: 'POST',
          credentials: 'include',
          body: JSON.stringify({ identifiers: 'PMID:1\nPMID:2\nPMID:3' }),
        }),
      );
      expect(result).toMatchObject({
        requestedCount: 3,
        importedCount: 1,
        duplicateCount: 0,
        errorCount: 2,
      });
      expect(result.results[0]).toMatchObject({
        status: 'imported',
        source: {
          pdfArtifactId: 'pdf-1',
          convertedArtifactId: 'markdown-1',
        },
      });
      expect(result.results[1].status).toBe('access_denied');
      expect(result.results[2]).toMatchObject({ status: 'invalid', source: undefined });
    });

    it('maps every canonical document-source failure status', async () => {
      const expectedStatuses = [
        ['document_source_unavailable', 'provider_unavailable'],
        ['document_source_import_unavailable', 'provider_unavailable'],
        ['document_source_curator_token_unavailable', 'provider_unavailable'],
        ['document_source_conversion_running', 'conversion_running'],
        ['document_source_conversion_failed', 'conversion_failed'],
        ['document_source_ambiguous_match', 'needs_selection'],
        ['document_source_no_source_artifact', 'no_source_pdf'],
        ['document_source_no_converted_text', 'no_converted_text'],
      ] as const;
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          results: expectedStatuses.map(([errorCode], index) => ({
            identifier: `source-${index}`,
            status: 'error',
            error_code: errorCode,
            message: errorCode,
          })),
        }),
      });

      const result = await resolveSourceIdentifiers('sources');

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/resolve/source-identifiers',
        expect.objectContaining({ method: 'POST' }),
      );
      expect(result.results.map(({ status }) => status)).toEqual(
        expectedStatuses.map(([, status]) => status),
      );
    });

    it('surfaces FastAPI detail errors from identifier requests', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: async () => ({ detail: 'Invalid document-source import request' }),
      });

      await expect(importSourceIdentifiers('invalid')).rejects.toThrow(
        'Invalid document-source import request',
      );
    });

    it('fetches and normalizes configured provider presentation metadata', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          provider_id: 'abc_literature',
          presentation: {
            display_label: 'ABC Literature',
            identifier_help_label: 'Configured identifier help.',
            identifier_examples: ['PMID:1', 'AGRKB:2'],
          },
        }),
      });

      await expect(fetchConfiguredDocumentSourcePresentation()).resolves.toEqual({
        providerId: 'abc_literature',
        presentation: {
          displayLabel: 'ABC Literature',
          referenceLabelPriority: null,
          identifierHelpLabel: 'Configured identifier help.',
          identifierExamples: ['PMID:1', 'AGRKB:2'],
        },
      });
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/document-source/provider-presentation',
        expect.objectContaining({ credentials: 'include' }),
      );
    });

    it('builds exact configured PDF-job request and stream URLs', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ jobs: [], total: 0, limit: 4, offset: 0 }),
      });

      await fetchPdfJobs({ windowDays: 3, limit: 4, offset: 0 });

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/pdf-jobs?window_days=3&limit=4&offset=0',
        expect.any(Object),
      );
      expect(buildPdfJobsStreamUrl({ windowDays: 3, limit: 4 })).toBe(
        '/api/weaviate/pdf-jobs/stream?window_days=3&limit=4',
      );
    });
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
          processingStatus: 'completed',
          embeddingStatus: 'completed',
          errorMessage: null,
          chunkCount: 10,
          vectorCount: 100,
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
    it('fetches and normalizes the authoritative flat document response', async () => {
      const mockRawResponse = {
        document_id: 'doc-1',
        job_id: 'job-1',
        user_id: 5,
        filename: 'mock.pdf',
        title: 'Mock paper',
        status: 'COMPLETED',
        upload_timestamp: '2024-01-01T00:00:00Z',
        processing_started_at: null,
        processing_completed_at: null,
        file_size_bytes: 2048,
        weaviate_tenant: 'tenant-user-1',
        chunk_count: 10,
        error_message: null,
        source_provenance: {
          provider: 'abc_literature',
          provider_metadata: {
            display_label: 'ABC Literature',
            reference_label_priority: ['external_ids.pmid', 'reference_curie'],
          },
          reference_curie: 'AGRKB:101',
          external_ids: { pmid: '12345' },
          converted_artifact_id: 'converted-md-1',
          source_md5: 'abc123',
          access_group_ids: ['FB'],
          viewer_mode: 'local_pdf',
        },
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
          title: 'Mock paper',
          fileSize: 2048,
          creationDate: '2024-01-01T00:00:00Z',
          processingStatus: 'completed',
          errorMessage: null,
          chunkCount: 10,
          sourceProvenance: {
            provider: 'abc_literature',
            providerMetadata: {
              displayLabel: 'ABC Literature',
              referenceLabelPriority: ['external_ids.pmid', 'reference_curie'],
              identifierHelpLabel: null,
              identifierExamples: null,
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
            accessGroupIds: ['FB'],
            viewerMode: 'local_pdf',
          },
        },
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

  });

  describe('normalizeDocumentDetailResponse', () => {
    it('preserves explicit null provenance from the canonical response', () => {
      const normalized = normalizeDocumentDetailResponse({
        document_id: 'doc-null',
        job_id: null,
        user_id: 5,
        filename: 'null.pdf',
        title: null,
        status: 'PENDING',
        upload_timestamp: '2026-08-18T00:00:00Z',
        processing_started_at: null,
        processing_completed_at: null,
        file_size_bytes: 1,
        weaviate_tenant: 'tenant-user-1',
        chunk_count: null,
        error_message: null,
        source_provenance: null,
      });

      expect(normalized.document.sourceProvenance).toBeNull();
    });

    it('rejects the retired nested detail response shape', () => {
      expect(() => normalizeDocumentDetailResponse({
        document: { id: 'doc-retired', filename: 'retired.pdf' },
      } as never)).toThrow();
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

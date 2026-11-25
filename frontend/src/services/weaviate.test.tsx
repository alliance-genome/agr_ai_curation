import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  ACTIVE_PDF_JOB_STATUSES,
  AddDocumentSourceFilesError,
  ApiHttpError,
  AuthenticationError,
  addDocumentSourceFiles,
  fetchActivePdfJobsForDocument,
  fetchDocumentList,
  fetchDocumentSourceProviderConfiguration,
  fetchPdfJob,
  fetchPdfJobs,
  importSourceIdentifiers,
  isActivePdfJobStatus,
  resolveSourceIdentifiers,
  useDocument,
  useDocumentChunks,
  useWeaviateSettings,
  useDeleteDocument,
  useReembedDocument,
  useUpdateEmbeddingSettings,
  useUpdateChunkingStrategy,
  useWeaviateHealth,
  normalizeDocumentListResponse,
  normalizeDocumentDetailResponse,
  type OperationResult,
} from './weaviate';
import { logger } from './logger';

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

  describe('document-source identifier contracts', () => {
    it('posts resolve requests and normalizes canonical statuses and artifact provenance', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          imported_count: 0,
          results: [
            {
              identifier: 'SOURCE:1',
              normalized_identifier: 'SOURCE:1',
              status: 'resolved',
              message: 'Ready.',
              source_provenance: {
                provider: 'example_source',
                viewer_mode: 'local_pdf',
                pdf_artifact_id: 'pdf-1',
                converted_artifact_id: 'text-1',
                source_md5: 'abc',
              },
            },
            {
              identifier: 'SOURCE:2',
              status: 'error',
              error_code: 'document_source_access_denied',
              message: 'Denied.',
            },
          ],
        }),
      });

      await expect(resolveSourceIdentifiers('SOURCE:1\nSOURCE:2')).resolves.toEqual({
        importedCount: 0,
        results: [
          expect.objectContaining({
            identifier: 'SOURCE:1',
            status: 'resolved',
            source: {
              provider: 'example_source',
              viewerMode: 'local_pdf',
              pdfArtifactId: 'pdf-1',
              convertedArtifactId: 'text-1',
              sourceMd5: 'abc',
            },
          }),
          expect.objectContaining({ identifier: 'SOURCE:2', status: 'access_denied' }),
        ],
      });
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/resolve/source-identifiers',
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ identifiers: 'SOURCE:1\nSOURCE:2' }),
        },
      );
    });

    it('rejects malformed successful response envelopes and result contracts', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => ({ imported_count: 0 }) })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            imported_count: 0,
            results: [{ identifier: 'SOURCE:1', status: 'resolved' }],
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => { throw new SyntaxError('bad json'); },
        });

      await expect(resolveSourceIdentifiers('SOURCE:1')).rejects.toThrow(
        'Document-source response is missing required batch fields.',
      );
      await expect(resolveSourceIdentifiers('SOURCE:1')).rejects.toThrow(
        'Document-source result is missing its required message.',
      );
      await expect(resolveSourceIdentifiers('SOURCE:1')).rejects.toThrow('bad json');
    });

    it('posts imports and rejects obsolete short errors and referencefile provenance aliases', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          imported_count: 1,
          results: [
            {
              identifier: 'SOURCE:1',
              status: 'imported',
              message: 'Queued.',
              source_provenance: {
                provider: 'example_source',
                viewer_mode: 'local_pdf',
                pdf_referencefile_id: 'legacy-pdf',
                converted_referencefile_id: 'legacy-text',
              },
            },
            ...[
              'access_denied',
              'provider_unavailable',
              'conversion_running',
              'conversion_failed',
              'ambiguous_match',
              'no_source_artifact',
              'no_converted_text',
            ].map((errorCode, index) => ({
              identifier: `SOURCE:${index + 2}`,
              status: 'error',
              error_code: errorCode,
              message: 'Legacy alias.',
            })),
          ],
        }),
      });

      const result = await importSourceIdentifiers('SOURCE:1,SOURCE:2');

      expect(result.importedCount).toBe(1);
      expect(result.results[0].source).toBeUndefined();
      expect(result.results.slice(1).map(({ status }) => status)).toEqual(
        Array.from({ length: 7 }, () => 'invalid'),
      );
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/import/source-identifiers',
        expect.objectContaining({ body: JSON.stringify({ identifiers: 'SOURCE:1,SOURCE:2' }) }),
      );
    });

    it('normalizes configured provider presentation from the shared metadata endpoint', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          provider: 'example_source',
          import_enabled: true,
          presentation: {
            display_label: 'Example Source',
            identifier_help_label: 'Use example source IDs.',
            identifier_examples: ['SOURCE:1', 'SOURCE:2'],
          },
        }),
      });

      await expect(fetchDocumentSourceProviderConfiguration()).resolves.toEqual({
        provider: 'example_source',
        importEnabled: true,
        presentation: {
          displayLabel: 'Example Source',
          referenceLabelPriority: null,
          identifierHelpLabel: 'Use example source IDs.',
          identifierExamples: ['SOURCE:1', 'SOURCE:2'],
        },
      });
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/source-provider',
        expect.objectContaining({ credentials: 'include' }),
      );
    });

    it('preserves optional short_label on configured provider presentation metadata', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          provider: 'example_source',
          import_enabled: true,
          presentation: {
            display_label: 'Example Source',
            short_label: 'EXS',
          },
        }),
      });

      await expect(fetchDocumentSourceProviderConfiguration()).resolves.toEqual({
        provider: 'example_source',
        importEnabled: true,
        presentation: {
          displayLabel: 'Example Source',
          shortLabel: 'EXS',
          referenceLabelPriority: null,
        },
      });
    });

    it('posts exact supplement files to addDocumentSourceFiles and returns the accepted 202 job', async () => {
      const acceptedJob = {
        job_id: 'job-add-1',
        document_id: 'doc-1',
        user_id: 7,
        filename: 'paper.pdf',
        status: 'pending',
        progress_percentage: 0,
        cancel_requested: false,
        metadata: { job_kind: 'supplement_add' },
        created_at: '2026-11-24T12:00:00Z',
        updated_at: '2026-11-24T12:00:00Z',
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 202,
        json: async () => acceptedJob,
      });

      const controller = new AbortController();
      await expect(
        addDocumentSourceFiles(
          'doc-1',
          {
            files: [
              {
                provider_file_id: '20',
                md5: 'original-md5-20',
                converted_md5: 'converted-md5-20',
              },
              {
                provider_file_id: '22',
                md5: null,
                converted_md5: 'converted-md5-22',
              },
            ],
          },
          { signal: controller.signal },
        ),
      ).resolves.toEqual(acceptedJob);

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1/files',
        expect.objectContaining({
          method: 'POST',
          credentials: 'include',
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            files: [
              {
                provider_file_id: '20',
                md5: 'original-md5-20',
                converted_md5: 'converted-md5-20',
              },
              {
                provider_file_id: '22',
                md5: null,
                converted_md5: 'converted-md5-22',
              },
            ],
          }),
        }),
      );
    });

    it('preserves structured HTTP status, code, and limit_value on addDocumentSourceFiles refusals', async () => {
      const cases = [
        {
          status: 422,
          payload: { detail: { error: 'paper_file_limit', limit_value: 20 } },
          expected: { httpStatus: 422, code: 'paper_file_limit', limitValue: 20 },
        },
        {
          status: 409,
          payload: { detail: { error: 'import_in_progress' } },
          expected: { httpStatus: 409, code: 'import_in_progress', limitValue: null },
        },
        {
          status: 409,
          payload: { detail: { error: 'file_removal_unfinished' } },
          expected: { httpStatus: 409, code: 'file_removal_unfinished', limitValue: null },
        },
        {
          status: 409,
          payload: { detail: { error: 'document_source_curator_token_unavailable' } },
          expected: { httpStatus: 409, code: 'document_source_curator_token_unavailable', limitValue: null },
        },
        {
          status: 503,
          payload: { detail: { error: 'document_source_import_disabled' } },
          expected: { httpStatus: 503, code: 'document_source_import_disabled', limitValue: null },
        },
        {
          status: 503,
          payload: {
            detail: {
              error: 'document_source_curator_token_unavailable',
              message: 'Document-source authentication is unavailable.',
            },
          },
          expected: { httpStatus: 503, code: 'document_source_curator_token_unavailable', limitValue: null },
        },
      ];

      for (const testCase of cases) {
        mockFetch.mockResolvedValueOnce({
          ok: false,
          status: testCase.status,
          json: async () => testCase.payload,
        });

        let caught: unknown = null;
        try {
          await addDocumentSourceFiles('doc-1', {
            files: [{ provider_file_id: '20', md5: 'm1', converted_md5: 'c1' }],
          });
        } catch (error) {
          caught = error;
        }
        expect(caught).toBeInstanceOf(AddDocumentSourceFilesError);
        const refusal = caught as AddDocumentSourceFilesError;
        expect(refusal.httpStatus).toBe(testCase.expected.httpStatus);
        expect(refusal.code).toBe(testCase.expected.code);
        expect(refusal.limitValue).toBe(testCase.expected.limitValue);
      }
    });

    it('raises AuthenticationError aligned with fetchApi on 401 and rejects malformed 202 job payloads from addDocumentSourceFiles', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({ detail: 'Unauthorized' }) })
        .mockResolvedValueOnce({ ok: true, status: 202, json: async () => ({ unexpected: true }) });

      await expect(
        addDocumentSourceFiles('doc-1', {
          files: [{ provider_file_id: '20', md5: 'm1', converted_md5: 'c1' }],
        }),
      ).rejects.toThrow(new AuthenticationError('Session expired - redirecting to login'));
      await expect(
        addDocumentSourceFiles('doc-1', {
          files: [{ provider_file_id: '20', md5: 'm1', converted_md5: 'c1' }],
        }),
      ).rejects.toThrow('Add files response is missing required job fields.');
    });

    it('paginates across all active jobs in fetchActivePdfJobsForDocument, preserves ApiHttpError status on fetchPdfJob, and forwards AbortSignal', async () => {
      expect(ACTIVE_PDF_JOB_STATUSES).toEqual(['pending', 'running', 'cancel_requested']);
      expect(isActivePdfJobStatus('pending')).toBe(true);
      expect(isActivePdfJobStatus('running')).toBe(true);
      expect(isActivePdfJobStatus('cancel_requested')).toBe(true);
      expect(isActivePdfJobStatus('completed')).toBe(false);
      expect(isActivePdfJobStatus('failed')).toBe(false);
      expect(isActivePdfJobStatus('cancelled')).toBe(false);

      const otherJob = {
        job_id: 'job-other',
        document_id: 'doc-b',
        user_id: 7,
        status: 'running',
        progress_percentage: 20,
        cancel_requested: false,
        created_at: '2026-11-24T12:00:00Z',
        updated_at: '2026-11-24T12:01:00Z',
      };
      const targetJobOnPage2 = {
        ...otherJob,
        job_id: 'job-target-page2',
        document_id: 'doc-a',
      };
      mockFetch
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ jobs: [otherJob], total: 2, limit: 1, offset: 0 }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ jobs: [targetJobOnPage2], total: 2, limit: 1, offset: 1 }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => targetJobOnPage2,
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 404,
          json: async () => ({ detail: 'PDF job missing not found' }),
        });

      const controller = new AbortController();
      const activeForDocA = await fetchActivePdfJobsForDocument('doc-a', {
        pageSize: 1,
        signal: controller.signal,
      });
      expect(activeForDocA).toEqual([targetJobOnPage2]);
      expect(mockFetch).toHaveBeenNthCalledWith(
        1,
        '/api/weaviate/pdf-jobs?status=pending&status=running&status=cancel_requested&document_id=doc-a&limit=1',
        expect.objectContaining({ signal: controller.signal }),
      );
      expect(mockFetch).toHaveBeenNthCalledWith(
        2,
        '/api/weaviate/pdf-jobs?status=pending&status=running&status=cancel_requested&document_id=doc-a&limit=1&offset=1',
        expect.objectContaining({ signal: controller.signal }),
      );

      await expect(fetchPdfJob('job-target-page2', { signal: controller.signal })).resolves.toEqual(
        targetJobOnPage2,
      );
      expect(mockFetch).toHaveBeenNthCalledWith(
        3,
        '/api/weaviate/pdf-jobs/job-target-page2',
        expect.objectContaining({ signal: controller.signal }),
      );

      let caught: unknown = null;
      try {
        await fetchPdfJob('missing');
      } catch (error) {
        caught = error;
      }
      expect(caught).toBeInstanceOf(ApiHttpError);
      expect((caught as ApiHttpError).httpStatus).toBe(404);

      // fetchPdfJobs forwards document_id and preserves the server payload's total.
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ jobs: [otherJob], total: 75, limit: 50, offset: 0 }),
      });
      const rawPage = await fetchPdfJobs({ status: ['running'], documentId: 'doc-b', limit: 50 });
      expect(mockFetch).toHaveBeenLastCalledWith(
        '/api/weaviate/pdf-jobs?status=running&document_id=doc-b&limit=50',
        expect.any(Object),
      );
      expect(rawPage.total).toBe(75);
      expect(rawPage.jobs).toEqual([otherJob]);
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

    it('preserves ready_file_count when returned on list items', () => {
      const normalized = normalizeDocumentListResponse({
        documents: [{
          document_id: 'doc-files',
          user_id: 'user-1',
          filename: 'with-supplements.pdf',
          title: 'With Supplements',
          status: 'COMPLETED',
          upload_timestamp: '2026-11-24T00:00:00Z',
          processing_started_at: null,
          processing_completed_at: null,
          file_size_bytes: 1024,
          weaviate_tenant: 'tenant-user-1',
          chunk_count: 12,
          vector_count: 12,
          ready_file_count: 3,
          embedding_status: 'completed',
          error_message: null,
          source_provenance: null,
        }],
        total: 1,
        limit: 20,
        offset: 0,
      });

      expect(normalized.documents[0]?.ready_file_count).toBe(3);
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

    it('preserves ready_file_count when returned on document details', () => {
      const normalized = normalizeDocumentDetailResponse({
        document_id: 'doc-files',
        job_id: null,
        user_id: 5,
        filename: 'files.pdf',
        title: null,
        status: 'COMPLETED',
        upload_timestamp: '2026-11-24T00:00:00Z',
        processing_started_at: null,
        processing_completed_at: null,
        file_size_bytes: 100,
        weaviate_tenant: 'tenant-user-1',
        chunk_count: 5,
        ready_file_count: 4,
        error_message: null,
        source_provenance: null,
      });

      expect(normalized.document.ready_file_count).toBe(4);
    });

    it('rejects the retired nested detail response shape', () => {
      expect(() => normalizeDocumentDetailResponse({
        document: { id: 'doc-retired', filename: 'retired.pdf' },
      } as never)).toThrow();
    });
  });

  describe('useDeleteDocument', () => {
    it('deletes a document and invalidates queries', async () => {
      const operationResult: OperationResult = {
        success: true,
        message: 'Document deleted successfully',
        document_id: 'doc-1',
        operation: 'delete_document',
        error: null,
      };
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => operationResult,
      });

      const { result } = renderHook(
        () => useDeleteDocument(),
        { wrapper: createWrapper() }
      );

      await expect(result.current.mutateAsync('doc-1')).resolves.toEqual(operationResult);

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1',
        expect.objectContaining({ method: 'DELETE' })
      );
    });

    it('handles delete errors', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: 'Cannot delete' }),
      });

      const { result } = renderHook(
        () => useDeleteDocument(),
        { wrapper: createWrapper() }
      );

      await expect(result.current.mutateAsync('doc-1')).rejects.toThrow('Cannot delete');
    });
  });

  describe('useReembedDocument', () => {
    it('returns the backend operation result when re-embedding a document', async () => {
      const operationResult: OperationResult = {
        success: true,
        message: 'Re-embedding initiated for 4 chunks',
        document_id: 'doc-1',
        operation: 'reembed_document',
        error: null,
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => operationResult,
      });

      const { result } = renderHook(
        () => useReembedDocument(),
        { wrapper: createWrapper() }
      );

      const response = await result.current.mutateAsync('doc-1');

      expect(response).toEqual(operationResult);
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/weaviate/documents/doc-1/reembed',
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

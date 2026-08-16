import { act, fireEvent, render, screen, waitFor } from '../../test/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { GridPaginationModel, GridSortModel } from '@mui/x-data-grid';
import type { DocumentFilter, DocumentSummary } from '../../services/weaviate';
import DocumentsPage, { buildDocumentListSearchParams, lastDocumentPage } from './DocumentsPage';

vi.hoisted(() => {
  vi.stubEnv('VITE_DOCUMENTS_LIBRARY_DEFAULT_PAGE_SIZE', '20');
  vi.stubEnv('VITE_DOCUMENTS_LIBRARY_SEARCH_DEBOUNCE_MS', '300');
});

interface MockDocumentListProps {
  documents: DocumentSummary[];
  loading: boolean;
  totalCount: number;
  filterBar?: ReactNode;
  paginationModel?: GridPaginationModel;
  onPaginationModelChange?: (model: GridPaginationModel) => void;
  sortModel?: GridSortModel;
  onSortModelChange?: (model: GridSortModel) => void;
}

interface MockInlineFilterBarProps {
  filters: DocumentFilter;
  onFilterChange: (filters: DocumentFilter) => void;
  onClear: () => void;
}

vi.mock('../../components/weaviate/DocumentList', () => ({
  default: ({
    documents,
    loading,
    totalCount,
    filterBar,
    paginationModel,
    onPaginationModelChange,
    sortModel,
    onSortModelChange,
  }: MockDocumentListProps) => (
    <section
      data-testid="document-list"
      data-loading={String(loading)}
      data-total-count={String(totalCount)}
      data-page={String(paginationModel?.page)}
      data-page-size={String(paginationModel?.pageSize)}
      data-sort-field={sortModel?.[0]?.field ?? ''}
      data-sort-order={sortModel?.[0]?.sort ?? ''}
      data-first-document-id={documents[0]?.id ?? ''}
      data-first-document-file-size={String(documents[0]?.fileSize ?? '')}
      data-first-document-status={documents[0]?.processingStatus ?? ''}
    >
      {filterBar}
      {documents.map((document) => <span key={document.id}>{document.filename}</span>)}
      <button
        type="button"
        onClick={() => onPaginationModelChange?.({ page: 2, pageSize: 50 })}
      >
        Show page three
      </button>
      <button
        type="button"
        onClick={() => onSortModelChange?.([{ field: 'filename', sort: 'asc' }])}
      >
        Sort by filename
      </button>
    </section>
  ),
}));

vi.mock('../../components/weaviate/InlineFilterBar', () => ({
  default: ({ filters, onFilterChange, onClear }: MockInlineFilterBarProps) => (
    <div>
      <label htmlFor="document-search">Search documents</label>
      <input
        id="document-search"
        value={filters.searchTerm ?? ''}
        onChange={(event) => onFilterChange({
          ...filters,
          searchTerm: event.target.value || undefined,
        })}
      />
      <button type="button" onClick={onClear}>Clear filters</button>
      <button
        type="button"
        onClick={() => onFilterChange({
          ...filters,
          embeddingStatus: ['completed'],
        })}
      >
        Filter completed
      </button>
    </div>
  ),
}));

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function documentListResponse(
  id: string,
  total: number,
  options: { limit?: number; offset?: number } = {},
): Response {
  const limit = options.limit ?? 20;
  const offset = options.offset ?? 0;
  return new Response(JSON.stringify({
    documents: [{
      document_id: id,
      user_id: 'curator-1',
      filename: `${id}.pdf`,
      title: null,
      status: 'completed',
      upload_timestamp: '2026-08-14T00:00:00Z',
      processing_started_at: null,
      processing_completed_at: '2026-08-14T00:01:00Z',
      file_size_bytes: 1024,
      weaviate_tenant: 'tenant-1',
      chunk_count: 4,
      vector_count: 4,
      embedding_status: 'completed',
      error_message: null,
      source_provenance: null,
    }],
    total,
    limit,
    offset,
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function fetchUrl(callIndex: number): string {
  return String(vi.mocked(global.fetch).mock.calls[callIndex]?.[0]);
}

function fetchSignal(callIndex: number): AbortSignal {
  const init = vi.mocked(global.fetch).mock.calls[callIndex]?.[1];
  return init?.signal as AbortSignal;
}

describe('buildDocumentListSearchParams', () => {
  it('sends search and pagination to the tenant-scoped documents endpoint', () => {
    const params = buildDocumentListSearchParams(
      { page: 9, pageSize: 20 },
      [{ field: 'filename', sort: 'asc' }],
      { searchTerm: 'J-158751.pdf', embeddingStatus: ['completed'] },
    );

    expect(params.toString()).toBe(
      'page=10&page_size=20&sort_by=filename&sort_order=asc&search=J-158751.pdf&embedding_status=completed',
    );
  });

  it('uses creation-date descending when the grid has no supported sort', () => {
    const params = buildDocumentListSearchParams(
      { page: 0, pageSize: 50 },
      [{ field: 'lastAccessedDate', sort: 'asc' }],
      {},
    );

    expect(params.toString()).toBe('page=1&page_size=50&sort_by=creationDate&sort_order=asc');
  });

  it('returns the last valid page after the final page shrinks', () => {
    expect(lastDocumentPage(10, 10)).toBe(0);
    expect(lastDocumentPage(11, 10)).toBe(1);
    expect(lastDocumentPage(0, 10)).toBe(0);
  });
});

describe('DocumentsPage request ownership', () => {
  beforeEach(() => {
    vi.mocked(global.fetch).mockReset();
  });

  it('loads the canonical response through the direct request contract and passes the server total', async () => {
    vi.mocked(global.fetch).mockResolvedValue(documentListResponse('canonical-document', 37));

    render(<DocumentsPage />);

    expect(await screen.findByText('canonical-document.pdf')).toBeInTheDocument();
    const list = screen.getByTestId('document-list');
    expect(list).toHaveAttribute('data-total-count', '37');
    expect(list).toHaveAttribute('data-page', '0');
    expect(list).toHaveAttribute('data-page-size', '20');
    expect(list).toHaveAttribute('data-first-document-id', 'canonical-document');
    expect(list).toHaveAttribute('data-first-document-file-size', '1024');
    expect(list).toHaveAttribute('data-first-document-status', 'completed');
    expect(fetchUrl(0)).toBe(
      '/api/weaviate/documents?page=1&page_size=20&sort_by=creationDate&sort_order=desc',
    );
    expect(vi.mocked(global.fetch).mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: expect.any(AbortSignal),
    }));
  });

  it('propagates controlled pagination and resets the page for sort and filter changes', async () => {
    vi.mocked(global.fetch).mockResolvedValue(documentListResponse('page-result', 200));

    render(<DocumentsPage />);
    await screen.findByText('page-result.pdf');

    fireEvent.click(screen.getByRole('button', { name: 'Show page three' }));
    await waitFor(() => expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(2));
    expect(fetchUrl(1)).toBe(
      '/api/weaviate/documents?page=3&page_size=50&sort_by=creationDate&sort_order=desc',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sort by filename' }));
    await waitFor(() => expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(3));
    expect(fetchUrl(2)).toBe(
      '/api/weaviate/documents?page=1&page_size=50&sort_by=filename&sort_order=asc',
    );
    expect(screen.getByTestId('document-list')).toHaveAttribute('data-page', '0');

    fireEvent.click(screen.getByRole('button', { name: 'Show page three' }));
    await waitFor(() => expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(4));
    expect(fetchUrl(3)).toBe(
      '/api/weaviate/documents?page=3&page_size=50&sort_by=filename&sort_order=asc',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Filter completed' }));
    await waitFor(() => expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(5));
    expect(fetchUrl(4)).toBe(
      '/api/weaviate/documents?page=1&page_size=50&sort_by=filename&sort_order=asc&embedding_status=completed',
    );
    expect(screen.getByTestId('document-list')).toHaveAttribute('data-page', '0');
  });

  it('debounces search and sends only the settled term from the first page', async () => {
    vi.mocked(global.fetch).mockResolvedValue(documentListResponse('search-result', 1));

    render(<DocumentsPage />);
    await screen.findByText('search-result.pdf');
    fireEvent.click(screen.getByRole('button', { name: 'Show page three' }));
    await waitFor(() => expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(2));

    const search = screen.getByRole('textbox', { name: 'Search documents' });
    fireEvent.change(search, { target: { value: 'alpha' } });
    fireEvent.change(search, { target: { value: 'beta' } });

    await waitFor(() => {
      expect(vi.mocked(global.fetch).mock.calls.some(([url]) => (
        String(url).includes('page=1') && String(url).includes('search=beta')
      ))).toBe(true);
    });
    expect(vi.mocked(global.fetch).mock.calls.some(([url]) => (
      String(url).includes('search=alpha')
    ))).toBe(false);
  });

  it('keeps the replacement loading when an aborted stale response finishes first', async () => {
    const staleResponse = deferred<Response>();
    const currentResponse = deferred<Response>();
    vi.mocked(global.fetch)
      .mockImplementationOnce(() => staleResponse.promise)
      .mockImplementationOnce(() => currentResponse.promise);

    render(<DocumentsPage />);
    const list = await screen.findByTestId('document-list');
    expect(list).toHaveAttribute('data-loading', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Show page three' }));
    await waitFor(() => expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(2));
    expect(fetchSignal(0).aborted).toBe(true);

    staleResponse.resolve(documentListResponse('stale-first', 999));
    await act(async () => {
      await staleResponse.promise;
    });
    expect(screen.queryByText('stale-first.pdf')).not.toBeInTheDocument();
    expect(list).toHaveAttribute('data-loading', 'true');

    currentResponse.resolve(documentListResponse('current-after-stale', 101, {
      limit: 50,
      offset: 100,
    }));
    expect(await screen.findByText('current-after-stale.pdf')).toBeInTheDocument();
    expect(list).toHaveAttribute('data-loading', 'false');
    expect(list).toHaveAttribute('data-total-count', '101');
  });

  it('does not let an aborted stale response overwrite a completed replacement', async () => {
    const staleResponse = deferred<Response>();
    const currentResponse = deferred<Response>();
    vi.mocked(global.fetch)
      .mockImplementationOnce(() => staleResponse.promise)
      .mockImplementationOnce(() => currentResponse.promise);

    render(<DocumentsPage />);
    await screen.findByTestId('document-list');
    fireEvent.click(screen.getByRole('button', { name: 'Show page three' }));
    await waitFor(() => expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(2));

    currentResponse.resolve(documentListResponse('current-first', 125, {
      limit: 50,
      offset: 100,
    }));
    expect(await screen.findByText('current-first.pdf')).toBeInTheDocument();

    staleResponse.resolve(documentListResponse('stale-last', 999));
    await act(async () => {
      await staleResponse.promise;
    });
    expect(screen.queryByText('stale-last.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('current-first.pdf')).toBeInTheDocument();
    expect(screen.getByTestId('document-list')).toHaveAttribute('data-total-count', '125');
  });

  it('corrects an invalid page and refetches the last valid page', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(documentListResponse('initial-page', 200))
      .mockResolvedValueOnce(documentListResponse('invalid-page', 51, {
        limit: 50,
        offset: 100,
      }))
      .mockResolvedValueOnce(documentListResponse('corrected-page', 51, {
        limit: 50,
        offset: 50,
      }));

    render(<DocumentsPage />);
    await screen.findByText('initial-page.pdf');
    fireEvent.click(screen.getByRole('button', { name: 'Show page three' }));

    expect(await screen.findByText('corrected-page.pdf')).toBeInTheDocument();
    expect(screen.queryByText('invalid-page.pdf')).not.toBeInTheDocument();
    expect(fetchUrl(1)).toContain('page=3&page_size=50');
    expect(fetchUrl(2)).toContain('page=2&page_size=50');
    expect(screen.getByTestId('document-list')).toHaveAttribute('data-page', '1');
  });
});

import { describe, expect, it } from 'vitest';
import { buildDocumentListSearchParams, lastDocumentPage } from './DocumentsPage';

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

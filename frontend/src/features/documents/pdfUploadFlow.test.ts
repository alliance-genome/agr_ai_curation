import { afterEach, describe, expect, it, vi } from 'vitest';

import { uploadPdfDocument } from './pdfUploadFlow';

describe('uploadPdfDocument', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('identifies the existing filename when an upload matches stored document content', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: vi.fn().mockResolvedValue({
        detail: {
          error: 'duplicate_file',
          existing_document_id: 'doc-existing',
          existing_filename: '8385804.pdf',
          uploaded_at: '2026-07-15T15:37:00+00:00',
          suggestion: 'This server-provided fallback should not hide the existing filename.',
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const upload = uploadPdfDocument(
      new File(['same PDF bytes'], 'J-389165.pdf', { type: 'application/pdf' }),
    );

    await expect(upload).rejects.toThrow(
      'The existing document is in Documents as "8385804.pdf". Search for that filename to load it.',
    );
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/weaviate/documents/upload',
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    );
  });
});

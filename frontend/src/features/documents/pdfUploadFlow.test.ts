import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { uploadPdfDocument, waitForDocumentProcessing, PdfProgressStreamError } from './pdfUploadFlow';

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

class FakeEventSource {
  static latest: FakeEventSource;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor() { FakeEventSource.latest = this; }
  emit(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}

describe('PDF progress tracking', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeEventSource);
    fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ processing_status: 'completed' }) });
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
    vi.unstubAllGlobals();
    delete window.__APP_RUNTIME_CONFIG__;
    vi.resetModules();
  });

  it('allows completion beyond the former 15-minute deadline', async () => {
    const result = waitForDocumentProcessing('doc');
    FakeEventSource.latest.onopen?.();
    await vi.advanceTimersByTimeAsync(16 * 60 * 1000);
    FakeEventSource.latest.emit({ stage: 'completed', progress: 100, final: true });
    await expect(result).resolves.toMatchObject({ stage: 'completed', final: true });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(FakeEventSource.latest.close).toHaveBeenCalledOnce();
  });

  it.each(['{"error":"Document not found","document_id":"doc"}', '{', 'null', '[]', '{}', '{"stage":3}', '{"stage":"parsing","progress":"bad"}', '{"error":""}'])('treats producer or malformed data as terminal: %s', async (data) => {
    const result = waitForDocumentProcessing('doc');
    const rejected = expect(result).rejects.toBeInstanceOf(PdfProgressStreamError);
    FakeEventSource.latest.onmessage?.({ data });
    await rejected;
    expect(fetchMock).not.toHaveBeenCalled();
    expect(FakeEventSource.latest.close).toHaveBeenCalledOnce();
  });

  it.each(['absent', 'connect', 'disconnect', 'connect-timeout'])('polls for transport failure: %s', async (failure) => {
    if (failure === 'absent') vi.stubGlobal('EventSource', undefined);
    const result = waitForDocumentProcessing('doc');
    if (failure === 'disconnect') FakeEventSource.latest.emit({ stage: 'parsing' });
    if (failure === 'connect-timeout') await vi.advanceTimersByTimeAsync(5000);
    else if (failure !== 'absent') FakeEventSource.latest.onerror?.();
    await expect(result).resolves.toMatchObject({ stage: 'completed' });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('does not reset the deadline when switching to polling, including a hung status request', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const result = waitForDocumentProcessing('doc', { timeoutMs: 10000 });
    const rejected = expect(result).rejects.toThrow('Timed out');
    FakeEventSource.latest.onopen?.();
    await vi.advanceTimersByTimeAsync(9000);
    FakeEventSource.latest.onerror?.();
    await vi.advanceTimersByTimeAsync(1000);
    await rejected;
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  });

  it('enforces the default one-hour total timeout without polling', async () => {
    const result = waitForDocumentProcessing('doc');
    const rejected = expect(result).rejects.toThrow('Timed out');
    FakeEventSource.latest.onopen?.();
    await vi.advanceTimersByTimeAsync(3600000);
    await rejected;
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['before', 'stream', 'poll'])('propagates abort during %s', async (phase) => {
    const controller = new AbortController();
    if (phase === 'before') controller.abort();
    if (phase === 'poll') {
      vi.stubGlobal('EventSource', undefined);
      fetchMock.mockResolvedValue({ ok: true, json: async () => ({ processing_status: 'parsing' }) });
    }
    const result = waitForDocumentProcessing('doc', { signal: controller.signal });
    const rejected = expect(result).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await rejected;
    if (phase !== 'poll') expect(fetchMock).not.toHaveBeenCalled();
  });

  it('applies boot-time timing overrides throughout tracking', async () => {
    window.__APP_RUNTIME_CONFIG__ = {
      VITE_PDF_PROGRESS_CONNECT_TIMEOUT_MS: '100',
      VITE_PDF_PROGRESS_TIMEOUT_MS: '1000',
      VITE_PDF_PROGRESS_POLL_INTERVAL_MS: '250',
    };
    vi.resetModules();
    const { waitForDocumentProcessing: configuredWait } = await import('./pdfUploadFlow');
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ processing_status: 'parsing' }) });
    const result = configuredWait('doc');
    const rejected = expect(result).rejects.toThrow('Timed out');
    await vi.advanceTimersByTimeAsync(99);
    expect(fetchMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(650);
    await rejected;
  });

  it.each(['failed', 'cancelled', 'timeout'])('preserves terminal stage %s', async (stage) => {
    const result = waitForDocumentProcessing('doc');
    FakeEventSource.latest.emit({ stage, message: 'Processing stopped' });
    await expect(result).resolves.toMatchObject({ stage, final: true });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('uses the configured polling cadence', async () => {
    vi.stubGlobal('EventSource', undefined);
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ processing_status: 'parsing' }) });
    const result = waitForDocumentProcessing('doc', { pollingIntervalMs: 2500 });
    await vi.advanceTimersByTimeAsync(2499);
    expect(fetchMock).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toMatchObject({ stage: 'completed' });
  });
});

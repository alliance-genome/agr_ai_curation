import { afterEach, describe, expect, it, vi } from 'vitest';

describe('document intake configuration', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('preserves the operational defaults', async () => {
    const config = await import('./documentIntakeConfig');

    expect(config.PDF_UPLOAD_MAX_SELECTED_FILES).toBe(10);
    expect(config.PDF_JOB_WINDOW_DAYS).toBe(7);
    expect(config.PDF_JOB_LIMIT).toBe(50);
    expect(config.PDF_JOB_FALLBACK_POLL_INTERVAL_MS).toBe(5000);
  });

  it('uses positive VITE overrides for all four limits', async () => {
    vi.stubEnv('VITE_PDF_UPLOAD_MAX_SELECTED_FILES', '4');
    vi.stubEnv('VITE_PDF_JOB_WINDOW_DAYS', '14');
    vi.stubEnv('VITE_PDF_JOB_LIMIT', '75');
    vi.stubEnv('VITE_PDF_JOB_FALLBACK_POLL_INTERVAL_MS', '2500');

    const config = await import('./documentIntakeConfig');

    expect(config.PDF_UPLOAD_MAX_SELECTED_FILES).toBe(4);
    expect(config.PDF_JOB_WINDOW_DAYS).toBe(14);
    expect(config.PDF_JOB_LIMIT).toBe(75);
    expect(config.PDF_JOB_FALLBACK_POLL_INTERVAL_MS).toBe(2500);

    const { validatePdfSelection } = await import('./pdfUploadFlow');
    const files = Array.from(
      { length: 5 },
      (_, index) => new File(['pdf'], `paper-${index}.pdf`, { type: 'application/pdf' }),
    );
    expect(validatePdfSelection(files)).toEqual({
      ok: false,
      files,
      error: 'Please select up to 4 PDF files at a time',
    });
  });

  it.each([
    ['VITE_PDF_UPLOAD_MAX_SELECTED_FILES', '0'],
    ['VITE_PDF_JOB_WINDOW_DAYS', '-1'],
    ['VITE_PDF_JOB_LIMIT', 'invalid'],
    ['VITE_PDF_JOB_FALLBACK_POLL_INTERVAL_MS', '0'],
  ])('rejects an invalid explicit %s override', async (name, value) => {
    vi.stubEnv(name, value);

    await expect(import('./documentIntakeConfig')).rejects.toThrow(
      `${name} must be a positive integer.`,
    );
  });
});

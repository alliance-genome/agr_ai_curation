import { afterEach, describe, expect, it, vi } from 'vitest';

import { getAddLiteratureConfig } from './addLiterature';

const ENV_KEYS = [
  'VITE_ADD_LITERATURE_MAX_SELECTED_FILES',
  'VITE_ADD_LITERATURE_PDF_JOB_WINDOW_DAYS',
  'VITE_ADD_LITERATURE_PDF_JOB_LIMIT',
  'VITE_ADD_LITERATURE_FALLBACK_POLL_INTERVAL_MS',
] as const;

describe('getAddLiteratureConfig', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('preserves the operational defaults', () => {
    ENV_KEYS.forEach((key) => vi.stubEnv(key, ''));

    expect(getAddLiteratureConfig()).toEqual({
      maxSelectedFiles: 10,
      pdfJobWindowDays: 7,
      pdfJobLimit: 50,
      fallbackPollingIntervalMs: 5000,
    });
  });

  it('reads positive VITE overrides from one configuration source', () => {
    vi.stubEnv('VITE_ADD_LITERATURE_MAX_SELECTED_FILES', '3');
    vi.stubEnv('VITE_ADD_LITERATURE_PDF_JOB_WINDOW_DAYS', '14');
    vi.stubEnv('VITE_ADD_LITERATURE_PDF_JOB_LIMIT', '75');
    vi.stubEnv('VITE_ADD_LITERATURE_FALLBACK_POLL_INTERVAL_MS', '2500');

    expect(getAddLiteratureConfig()).toEqual({
      maxSelectedFiles: 3,
      pdfJobWindowDays: 14,
      pdfJobLimit: 75,
      fallbackPollingIntervalMs: 2500,
    });
  });
});

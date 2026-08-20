import { getEnvInt } from '@/utils/env';

export interface AddLiteratureConfig {
  maxSelectedFiles: number;
  pdfJobWindowDays: number;
  pdfJobLimit: number;
  fallbackPollingIntervalMs: number;
}

const positiveEnvInt = (key: string, fallback: number): number => {
  const value = getEnvInt(key, fallback);
  return value > 0 ? value : fallback;
};

export const getAddLiteratureConfig = (): AddLiteratureConfig => ({
  maxSelectedFiles: positiveEnvInt('VITE_ADD_LITERATURE_MAX_SELECTED_FILES', 10),
  pdfJobWindowDays: positiveEnvInt('VITE_ADD_LITERATURE_PDF_JOB_WINDOW_DAYS', 7),
  pdfJobLimit: positiveEnvInt('VITE_ADD_LITERATURE_PDF_JOB_LIMIT', 50),
  fallbackPollingIntervalMs: positiveEnvInt(
    'VITE_ADD_LITERATURE_FALLBACK_POLL_INTERVAL_MS',
    5000,
  ),
});

import { getEnvVar } from '@/utils/env';

const positiveEnvInt = (name: string, fallback: number): number => {
  const rawValue = getEnvVar(name);
  if (rawValue === undefined) {
    return fallback;
  }
  if (!/^\d+$/.test(rawValue) || Number(rawValue) <= 0) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return Number(rawValue);
};

export const PDF_UPLOAD_MAX_SELECTED_FILES = positiveEnvInt(
  'VITE_PDF_UPLOAD_MAX_SELECTED_FILES',
  10,
);

export const PDF_JOB_WINDOW_DAYS = positiveEnvInt('VITE_PDF_JOB_WINDOW_DAYS', 7);

export const PDF_JOB_LIMIT = positiveEnvInt('VITE_PDF_JOB_LIMIT', 50);

export const PDF_JOB_FALLBACK_POLL_INTERVAL_MS = positiveEnvInt(
  'VITE_PDF_JOB_FALLBACK_POLL_INTERVAL_MS',
  5000,
);

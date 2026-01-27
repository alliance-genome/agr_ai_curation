type EnvSource = Record<string, string | boolean | number | undefined> | undefined;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const getViteEnv = (): EnvSource => (typeof import.meta !== 'undefined' ? (import.meta as any).env : undefined);

const getNodeEnv = (): EnvSource => (typeof process !== 'undefined' ? process.env : undefined);

export const getEnvVar = (keys: string[] | string, fallback?: string): string | undefined => {
  const keyList = Array.isArray(keys) ? keys : [keys];
  const sources: EnvSource[] = [getViteEnv(), getNodeEnv()];

  for (const key of keyList) {
    for (const source of sources) {
      if (source && source[key] !== undefined) {
        return String(source[key]);
      }
    }
  }

  return fallback;
};

/**
 * Debug logging utility - only logs when VITE_DEBUG=true
 * Usage: import { debug } from '../utils/env'; debug.log('message', data);
 */
const isDebugMode = (): boolean => {
  const sources: EnvSource[] = [getViteEnv(), getNodeEnv()];
  for (const source of sources) {
    if (source) {
      const val = source['VITE_DEBUG'] || source['DEBUG'];
      if (val === true || val === 'true' || val === '1') return true;
    }
  }
  return false;
};

export const debug = {
  log: (...args: unknown[]): void => {
    if (isDebugMode()) console.log(...args);
  },
  warn: (...args: unknown[]): void => {
    if (isDebugMode()) console.warn(...args);
  },
  error: (...args: unknown[]): void => {
    // Always log errors, even in production
    console.error(...args);
  },
};

export const getEnvFlag = (keys: string[] | string, fallback = false): boolean => {
  const value = getEnvVar(keys);
  if (value === undefined) {
    return fallback;
  }

  switch (String(value).toLowerCase()) {
    case 'true':
    case '1':
    case 'yes':
    case 'on':
      return true;
    case 'false':
    case '0':
    case 'no':
    case 'off':
      return false;
    default:
      return fallback;
  }
};

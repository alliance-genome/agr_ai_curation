import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createTraceReviewTheme,
  persistTraceReviewThemeMode,
  readTraceReviewThemeMode,
} from './theme';

const STORAGE_KEY = 'trace-review:theme-mode';

function installLocalStorage(storage: Pick<Storage, 'getItem' | 'setItem'>) {
  vi.stubGlobal('window', { localStorage: storage });
}

describe('trace review theme mode storage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('defaults to dark when no persisted mode exists', () => {
    const getItem = vi.fn(() => null);

    installLocalStorage({
      getItem,
      setItem: vi.fn(),
    });

    expect(readTraceReviewThemeMode()).toBe('dark');
    expect(getItem).toHaveBeenCalledWith(STORAGE_KEY);
  });

  it('reads a persisted light or dark mode', () => {
    const getItem = vi.fn()
      .mockReturnValueOnce('light')
      .mockReturnValueOnce('dark');

    installLocalStorage({
      getItem,
      setItem: vi.fn(),
    });

    expect(readTraceReviewThemeMode()).toBe('light');
    expect(readTraceReviewThemeMode()).toBe('dark');
  });

  it('surfaces corrupt persisted modes', () => {
    installLocalStorage({
      getItem: vi.fn(() => 'solarized'),
      setItem: vi.fn(),
    });

    expect(() => readTraceReviewThemeMode()).toThrow(
      'Invalid trace review theme mode "solarized" in localStorage.',
    );
  });

  it('surfaces localStorage read failures', () => {
    installLocalStorage({
      getItem: vi.fn(() => {
        throw new Error('storage disabled');
      }),
      setItem: vi.fn(),
    });

    expect(() => readTraceReviewThemeMode()).toThrow('storage disabled');
  });

  it('persists the selected theme mode', () => {
    const setItem = vi.fn();

    installLocalStorage({
      getItem: vi.fn(),
      setItem,
    });

    persistTraceReviewThemeMode('light');

    expect(setItem).toHaveBeenCalledWith(STORAGE_KEY, 'light');
  });

  it('surfaces localStorage write failures', () => {
    installLocalStorage({
      getItem: vi.fn(),
      setItem: vi.fn(() => {
        throw new Error('quota exceeded');
      }),
    });

    expect(() => persistTraceReviewThemeMode('dark')).toThrow('quota exceeded');
  });
});

describe('createTraceReviewTheme', () => {
  it('creates the dark trace review theme branch', () => {
    const theme = createTraceReviewTheme('dark');

    expect(theme.palette.mode).toBe('dark');
    expect(theme.palette.primary.main).toBe('#90caf9');
    expect(theme.palette.background.default).toBe('#121212');
    expect(theme.palette.text.primary).toBe('#ffffff');
  });

  it('creates the light trace review theme branch', () => {
    const theme = createTraceReviewTheme('light');

    expect(theme.palette.mode).toBe('light');
    expect(theme.palette.primary.main).toBe('#1565c0');
    expect(theme.palette.background.default).toBe('#f6f8fb');
    expect(theme.palette.text.primary).toBe('#17212b');
  });
});

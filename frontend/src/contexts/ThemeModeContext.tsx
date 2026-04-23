import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { ThemeProvider as MuiThemeProvider } from '@mui/material/styles';
import type { Theme } from '@mui/material/styles';

import { createAppTheme, DEFAULT_THEME_MODE, type ThemeMode } from '@/theme';

export const THEME_MODE_STORAGE_KEY = 'ai-curation:theme-mode';

interface ThemeModeContextValue {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggleMode: () => void;
  theme: Theme;
}

const ThemeModeContext = createContext<ThemeModeContextValue | null>(null);

function isThemeMode(value: string | null): value is ThemeMode {
  return value === 'light' || value === 'dark';
}

function readStoredThemeMode(): ThemeMode {
  if (typeof window === 'undefined') {
    return DEFAULT_THEME_MODE;
  }

  try {
    const storedMode = window.localStorage.getItem(THEME_MODE_STORAGE_KEY);
    return isThemeMode(storedMode) ? storedMode : DEFAULT_THEME_MODE;
  } catch {
    return DEFAULT_THEME_MODE;
  }
}

function persistThemeMode(mode: ThemeMode) {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(THEME_MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore storage failures so restricted browser contexts still render.
  }
}

export function ThemeModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredThemeMode());

  const setMode = useCallback((nextMode: ThemeMode) => {
    setModeState(nextMode);
    persistThemeMode(nextMode);
  }, []);

  const toggleMode = useCallback(() => {
    setModeState((currentMode) => {
      const nextMode = currentMode === 'dark' ? 'light' : 'dark';
      persistThemeMode(nextMode);
      return nextMode;
    });
  }, []);

  const theme = useMemo(() => createAppTheme(mode), [mode]);

  const contextValue = useMemo(
    () => ({
      mode,
      setMode,
      toggleMode,
      theme,
    }),
    [mode, setMode, toggleMode, theme],
  );

  return (
    <ThemeModeContext.Provider value={contextValue}>
      <MuiThemeProvider theme={theme}>{children}</MuiThemeProvider>
    </ThemeModeContext.Provider>
  );
}

export function useThemeMode() {
  const context = useContext(ThemeModeContext);
  if (!context) {
    throw new Error('useThemeMode must be used within a ThemeModeProvider');
  }

  return context;
}

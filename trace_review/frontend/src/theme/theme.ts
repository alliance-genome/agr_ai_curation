import type { PaletteMode } from '@mui/material';
import { alpha, createTheme } from '@mui/material/styles';

export type TraceReviewThemeMode = Extract<PaletteMode, 'light' | 'dark'>;

const DEFAULT_TRACE_REVIEW_THEME_MODE: TraceReviewThemeMode = 'dark';
const TRACE_REVIEW_THEME_MODE_STORAGE_KEY = 'trace-review:theme-mode';

function isTraceReviewThemeMode(value: string | null): value is TraceReviewThemeMode {
  return value === 'light' || value === 'dark';
}

export function readTraceReviewThemeMode(): TraceReviewThemeMode {
  const storedMode = window.localStorage.getItem(TRACE_REVIEW_THEME_MODE_STORAGE_KEY);

  if (storedMode === null) {
    return DEFAULT_TRACE_REVIEW_THEME_MODE;
  }

  if (!isTraceReviewThemeMode(storedMode)) {
    throw new Error(`Invalid trace review theme mode "${storedMode}" in localStorage.`);
  }

  return storedMode;
}

export function persistTraceReviewThemeMode(mode: TraceReviewThemeMode) {
  window.localStorage.setItem(TRACE_REVIEW_THEME_MODE_STORAGE_KEY, mode);
}

export function createTraceReviewTheme(mode: TraceReviewThemeMode) {
  const isDark = mode === 'dark';
  const primaryMain = isDark ? '#90caf9' : '#1976d2';
  const secondaryMain = isDark ? '#f48fb1' : '#ad1457';
  const backgroundDefault = isDark ? '#121212' : '#f6f8fb';
  const backgroundPaper = isDark ? '#1e1e1e' : '#ffffff';
  const textPrimary = isDark ? '#ffffff' : '#17212b';
  const textSecondary = isDark ? '#b0b0b0' : '#51606f';

  return createTheme({
    palette: {
      mode,
      primary: {
        main: primaryMain,
      },
      secondary: {
        main: secondaryMain,
      },
      background: {
        default: backgroundDefault,
        paper: backgroundPaper,
      },
      text: {
        primary: textPrimary,
        secondary: textSecondary,
      },
      divider: alpha(textPrimary, isDark ? 0.14 : 0.12),
      action: {
        hover: alpha(textPrimary, isDark ? 0.06 : 0.04),
        selected: alpha(primaryMain, isDark ? 0.18 : 0.12),
        disabled: alpha(textPrimary, isDark ? 0.32 : 0.26),
        disabledBackground: alpha(textPrimary, isDark ? 0.12 : 0.08),
      },
    },
    shape: {
      borderRadius: 6,
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundColor: backgroundDefault,
            color: textPrimary,
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: {
            textTransform: 'none',
          },
        },
      },
      MuiAppBar: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
          },
        },
      },
      MuiAccordion: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
          },
        },
      },
    },
  });
}

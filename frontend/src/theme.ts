import { alpha, createTheme } from '@mui/material/styles';
import type { PaletteMode } from '@mui/material';
import type { ThemeOptions } from '@mui/material/styles';

export type ThemeMode = PaletteMode;

export const DEFAULT_THEME_MODE: ThemeMode = 'dark';

const primary = {
  main: '#2196f3',
  light: '#64b5f6',
  dark: '#1976d2',
  contrastText: '#ffffff',
};

const secondary = {
  main: '#1565c0',
  light: '#42a5f5',
  dark: '#0d47a1',
  contrastText: '#ffffff',
};

function getModeTokens(mode: ThemeMode) {
  if (mode === 'light') {
    return {
      backgroundDefault: '#f6f9fc',
      backgroundPaper: '#ffffff',
      dataGridHeader: '#edf5fd',
      textPrimary: '#102033',
      textSecondary: 'rgba(16, 32, 51, 0.68)',
      divider: 'rgba(16, 32, 51, 0.12)',
      actionActive: 'rgba(16, 32, 51, 0.56)',
      actionHover: alpha(primary.main, 0.08),
      actionSelected: alpha(primary.main, 0.14),
      actionDisabled: 'rgba(16, 32, 51, 0.3)',
      actionDisabledBackground: 'rgba(16, 32, 51, 0.1)',
    };
  }

  return {
    backgroundDefault: '#121212',
    backgroundPaper: '#1e1e1e',
    dataGridHeader: '#252525',
    textPrimary: '#ffffff',
    textSecondary: 'rgba(255, 255, 255, 0.7)',
    divider: 'rgba(255, 255, 255, 0.12)',
    actionActive: 'rgba(255, 255, 255, 0.54)',
    actionHover: 'rgba(255, 255, 255, 0.08)',
    actionSelected: 'rgba(255, 255, 255, 0.16)',
    actionDisabled: 'rgba(255, 255, 255, 0.3)',
    actionDisabledBackground: 'rgba(255, 255, 255, 0.12)',
  };
}

function buildThemeOptions(mode: ThemeMode): ThemeOptions {
  const tokens = getModeTokens(mode);

  return {
    palette: {
      mode,
      primary,
      secondary,
      background: {
        default: tokens.backgroundDefault,
        paper: tokens.backgroundPaper,
      },
      text: {
        primary: tokens.textPrimary,
        secondary: tokens.textSecondary,
      },
      divider: tokens.divider,
      action: {
        active: tokens.actionActive,
        hover: tokens.actionHover,
        selected: tokens.actionSelected,
        disabled: tokens.actionDisabled,
        disabledBackground: tokens.actionDisabledBackground,
      },
    },
    typography: {
      fontFamily: '"Roboto", "Helvetica", "Arial", sans-serif',
      h1: {
        fontSize: '1.5rem',
        fontWeight: 500,
        color: tokens.textPrimary,
      },
      h6: {
        fontSize: '1.25rem',
        fontWeight: 500,
      },
    },
    components: {
      // @ts-expect-error MuiDataGrid types come from @mui/x-data-grid
      MuiDataGrid: {
        styleOverrides: {
          root: {
            backgroundColor: tokens.backgroundPaper,
            color: tokens.textPrimary,
            border: `1px solid ${tokens.divider}`,
          },
          cell: {
            borderBottom: `1px solid ${tokens.divider}`,
          },
          columnHeaders: {
            backgroundColor: tokens.dataGridHeader,
            borderBottom: `1px solid ${tokens.divider}`,
          },
          footerContainer: {
            borderTop: `1px solid ${tokens.divider}`,
            backgroundColor: tokens.dataGridHeader,
          },
          row: {
            '&:hover': {
              backgroundColor: tokens.actionHover,
            },
          },
        },
      },
      MuiDrawer: {
        styleOverrides: {
          paper: {
            backgroundColor: tokens.backgroundPaper,
            borderRight: `1px solid ${tokens.divider}`,
          },
        },
      },
      MuiAppBar: {
        styleOverrides: {
          root: {
            backgroundColor: primary.main,
            zIndex: 1201, // Above drawer
          },
        },
      },
      MuiListItemButton: {
        styleOverrides: {
          root: {
            '&:hover': {
              backgroundColor: tokens.actionHover,
            },
            '&.Mui-selected': {
              backgroundColor: alpha(primary.main, mode === 'dark' ? 0.16 : 0.14),
              '&:hover': {
                backgroundColor: alpha(primary.main, mode === 'dark' ? 0.24 : 0.2),
              },
            },
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundColor: tokens.backgroundPaper,
          },
        },
      },
    },
  };
}

export function createAppTheme(mode: ThemeMode = DEFAULT_THEME_MODE) {
  return createTheme(buildThemeOptions(mode));
}

const theme = createAppTheme();

export default theme;

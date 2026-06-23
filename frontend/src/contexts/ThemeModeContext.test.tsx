import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useTheme } from '@mui/material/styles';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  THEME_MODE_STORAGE_KEY,
  ThemeModeProvider,
  useThemeMode,
} from './ThemeModeContext';

function ThemeModeProbe() {
  const { mode, setMode, toggleMode } = useThemeMode();
  const theme = useTheme();

  return (
    <>
      <div>Context mode: {mode}</div>
      <div>MUI mode: {theme.palette.mode}</div>
      <button onClick={toggleMode}>Toggle mode</button>
      <button onClick={() => setMode('light')}>Set light mode</button>
    </>
  );
}

describe('ThemeModeProvider', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('defaults to the dark theme when no preference is stored', () => {
    render(
      <ThemeModeProvider>
        <ThemeModeProbe />
      </ThemeModeProvider>,
    );

    expect(screen.getByText('Context mode: dark')).toBeInTheDocument();
    expect(screen.getByText('MUI mode: dark')).toBeInTheDocument();
  });

  it('initializes from a stored light preference', () => {
    localStorage.setItem(THEME_MODE_STORAGE_KEY, 'light');

    render(
      <ThemeModeProvider>
        <ThemeModeProbe />
      </ThemeModeProvider>,
    );

    expect(screen.getByText('Context mode: light')).toBeInTheDocument();
    expect(screen.getByText('MUI mode: light')).toBeInTheDocument();
  });

  it('falls back to the default mode when storage reads fail during bootstrap', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage read failed');
    });

    render(
      <ThemeModeProvider>
        <ThemeModeProbe />
      </ThemeModeProvider>,
    );

    expect(screen.getByText('Context mode: dark')).toBeInTheDocument();
  });

  it('persists toggled preferences', async () => {
    const user = userEvent.setup();

    render(
      <ThemeModeProvider>
        <ThemeModeProbe />
      </ThemeModeProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'Toggle mode' }));

    expect(screen.getByText('Context mode: light')).toBeInTheDocument();
    expect(localStorage.getItem(THEME_MODE_STORAGE_KEY)).toBe('light');
  });

  it('updates mode in memory when storage writes fail', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage write failed');
    });

    render(
      <ThemeModeProvider>
        <ThemeModeProbe />
      </ThemeModeProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Set light mode' }));

    expect(screen.getByText('Context mode: light')).toBeInTheDocument();
  });
});

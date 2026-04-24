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

  it('surfaces storage read failures during bootstrap', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage read failed');
    });
    const handleError = (event: ErrorEvent) => {
      event.preventDefault();
    };

    window.addEventListener('error', handleError);
    try {
      expect(() =>
        render(
          <ThemeModeProvider>
            <ThemeModeProbe />
          </ThemeModeProvider>,
        ),
      ).toThrow('storage read failed');
    } finally {
      window.removeEventListener('error', handleError);
    }
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

  it('surfaces storage write failures without updating mode', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage write failed');
    });
    let surfacedMessage = '';
    const handleError = (event: ErrorEvent) => {
      event.preventDefault();
      surfacedMessage = event.error instanceof Error ? event.error.message : event.message;
    };

    render(
      <ThemeModeProvider>
        <ThemeModeProbe />
      </ThemeModeProvider>,
    );

    window.addEventListener('error', handleError);
    fireEvent.click(screen.getByRole('button', { name: 'Set light mode' }));
    window.removeEventListener('error', handleError);

    expect(surfacedMessage).toBe('storage write failed');
    expect(screen.getByText('Context mode: dark')).toBeInTheDocument();
  });
});

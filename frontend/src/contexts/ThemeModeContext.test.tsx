import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useTheme } from '@mui/material/styles';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  THEME_MODE_STORAGE_KEY,
  ThemeModeProvider,
  useThemeMode,
} from './ThemeModeContext';

function ThemeModeProbe() {
  const { mode, toggleMode } = useThemeMode();
  const theme = useTheme();

  return (
    <>
      <div>Context mode: {mode}</div>
      <div>MUI mode: {theme.palette.mode}</div>
      <button onClick={toggleMode}>Toggle mode</button>
    </>
  );
}

describe('ThemeModeProvider', () => {
  beforeEach(() => {
    localStorage.clear();
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
});

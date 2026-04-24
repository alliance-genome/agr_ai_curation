import { IconButton, Tooltip } from '@mui/material';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import type { TraceReviewThemeMode } from '../theme/theme';

interface ThemeModeToggleProps {
  mode: TraceReviewThemeMode;
  onChange: (mode: TraceReviewThemeMode) => void;
}

export function ThemeModeToggle({ mode, onChange }: ThemeModeToggleProps) {
  const nextMode: TraceReviewThemeMode = mode === 'dark' ? 'light' : 'dark';
  const label = `Switch to ${nextMode} mode`;

  return (
    <Tooltip title={label}>
      <IconButton color="inherit" aria-label={label} onClick={() => onChange(nextMode)}>
        {mode === 'dark' ? <LightModeIcon /> : <DarkModeIcon />}
      </IconButton>
    </Tooltip>
  );
}

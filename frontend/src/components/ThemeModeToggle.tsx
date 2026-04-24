import { IconButton, Tooltip } from '@mui/material';
import {
  DarkMode as DarkModeIcon,
  LightMode as LightModeIcon,
} from '@mui/icons-material';

import { useThemeMode } from '@/contexts/ThemeModeContext';

export default function ThemeModeToggle() {
  const { mode, toggleMode } = useThemeMode();
  const nextMode = mode === 'dark' ? 'light' : 'dark';
  const label = `Switch to ${nextMode} mode`;

  return (
    <Tooltip title={label} arrow>
      <IconButton
        aria-label={label}
        color="inherit"
        onClick={toggleMode}
        size="small"
        sx={{ mr: 1 }}
      >
        {mode === 'dark' ? <LightModeIcon fontSize="small" /> : <DarkModeIcon fontSize="small" />}
      </IconButton>
    </Tooltip>
  );
}

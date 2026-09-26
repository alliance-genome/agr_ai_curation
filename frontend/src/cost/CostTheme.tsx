import { ReactNode, useEffect, useMemo, useState } from 'react';
import { ThemeProvider } from '@mui/material/styles';
import CssBaseline from '@mui/material/CssBaseline';
import Button from '@mui/material/Button';
import { createAppTheme } from '../theme';

type Mode = 'light' | 'dark';
const preferenceKey = 'cost-theme:v1';
function initialMode(): Mode {
  try {
    const saved = localStorage.getItem(preferenceKey);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* The dashboard also works with storage disabled. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export default function CostTheme({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState(initialMode);
  const theme = useMemo(() => createAppTheme(mode), [mode]);
  useEffect(() => {
    document.documentElement.dataset.costTheme = mode;
    return () => { delete document.documentElement.dataset.costTheme; };
  }, [mode]);
  function toggle() {
    const next = mode === 'light' ? 'dark' : 'light';
    setMode(next);
    try { localStorage.setItem(preferenceKey, next); } catch { /* Optional preference only. */ }
  }
  return <ThemeProvider theme={theme}><CssBaseline />
    <div className="cost-theme-control"><Button onClick={toggle} aria-pressed={mode === 'dark'}>Dark mode</Button></div>
    {children}
  </ThemeProvider>;
}

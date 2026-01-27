import { createTheme } from '@mui/material/styles';

// Dark mode theme for trace review
export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#90caf9', // lighter blue for dark mode
    },
    secondary: {
      main: '#f48fb1', // lighter pink for dark mode
    },
    background: {
      default: '#121212', // dark background
      paper: '#1e1e1e', // slightly lighter for cards
    },
    text: {
      primary: '#ffffff',
      secondary: '#b0b0b0',
    },
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none', // Don't uppercase button text
        },
      },
    },
  },
});

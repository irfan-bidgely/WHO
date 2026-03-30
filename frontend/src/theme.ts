import { createTheme } from '@mui/material/styles';

/** Montserrat is loaded via `@fontsource-variable/montserrat` in `main.tsx`. */
export const appTheme = createTheme({
  typography: {
    fontFamily: '"Montserrat Variable", "Montserrat", system-ui, sans-serif',
    h1: { fontWeight: 600 },
    h2: { fontWeight: 600 },
  },
  palette: {
    mode: 'light',
    primary: { main: '#0d47a1' },
    background: { default: '#f6f8fb' },
  },
});

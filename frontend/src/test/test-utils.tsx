import React from 'react';
import { render, RenderOptions } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';

const theme = createTheme();

interface AllTheProvidersProps {
  children: React.ReactNode;
}

// Create a custom render function that includes providers
function AllTheProviders({ children }: AllTheProvidersProps) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false, // Turn off retries for tests
        gcTime: 0, // gcTime replaces cacheTime in React Query v5
      },
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <BrowserRouter>{children}</BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

const customRender = (
  ui: React.ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>
) => render(ui, { wrapper: AllTheProviders, ...options });

// Mock data generators
export const createMockChunk = (overrides = {}) => ({
  id: 'chunk-1',
  documentId: '1',
  chunkIndex: 0,
  content: 'This is test content for the chunk.',
  elementType: 'NarrativeText',
  pageNumber: 1,
  sectionTitle: 'Introduction',
  metadata: {
    characterCount: 36,
    wordCount: 7,
    hasTable: false,
    hasImage: false,
  },
  ...overrides,
});

// Re-export everything
export * from '@testing-library/react';
export { customRender as render };
export { default as userEvent } from '@testing-library/user-event';

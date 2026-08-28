import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Box } from '@mui/material';
import { render, screen, within } from '../../test/test-utils';
import DocumentList from './DocumentList';
import type { DocumentSummary } from '../../services/weaviate';

const refetchHealthMock = vi.fn();
const openCurationWorkspaceMock = vi.fn();

const createTestDocument = (overrides: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: '1',
  filename: 'test-document.pdf',
  fileSize: 1024000,
  creationDate: '2024-01-01T00:00:00.000Z',
  processingStatus: 'completed',
  embeddingStatus: 'completed',
  chunkCount: 10,
  vectorCount: 100,
  ...overrides,
});

vi.mock('../../lib/globalNotifications', () => ({
  emitGlobalToast: vi.fn(),
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { uid: 'scroll-test-user' } }),
}));

vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace'
  );

  return {
    ...actual,
    openCurationWorkspace: (options: unknown) => openCurationWorkspaceMock(options),
  };
});

vi.mock('../../services/weaviate', async () => {
  const actual = await vi.importActual<typeof import('../../services/weaviate')>('../../services/weaviate');
  return {
    ...actual,
    usePdfExtractionHealth: () => ({
      data: { status: 'healthy', last_checked: '2026-03-05T00:00:00Z' },
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: refetchHealthMock,
    }),
  };
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const totalRows = 48;

describe('DocumentList TanStack table scroll behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it.each([
    ['Safari laptop height', 620],
    ['desktop height', 900],
  ])('keeps lower row actions in the bounded vertical and horizontal scroller at %s', (_label, viewportHeight) => {
    const docs = Array.from({ length: totalRows }, (_, index) => {
      const rowNumber = index + 1;
      return createTestDocument({
        id: `doc-${rowNumber}`,
        filename: `doc-${rowNumber}.pdf`,
        title: `Document ${rowNumber}`,
      });
    });

    render(
      <Box
        data-testid="documents-scroll-test-shell"
        sx={{
          display: 'flex',
          flexDirection: 'column',
          height: viewportHeight,
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
        <Box data-testid="documents-jobs-panel">PDF jobs panel visible</Box>
        <DocumentList
          documents={docs}
          loading={false}
          totalCount={docs.length}
          onDelete={vi.fn()}
          onReembed={vi.fn()}
          onRefresh={vi.fn()}
          checkboxSelection
          filterBar={<Box>Filter bar visible</Box>}
        />
      </Box>
    );

    expect(screen.getByTestId('documents-jobs-panel')).toBeVisible();
    expect(screen.getByText('Filter bar visible')).toBeVisible();

    const scrollRegion = screen.getByTestId('documents-table-scroll-region');
    expect(scrollRegion).toHaveStyle({ overflow: 'hidden' });
    const tableScroller = scrollRegion.querySelector('.MuiTableContainer-root');
    expect(tableScroller).toHaveStyle({ overflow: 'auto' });

    const lowerRow = screen.getByText('doc-48.pdf').closest('tr');
    expect(lowerRow).not.toBeNull();
    expect(within(lowerRow as HTMLElement).getByTestId('VisibilityIcon')).toBeInTheDocument();
  });
});

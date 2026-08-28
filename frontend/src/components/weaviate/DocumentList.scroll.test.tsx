import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Box } from '@mui/material';
import { fireEvent, render, screen, within } from '../../test/test-utils';
import DocumentList from './DocumentList';
import type { DocumentSummary } from '../../services/weaviate';

const refetchHealthMock = vi.fn();
const openCurationWorkspaceMock = vi.fn();
const rowHeight = 52;
const totalRows = 48;

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

vi.mock('../../lib/globalNotifications', () => ({ emitGlobalToast: vi.fn() }));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { uid: 'scroll-test-user' } }),
}));
vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace',
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

type LayoutMocks = { restore: () => void };

function installMeasuredLayout(viewportHeight: number): LayoutMocks {
  const tableHeight = viewportHeight === 620 ? 300 : 580;
  const scrollTops = new WeakMap<Element, number>();
  const clientHeightSpy = vi
    .spyOn(HTMLElement.prototype, 'clientHeight', 'get')
    .mockImplementation(function getClientHeight(this: HTMLElement) {
      return this.dataset.testid === 'documents-table-scroller' ? tableHeight : viewportHeight;
    });
  const scrollHeightSpy = vi
    .spyOn(HTMLElement.prototype, 'scrollHeight', 'get')
    .mockImplementation(function getScrollHeight(this: HTMLElement) {
      return this.dataset.testid === 'documents-table-scroller'
        ? totalRows * rowHeight
        : viewportHeight;
    });
  const scrollTopGetSpy = vi
    .spyOn(HTMLElement.prototype, 'scrollTop', 'get')
    .mockImplementation(function getScrollTop(this: HTMLElement) {
      return scrollTops.get(this) ?? 0;
    });
  const scrollTopSetSpy = vi
    .spyOn(HTMLElement.prototype, 'scrollTop', 'set')
    .mockImplementation(function setScrollTop(this: HTMLElement, value: number) {
      scrollTops.set(this, value);
    });

  return {
    restore: () => {
      clientHeightSpy.mockRestore();
      scrollHeightSpy.mockRestore();
      scrollTopGetSpy.mockRestore();
      scrollTopSetSpy.mockRestore();
    },
  };
}

describe('DocumentList TanStack table scroll behavior', () => {
  let layoutMocks: LayoutMocks | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    layoutMocks?.restore();
    layoutMocks = null;
  });

  it.each([
    ['Safari laptop height', 620],
    ['desktop height', 900],
  ])('keeps lower row actions reachable in a bounded table scroller at %s', (_label, viewportHeight) => {
    layoutMocks = installMeasuredLayout(viewportHeight);
    const documents = Array.from({ length: totalRows }, (_, index) => {
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
        sx={{ display: 'flex', flexDirection: 'column', height: viewportHeight, minHeight: 0, overflow: 'hidden' }}
      >
        <Box data-testid="documents-jobs-panel">PDF jobs panel visible</Box>
        <DocumentList
          documents={documents}
          loading={false}
          totalCount={documents.length}
          onDelete={vi.fn()}
          onReembed={vi.fn()}
          onRefresh={vi.fn()}
          checkboxSelection
          filterBar={<Box>Filter bar visible</Box>}
        />
      </Box>,
    );

    expect(screen.getByTestId('documents-jobs-panel')).toBeVisible();
    expect(screen.getByText('Filter bar visible')).toBeVisible();

    const scrollRegion = screen.getByTestId('documents-table-scroll-region');
    expect(scrollRegion).toHaveStyle({ overflow: 'hidden' });

    const tableScroller = screen.getByTestId('documents-table-scroller');
    expect(tableScroller).toHaveStyle({ overflow: 'auto' });
    expect(tableScroller.clientHeight).toBeGreaterThan(0);
    expect(tableScroller.scrollHeight).toBeGreaterThan(tableScroller.clientHeight);

    tableScroller.scrollTop = tableScroller.scrollHeight - tableScroller.clientHeight;
    fireEvent.scroll(tableScroller);

    const lowerRow = screen.getByText('doc-48.pdf').closest('tr');
    expect(lowerRow).not.toBeNull();
    expect(within(lowerRow as HTMLElement).getByTestId('VisibilityIcon')).toBeInTheDocument();
    expect(tableScroller.scrollTop).toBeGreaterThan(0);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Box } from '@mui/material';
import { fireEvent, render, screen, waitFor, within } from '../../test/test-utils';
import DocumentList from './DocumentList';
import type { DocumentSummary } from '../../services/weaviate';

const refetchHealthMock = vi.fn();
const openCurationWorkspaceMock = vi.fn();

const createTestDocument = (overrides: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: '1',
  filename: 'test-document.pdf',
  fileSize: 1024000,
  creationDate: '2024-01-01T00:00:00.000Z',
  lastAccessedDate: '2024-01-02T00:00:00.000Z',
  processingStatus: 'completed',
  embeddingStatus: 'completed',
  chunkCount: 10,
  vectorCount: 100,
  metadata: {
    pageCount: 5,
    author: 'Test Author',
    title: 'Test Document',
    checksum: 'abc123',
    documentType: 'research',
    lastProcessedStage: 'completed',
  },
  ...overrides,
});

vi.mock('../../lib/globalNotifications', () => ({
  emitGlobalToast: vi.fn(),
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
      data: {
        status: 'healthy',
        last_checked: '2026-03-05T00:00:00Z',
      },
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: refetchHealthMock,
    }),
  };
});

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

type LayoutMocks = {
  restore: () => void;
};

const rowHeight = 52;
const totalRows = 48;

function makeRect(width: number, height: number): DOMRect {
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    bottom: height,
    right: width,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect;
}

function installMeasuredLayout(viewportHeight: number): LayoutMocks {
  const tableHeight = viewportHeight === 620 ? 300 : 580;
  const width = viewportHeight === 620 ? 1024 : 1280;
  const scrollTops = new WeakMap<Element, number>();
  const originalResizeObserver = globalThis.ResizeObserver;

  const measureElement = (element: HTMLElement): { width: number; height: number } => {
    if (element.dataset.testid === 'documents-scroll-test-shell') {
      return { width, height: viewportHeight };
    }

    if (
      element.dataset.testid === 'documents-table-scroll-region' ||
      element.classList.contains('MuiDataGrid-root') ||
      element.classList.contains('MuiDataGrid-main') ||
      element.classList.contains('MuiDataGrid-virtualScroller')
    ) {
      return { width: width - 48, height: tableHeight };
    }

    if (element.classList.contains('MuiDataGrid-columnHeaders')) {
      return { width: width - 48, height: 56 };
    }

    if (element.classList.contains('MuiDataGrid-footerContainer')) {
      return { width: width - 48, height: 52 };
    }

    if (element.classList.contains('MuiDataGrid-row')) {
      return { width: width - 48, height: rowHeight };
    }

    return { width: width - 48, height: Math.min(viewportHeight, 720) };
  };

  const getBoundingClientRectSpy = vi
    .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    .mockImplementation(function getMeasuredRect(this: HTMLElement) {
      const measured = measureElement(this);
      return makeRect(measured.width, measured.height);
    });

  const clientHeightSpy = vi
    .spyOn(HTMLElement.prototype, 'clientHeight', 'get')
    .mockImplementation(function getClientHeight(this: HTMLElement) {
      return measureElement(this).height;
    });

  const clientWidthSpy = vi
    .spyOn(HTMLElement.prototype, 'clientWidth', 'get')
    .mockImplementation(function getClientWidth(this: HTMLElement) {
      return measureElement(this).width;
    });

  const offsetHeightSpy = vi
    .spyOn(HTMLElement.prototype, 'offsetHeight', 'get')
    .mockImplementation(function getOffsetHeight(this: HTMLElement) {
      return measureElement(this).height;
    });

  const offsetWidthSpy = vi
    .spyOn(HTMLElement.prototype, 'offsetWidth', 'get')
    .mockImplementation(function getOffsetWidth(this: HTMLElement) {
      return measureElement(this).width;
    });

  const scrollHeightSpy = vi
    .spyOn(HTMLElement.prototype, 'scrollHeight', 'get')
    .mockImplementation(function getScrollHeight(this: HTMLElement) {
      if (this.classList.contains('MuiDataGrid-virtualScroller')) {
        return totalRows * rowHeight;
      }

      return measureElement(this).height;
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

  class MeasuredResizeObserver implements ResizeObserver {
    private callback: ResizeObserverCallback;

    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
    }

    disconnect(): void {}

    observe(target: Element): void {
      const measured = measureElement(target as HTMLElement);
      const contentRect = makeRect(measured.width, measured.height);
      const size = {
        inlineSize: measured.width,
        blockSize: measured.height,
      } as ResizeObserverSize;
      this.callback(
        [
          {
            target,
            contentRect,
            borderBoxSize: [size],
            contentBoxSize: [size],
            devicePixelContentBoxSize: [size],
          } as ResizeObserverEntry,
        ],
        this
      );
    }

    takeRecords(): ResizeObserverEntry[] {
      return [];
    }

    unobserve(_target: Element): void {}
  }

  globalThis.ResizeObserver = MeasuredResizeObserver as typeof ResizeObserver;

  return {
    restore: () => {
      globalThis.ResizeObserver = originalResizeObserver;
      getBoundingClientRectSpy.mockRestore();
      clientHeightSpy.mockRestore();
      clientWidthSpy.mockRestore();
      offsetHeightSpy.mockRestore();
      offsetWidthSpy.mockRestore();
      scrollHeightSpy.mockRestore();
      scrollTopGetSpy.mockRestore();
      scrollTopSetSpy.mockRestore();
    },
  };
}

describe('DocumentList real DataGrid scroll behavior', () => {
  let layoutMocks: LayoutMocks | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    refetchHealthMock.mockReset();
    openCurationWorkspaceMock.mockReset();
  });

  afterEach(() => {
    layoutMocks?.restore();
    layoutMocks = null;
  });

  it.each([
    ['Safari laptop height', 620],
    ['desktop height', 900],
  ])('keeps lower row actions reachable in a bounded DataGrid scroller at %s', async (_label, viewportHeight) => {
    layoutMocks = installMeasuredLayout(viewportHeight);
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
          checkboxSelection={true}
          filterBar={<Box>Filter bar visible</Box>}
        />
      </Box>
    );

    expect(screen.getByTestId('documents-jobs-panel')).toBeVisible();
    expect(screen.getByText('Filter bar visible')).toBeVisible();

    const scrollRegion = screen.getByTestId('documents-table-scroll-region');
    expect(scrollRegion).toHaveStyle({ overflow: 'hidden' });

    const virtualScroller = await waitFor(() => {
      const element = scrollRegion.querySelector<HTMLElement>('.MuiDataGrid-virtualScroller');
      expect(element).not.toBeNull();
      expect(element!.clientHeight).toBeGreaterThan(0);
      expect(element!.scrollHeight).toBeGreaterThan(element!.clientHeight);
      return element!;
    });

    expect(screen.queryByText('doc-48.pdf')).not.toBeInTheDocument();

    virtualScroller.scrollTop = virtualScroller.scrollHeight - virtualScroller.clientHeight;
    fireEvent.scroll(virtualScroller);

    const lowerRowCell = await screen.findByText('doc-48.pdf');
    const lowerRow = lowerRowCell.closest('[role="row"]');
    expect(lowerRow).not.toBeNull();
    expect(within(lowerRow as HTMLElement).getByTestId('VisibilityIcon')).toBeInTheDocument();
    expect(virtualScroller.scrollTop).toBeGreaterThan(0);
  });
});

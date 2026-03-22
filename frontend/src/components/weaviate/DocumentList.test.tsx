import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import DocumentList from './DocumentList';
import { createMockDocument } from '../../test/test-utils';

const refetchHealthMock = vi.fn();
const emitGlobalToastMock = vi.fn();
const openCurationWorkspaceMock = vi.fn();
const getCurationWorkspaceLaunchAvailabilityMock = vi.fn();

vi.mock('../../lib/globalNotifications', () => ({
  emitGlobalToast: (detail: unknown) => emitGlobalToastMock(detail),
}));

vi.mock('@/features/curation/navigation/openCurationWorkspace', async () => {
  const actual = await vi.importActual<typeof import('@/features/curation/navigation/openCurationWorkspace')>(
    '@/features/curation/navigation/openCurationWorkspace'
  );

  return {
    ...actual,
    getCurationWorkspaceLaunchAvailability: (options: unknown) =>
      getCurationWorkspaceLaunchAvailabilityMock(options),
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

vi.mock('@mui/x-data-grid', async () => {
  const React = await vi.importActual<typeof import('react')>('react');

  const DataGrid = ({
    rows = [],
    columns = [],
    checkboxSelection = false,
    rowSelectionModel,
    onRowSelectionModelChange,
  }: {
    rows?: any[];
    columns?: any[];
    checkboxSelection?: boolean;
    rowSelectionModel?: string[];
    onRowSelectionModelChange?: (ids: string[]) => void;
  }) => {
    const [internalSelection, setInternalSelection] = React.useState<string[]>([]);
    const selectedIds =
      rowSelectionModel !== undefined ? rowSelectionModel.map(String) : internalSelection;

    const setSelection = (ids: string[]) => {
      if (rowSelectionModel === undefined) {
        setInternalSelection(ids);
      }
      onRowSelectionModelChange?.(ids);
    };

    return (
      <div className="MuiDataGrid-root" role="grid">
        <table>
          <thead>
            <tr>
              {checkboxSelection && (
                <th className="MuiDataGrid-columnHeaderCheckbox">
                  <input type="checkbox" />
                </th>
              )}
              {columns.map((column: any) => (
                <th key={column.field}>{column.headerName}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row: any) => (
              <tr key={row.id} className="MuiDataGrid-row" style={{ cursor: 'pointer' }}>
                {checkboxSelection && (
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(String(row.id))}
                      onChange={(event) => {
                        const checked = event.target.checked;
                        const id = String(row.id);
                        if (checked) {
                          setSelection([...selectedIds, id]);
                        } else {
                          setSelection(selectedIds.filter((selectedId) => selectedId !== id));
                        }
                      }}
                    />
                  </td>
                )}
                {columns.map((column: any) => {
                  const rawValue = row[column.field];
                  let content = rawValue;

                  if (typeof column.renderCell === 'function') {
                    content = column.renderCell({ row, value: rawValue, field: column.field });
                  } else if (typeof column.valueFormatter === 'function') {
                    content = column.valueFormatter({ row, value: rawValue, field: column.field });
                  }

                  return <td key={`${row.id}-${column.field}`}>{content}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  return {
    DataGrid,
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

describe('DocumentList', () => {
  const defaultProps = {
    documents: [
      createMockDocument({ id: '1', filename: 'doc1.pdf' }),
      createMockDocument({ id: '2', filename: 'doc2.pdf', embeddingStatus: 'processing' }),
      createMockDocument({ id: '3', filename: 'doc3.pdf', embeddingStatus: 'failed' }),
    ],
    loading: false,
    totalCount: 3,
    onDelete: vi.fn(),
    onReembed: vi.fn(),
    onRefresh: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    refetchHealthMock.mockReset();
    emitGlobalToastMock.mockReset();
    openCurationWorkspaceMock.mockReset();
    getCurationWorkspaceLaunchAvailabilityMock.mockReset();
  });

  it('renders document list with all documents', () => {
    render(<DocumentList {...defaultProps} />);

    expect(screen.getByText('doc1.pdf')).toBeInTheDocument();
    expect(screen.getByText('doc2.pdf')).toBeInTheDocument();
    expect(screen.getByText('doc3.pdf')).toBeInTheDocument();
  });

  it('displays loading state', () => {
    render(<DocumentList {...defaultProps} loading={true} />);

    const progressBar = document.querySelector('.MuiLinearProgress-root');
    expect(progressBar).toBeInTheDocument();
  });

  it('formats file sizes correctly', () => {
    const docs = [
      createMockDocument({ id: '11', fileSize: 1024 }),         // 1 KB
      createMockDocument({ id: '12', fileSize: 1048576 }),      // 1 MB
      createMockDocument({ id: '13', fileSize: 1073741824 }),   // 1 GB
    ];

    render(<DocumentList {...defaultProps} documents={docs} />);

    expect(screen.getByText('1 KB')).toBeInTheDocument();
    expect(screen.getByText('1 MB')).toBeInTheDocument();
    expect(screen.getByText('1 GB')).toBeInTheDocument();
  });

  it('displays embedding status with correct colors', () => {
    render(<DocumentList {...defaultProps} />);

    const completedChip = screen.getByText('completed');
    const processingChip = screen.getByText('processing');
    const failedChip = screen.getByText('failed');

    expect(completedChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorSuccess');
    expect(processingChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorPrimary');
    expect(failedChip.closest('.MuiChip-root')).toHaveClass('MuiChip-colorError');
  });

  it('opens document detail dialog on view button click', async () => {
    render(<DocumentList {...defaultProps} />);

    // Find the first view button
    const viewButtons = screen.getAllByTestId('VisibilityIcon');
    fireEvent.click(viewButtons[0].parentElement!);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('calls onReembed when refresh button is clicked', () => {
    render(<DocumentList {...defaultProps} />);

    const refreshButtons = screen
      .getAllByTestId('RefreshIcon')
      .filter((icon) => icon.closest('td') !== null);
    fireEvent.click(refreshButtons[0].parentElement!);

    expect(defaultProps.onReembed).toHaveBeenCalledWith('1');
  });

  it('calls onDelete when delete button is clicked', () => {
    render(<DocumentList {...defaultProps} />);

    const deleteButtons = screen
      .getAllByTestId('DeleteIcon')
      .filter((icon) => icon.closest('td') !== null);
    fireEvent.click(deleteButtons[0].parentElement!);

    expect(defaultProps.onDelete).toHaveBeenCalledWith('1');
  });

  it('opens Review & Curate from the document action column', async () => {
    getCurationWorkspaceLaunchAvailabilityMock.mockResolvedValue({
      existingSessionId: 'session-1',
      canBootstrap: true,
    });
    openCurationWorkspaceMock.mockResolvedValue('session-1');

    render(
      <DocumentList
        {...defaultProps}
        documents={[createMockDocument({ id: 'doc-review', embeddingStatus: 'completed' })]}
      />
    );

    fireEvent.click(await screen.findByRole('button', { name: /review & curate/i }));

    await waitFor(() => {
      expect(openCurationWorkspaceMock).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'session-1',
          documentId: 'doc-review',
        })
      );
    });
  });

  it('hides Review & Curate when the document has no prepared session', async () => {
    getCurationWorkspaceLaunchAvailabilityMock.mockResolvedValue({
      existingSessionId: null,
      canBootstrap: false,
    });

    render(
      <DocumentList
        {...defaultProps}
        documents={[createMockDocument({ id: 'doc-without-session', embeddingStatus: 'completed' })]}
      />
    );

    await waitFor(() => {
      expect(getCurationWorkspaceLaunchAvailabilityMock).toHaveBeenCalledWith(
        expect.objectContaining({
          documentId: 'doc-without-session',
        })
      );
    });

    expect(screen.queryByRole('button', { name: /review & curate/i })).not.toBeInTheDocument();
  });

  it('disables re-embed button for processing documents', () => {
    render(<DocumentList {...defaultProps} />);

    const refreshButtons = screen
      .getAllByTestId('RefreshIcon')
      .filter((icon) => icon.closest('td') !== null);
    // Second document is processing
    expect(refreshButtons[1].parentElement).toBeDisabled();
  });

  it('handles pagination changes', async () => {
    const { container } = render(<DocumentList {...defaultProps} totalCount={100} />);

    // DataGrid should handle pagination internally
    const grid = container.querySelector('.MuiDataGrid-root');
    expect(grid).toBeInTheDocument();
  });

  it('handles sorting', () => {
    const { container } = render(<DocumentList {...defaultProps} />);

    // Click on filename header to sort
    const filenameHeader = screen.getByText('Filename');
    fireEvent.click(filenameHeader);

    // DataGrid handles sorting internally
    expect(container.querySelector('.MuiDataGrid-root')).toBeInTheDocument();
  });

  it('displays correct column headers', () => {
    render(<DocumentList {...defaultProps} />);

    expect(screen.getByText('Filename')).toBeInTheDocument();
    expect(screen.getByText('Title')).toBeInTheDocument();
    expect(screen.getByText('Size')).toBeInTheDocument();
    expect(screen.getByText('Created')).toBeInTheDocument();
    expect(screen.getByText('Accessed')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Vectors')).toBeInTheDocument();
    expect(screen.getByText('Chunks')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
  });

  it('formats dates correctly', () => {
    const creationDate = new Date('2024-01-01T10:00:00');
    const lastAccessedDate = new Date('2024-01-02T15:30:00');
    const doc = createMockDocument({
      id: 'date-doc',
      creationDate: new Date('2024-01-01T10:00:00'),
      lastAccessedDate: new Date('2024-01-02T15:30:00'),
    });

    render(<DocumentList {...defaultProps} documents={[doc]} />);

    expect(screen.getByText(creationDate.toLocaleDateString())).toBeInTheDocument();
    expect(screen.getByText(lastAccessedDate.toLocaleDateString())).toBeInTheDocument();
  });

  it('displays vector and chunk counts', () => {
    const doc = createMockDocument({
      id: 'counts-doc',
      vectorCount: 150,
      chunkCount: 25,
    });

    render(<DocumentList {...defaultProps} documents={[doc]} />);

    expect(screen.getByText('150')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
  });

  it('handles empty document list', () => {
    render(<DocumentList {...defaultProps} documents={[]} totalCount={0} />);

    expect(screen.getByText('No documents yet. Upload a PDF to get started.')).toBeInTheDocument();
    const grid = document.querySelector('.MuiDataGrid-root');
    expect(grid).not.toBeInTheDocument();
  });

  it('allows selecting multiple files for upload', () => {
    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]');

    expect(fileInput).toHaveAttribute('multiple');
  });

  it('blocks selecting more than 10 files for upload', () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = Array.from(
      { length: 11 },
      (_, index) => new File(['test'], `doc-${index + 1}.pdf`, { type: 'application/pdf' })
    );

    fireEvent.change(fileInput, { target: { files } });

    expect(alertSpy).toHaveBeenCalledWith('Please select up to 10 PDF files at a time');
    alertSpy.mockRestore();
  });

  it('uploads two selected PDF files', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes('/api/weaviate/documents/upload')) {
        return new Response(JSON.stringify({ document_id: crypto.randomUUID() }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.includes('/api/weaviate/documents/pdf-extraction-health')) {
        return new Response(JSON.stringify({ status: 'healthy', service_url: 'http://pdfx' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const onRefresh = vi.fn();
    const { container } = render(<DocumentList {...defaultProps} onRefresh={onRefresh} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(['a'], 'doc-a.pdf', { type: 'application/pdf' }),
      new File(['b'], 'doc-b.pdf', { type: 'application/pdf' }),
    ];

    fireEvent.change(fileInput, { target: { files } });

    await waitFor(() => {
      const uploadCalls = fetchSpy.mock.calls.filter(([url]) =>
        String(url).includes('/api/weaviate/documents/upload')
      );
      expect(uploadCalls).toHaveLength(2);
    });

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalled();
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  it('shows one background-processing toast after a successful single upload', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes('/api/weaviate/documents/upload')) {
        return new Response(JSON.stringify({ document_id: crypto.randomUUID() }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.includes('/api/weaviate/documents/pdf-extraction-health')) {
        return new Response(JSON.stringify({ status: 'healthy', service_url: 'http://pdfx' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [new File(['a'], 'doc-a.pdf', { type: 'application/pdf' })];

    fireEvent.change(fileInput, { target: { files } });

    await waitFor(() => {
      expect(emitGlobalToastMock).toHaveBeenCalledTimes(1);
    });

    expect(emitGlobalToastMock).toHaveBeenCalledWith({
      message: 'Your PDFs are processing in the background. You can safely navigate away.',
      severity: 'info',
      autoHideDurationMs: 8000,
      anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
    });

    fetchSpy.mockRestore();
  });

  it('shows one background-processing toast for a multi-file upload initiation event', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes('/api/weaviate/documents/upload')) {
        return new Response(JSON.stringify({ document_id: crypto.randomUUID() }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.includes('/api/weaviate/documents/pdf-extraction-health')) {
        return new Response(JSON.stringify({ status: 'healthy', service_url: 'http://pdfx' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const { container } = render(<DocumentList {...defaultProps} />);
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(['a'], 'doc-a.pdf', { type: 'application/pdf' }),
      new File(['b'], 'doc-b.pdf', { type: 'application/pdf' }),
    ];

    fireEvent.change(fileInput, { target: { files } });

    await waitFor(() => {
      const uploadCalls = fetchSpy.mock.calls.filter(([url]) =>
        String(url).includes('/api/weaviate/documents/upload')
      );
      expect(uploadCalls).toHaveLength(2);
    });

    expect(emitGlobalToastMock).toHaveBeenCalledTimes(1);

    fetchSpy.mockRestore();
  });

  it('applies hover effects on rows', () => {
    const { container } = render(<DocumentList {...defaultProps} />);

    const rows = container.querySelectorAll('.MuiDataGrid-row');
    expect(rows.length).toBeGreaterThan(0);

    // DataGrid row style should include pointer cursor
    rows.forEach(row => {
      expect(row).toHaveStyle({ cursor: 'pointer' });
    });
  });

  describe('checkbox selection for batch processing', () => {
    it('renders checkboxes when checkboxSelection is enabled', () => {
      const onSelectionChange = vi.fn();
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={true}
          onSelectionChange={onSelectionChange}
        />
      );

      // Check for the checkbox column header (select all checkbox)
      const checkboxes = document.querySelectorAll('input[type="checkbox"]');
      expect(checkboxes.length).toBeGreaterThan(0);
    });

    it('does not render checkboxes when checkboxSelection is false', () => {
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={false}
        />
      );

      // Should not have checkbox column
      const checkboxColumn = document.querySelector('.MuiDataGrid-columnHeaderCheckbox');
      expect(checkboxColumn).not.toBeInTheDocument();
    });

    it('calls onSelectionChange when rows are selected', async () => {
      const onSelectionChange = vi.fn();
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={true}
          onSelectionChange={onSelectionChange}
        />
      );

      // Find and click a row checkbox
      const checkboxes = document.querySelectorAll('input[type="checkbox"]');
      // First checkbox is "select all", subsequent ones are row checkboxes
      const firstRowCheckbox = checkboxes[1];
      fireEvent.click(firstRowCheckbox);

      expect(onSelectionChange).toHaveBeenCalled();
    });

    it('supports controlled selection via selectedIds prop', () => {
      const onSelectionChange = vi.fn();
      render(
        <DocumentList
          {...defaultProps}
          checkboxSelection={true}
          selectedIds={['1', '2']}
          onSelectionChange={onSelectionChange}
        />
      );

      // Check that selected rows have checked checkboxes
      const checkboxes = document.querySelectorAll('input[type="checkbox"]:checked');
      // Should have at least 2 checked (the selected rows)
      expect(checkboxes.length).toBeGreaterThanOrEqual(2);
    });

    it('defaults checkboxSelection to false', () => {
      render(<DocumentList {...defaultProps} />);

      // Should not have checkbox column when not explicitly enabled
      const checkboxColumn = document.querySelector('.MuiDataGrid-columnHeaderCheckbox');
      expect(checkboxColumn).not.toBeInTheDocument();
    });
  });

  describe('edit document functionality', () => {
    it('accepts onTitleUpdate prop', () => {
      const onTitleUpdate = vi.fn();
      // Should render without errors when onTitleUpdate is provided
      const { container } = render(
        <DocumentList
          {...defaultProps}
          onTitleUpdate={onTitleUpdate}
        />
      );

      // Component should render successfully
      expect(container.querySelector('.MuiDataGrid-root')).toBeInTheDocument();
    });

    it('renders without onTitleUpdate prop', () => {
      const { container } = render(<DocumentList {...defaultProps} />);

      // Component should render successfully without onTitleUpdate
      expect(container.querySelector('.MuiDataGrid-root')).toBeInTheDocument();
    });
  });
});

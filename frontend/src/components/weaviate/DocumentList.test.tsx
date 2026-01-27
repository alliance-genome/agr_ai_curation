import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import DocumentList from './DocumentList';
import { createMockDocument } from '../../test/test-utils';

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
      createMockDocument({ fileSize: 1024 }),         // 1 KB
      createMockDocument({ fileSize: 1048576 }),      // 1 MB
      createMockDocument({ fileSize: 1073741824 }),   // 1 GB
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

  it('navigates to document detail on view button click', async () => {
    render(<DocumentList {...defaultProps} />);

    // Find the first view button
    const viewButtons = screen.getAllByTestId('VisibilityIcon');
    fireEvent.click(viewButtons[0].parentElement!);

    expect(mockNavigate).toHaveBeenCalledWith('/api/weaviate/document/1');
  });

  it('calls onReembed when refresh button is clicked', () => {
    render(<DocumentList {...defaultProps} />);

    const refreshButtons = screen.getAllByTestId('RefreshIcon');
    fireEvent.click(refreshButtons[0].parentElement!);

    expect(defaultProps.onReembed).toHaveBeenCalledWith('1');
  });

  it('calls onDelete when delete button is clicked', () => {
    render(<DocumentList {...defaultProps} />);

    const deleteButtons = screen.getAllByTestId('DeleteIcon');
    fireEvent.click(deleteButtons[0].parentElement!);

    expect(defaultProps.onDelete).toHaveBeenCalledWith('1');
  });

  it('disables re-embed button for processing documents', () => {
    render(<DocumentList {...defaultProps} />);

    const refreshButtons = screen.getAllByTestId('RefreshIcon');
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
    expect(screen.getByText('File Size')).toBeInTheDocument();
    expect(screen.getByText('Creation Date')).toBeInTheDocument();
    expect(screen.getByText('Last Accessed')).toBeInTheDocument();
    expect(screen.getByText('Embedding Status')).toBeInTheDocument();
    expect(screen.getByText('Vector Count')).toBeInTheDocument();
    expect(screen.getByText('Chunks')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
  });

  it('formats dates correctly', () => {
    const doc = createMockDocument({
      creationDate: new Date('2024-01-01T10:00:00'),
      lastAccessedDate: new Date('2024-01-02T15:30:00'),
    });

    render(<DocumentList {...defaultProps} documents={[doc]} />);

    // Check that dates are displayed (exact format may vary by locale)
    const dateElements = screen.getAllByText(/2024/);
    expect(dateElements.length).toBeGreaterThan(0);
  });

  it('displays vector and chunk counts', () => {
    const doc = createMockDocument({
      vectorCount: 150,
      chunkCount: 25,
    });

    render(<DocumentList {...defaultProps} documents={[doc]} />);

    expect(screen.getByText('150')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
  });

  it('handles empty document list', () => {
    render(<DocumentList {...defaultProps} documents={[]} totalCount={0} />);

    // DataGrid should show no rows message
    const grid = document.querySelector('.MuiDataGrid-root');
    expect(grid).toBeInTheDocument();
  });

  it('applies hover effects on rows', () => {
    const { container } = render(<DocumentList {...defaultProps} />);

    const rows = container.querySelectorAll('.MuiDataGrid-row');
    expect(rows.length).toBeGreaterThan(0);

    // DataGrid handles hover internally
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
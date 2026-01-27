import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../../test/test-utils';
import EditDocumentDialog from './EditDocumentDialog';

describe('EditDocumentDialog', () => {
  const defaultProps = {
    open: true,
    documentId: 'test-doc-123',
    currentTitle: 'Original Title',
    onClose: vi.fn(),
    onSave: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the dialog when open', () => {
    render(<EditDocumentDialog {...defaultProps} />);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Edit Document')).toBeInTheDocument();
  });

  it('does not render the dialog when closed', () => {
    render(<EditDocumentDialog {...defaultProps} open={false} />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('displays the current title in the text field', () => {
    render(<EditDocumentDialog {...defaultProps} />);

    const titleInput = screen.getByLabelText(/title/i);
    expect(titleInput).toHaveValue('Original Title');
  });

  it('displays empty text field when currentTitle is null', () => {
    render(<EditDocumentDialog {...defaultProps} currentTitle={null} />);

    const titleInput = screen.getByLabelText(/title/i);
    expect(titleInput).toHaveValue('');
  });

  it('calls onClose when Cancel button is clicked', () => {
    render(<EditDocumentDialog {...defaultProps} />);

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));

    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it('calls onClose when close icon is clicked', () => {
    render(<EditDocumentDialog {...defaultProps} />);

    const closeButton = screen.getByLabelText(/close/i);
    fireEvent.click(closeButton);

    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it('calls onSave with new title when Save button is clicked', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<EditDocumentDialog {...defaultProps} onSave={onSave} />);

    const titleInput = screen.getByLabelText(/title/i);
    fireEvent.change(titleInput, { target: { value: 'New Title' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith('test-doc-123', 'New Title');
    });
  });

  it('shows loading state while saving', async () => {
    const onSave = vi.fn().mockImplementation(() => new Promise(() => {})); // Never resolves
    render(<EditDocumentDialog {...defaultProps} onSave={onSave} />);

    const titleInput = screen.getByLabelText(/title/i);
    fireEvent.change(titleInput, { target: { value: 'New Title' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
    });
  });

  it('shows error message when save fails', async () => {
    const onSave = vi.fn().mockRejectedValue(new Error('Save failed'));
    render(<EditDocumentDialog {...defaultProps} onSave={onSave} />);

    const titleInput = screen.getByLabelText(/title/i);
    fireEvent.change(titleInput, { target: { value: 'New Title' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/save failed/i)).toBeInTheDocument();
    });
  });

  it('closes dialog after successful save', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<EditDocumentDialog {...defaultProps} onSave={onSave} />);

    const titleInput = screen.getByLabelText(/title/i);
    fireEvent.change(titleInput, { target: { value: 'New Title' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(defaultProps.onClose).toHaveBeenCalled();
    });
  });

  it('enforces max length of 255 characters', () => {
    render(<EditDocumentDialog {...defaultProps} />);

    const titleInput = screen.getByLabelText(/title/i);
    expect(titleInput).toHaveAttribute('maxLength', '255');
  });

  it('resets form state when dialog reopens', () => {
    const { rerender } = render(<EditDocumentDialog {...defaultProps} open={false} />);

    // Open dialog with different title
    rerender(<EditDocumentDialog {...defaultProps} open={true} currentTitle="Different Title" />);

    const titleInput = screen.getByLabelText(/title/i);
    expect(titleInput).toHaveValue('Different Title');
  });
});

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '../../test/test-utils';
import WeaviateNavIcon from './WeaviateNavIcon';

// Mock react-router-dom
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('WeaviateNavIcon', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the database icon and text', () => {
    render(<WeaviateNavIcon />);

    const iconButton = screen.getByRole('button', { name: /documents/i });
    expect(iconButton).toBeInTheDocument();
    const text = screen.getByText('Documents');
    expect(text).toBeInTheDocument();
  });

  it('navigates to /weaviate when clicked', () => {
    render(<WeaviateNavIcon />);

    const container = screen.getByRole('button', { name: /documents/i });
    fireEvent.click(container!);

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate');
  });

  it('applies hover styles', () => {
    render(<WeaviateNavIcon />);

    const container = screen.getByRole('button', { name: /documents/i });
    expect(container).toHaveStyle({ cursor: 'pointer' });
  });

  it('has correct accessibility attributes', () => {
    render(<WeaviateNavIcon />);

    const iconButton = screen.getByRole('button');
    expect(iconButton).toHaveAttribute('aria-label', 'Documents');
  });

  it('maintains consistent layout', () => {
    const { container } = render(<WeaviateNavIcon />);

    // Check flex layout
    const box = container.firstChild;
    expect(box).toHaveStyle({ display: 'flex', alignItems: 'center' });
  });

  it('handles keyboard navigation', () => {
    render(<WeaviateNavIcon />);

    const container = screen.getByRole('button', { name: /documents/i });

    // Simulate Enter key press
    fireEvent.keyDown(container!, { key: 'Enter', code: 'Enter' });

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate');
  });

  it('renders with correct icon size', () => {
    render(<WeaviateNavIcon />);

    const iconButton = screen.getByRole('button', { name: /documents/i });
    expect(iconButton).toHaveClass('MuiBox-root');

    // Check that icon is present
    const icon = screen.getByTestId('StorageIcon');
    expect(icon).toBeInTheDocument();
  });

  it('has proper spacing between icon and text', () => {
    render(<WeaviateNavIcon />);

    const text = screen.getByText('Documents');
    const icon = screen.getByTestId('StorageIcon');
    expect(text).toBeInTheDocument();
    expect(icon).toBeInTheDocument();
  });
});

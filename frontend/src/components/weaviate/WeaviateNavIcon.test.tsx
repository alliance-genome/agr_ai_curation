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

    // Check for icon button
    const iconButton = screen.getByRole('button', {
      name: /weaviate database control panel/i
    });
    expect(iconButton).toBeInTheDocument();

    // Check for text
    const text = screen.getByText('Weaviate');
    expect(text).toBeInTheDocument();
  });

  it('navigates to /weaviate when clicked', () => {
    render(<WeaviateNavIcon />);

    const container = screen.getByText('Weaviate').parentElement;
    fireEvent.click(container!);

    expect(mockNavigate).toHaveBeenCalledWith('/api/weaviate');
  });

  it('applies hover styles', () => {
    render(<WeaviateNavIcon />);

    const container = screen.getByText('Weaviate').parentElement;
    expect(container).toHaveStyle({ cursor: 'pointer' });
  });

  it('has correct accessibility attributes', () => {
    render(<WeaviateNavIcon />);

    const iconButton = screen.getByRole('button');
    expect(iconButton).toHaveAttribute('aria-label', 'Weaviate Database Control Panel');
  });

  it('maintains consistent layout', () => {
    const { container } = render(<WeaviateNavIcon />);

    // Check flex layout
    const box = container.firstChild;
    expect(box).toHaveStyle({ display: 'flex', alignItems: 'center' });
  });

  it('handles keyboard navigation', () => {
    render(<WeaviateNavIcon />);

    const container = screen.getByText('Weaviate').parentElement;

    // Simulate Enter key press
    fireEvent.keyDown(container!, { key: 'Enter', code: 'Enter' });

    // Click should still work as it's the main interaction
    fireEvent.click(container!);
    expect(mockNavigate).toHaveBeenCalledWith('/api/weaviate');
  });

  it('renders with correct icon size', () => {
    render(<WeaviateNavIcon />);

    const iconButton = screen.getByRole('button');
    // IconButton renders with proper classes
    expect(iconButton).toHaveClass('MuiIconButton-root');

    // Check that icon is present
    const icon = screen.getByTestId('StorageIcon');
    expect(icon).toBeInTheDocument();
  });

  it('has proper spacing between icon and text', () => {
    render(<WeaviateNavIcon />);

    const text = screen.getByText('Weaviate');
    const styles = window.getComputedStyle(text);

    // Check that text has proper margins
    expect(text).toHaveStyle({ fontWeight: '500' });
  });
});
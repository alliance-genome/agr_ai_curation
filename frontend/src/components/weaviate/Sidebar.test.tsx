import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import { ThemeProvider, createTheme } from '@mui/material';
import Sidebar from './Sidebar';

// Mock react-router-dom navigation
const mockNavigate = vi.fn();
const mockLocation = { pathname: '/weaviate/documents' };

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => mockLocation,
  };
});

// Mock useMediaQuery for testing mobile/desktop behavior
const mockUseMediaQuery = vi.fn();
vi.mock('@mui/material', async () => {
  const actual = await vi.importActual('@mui/material');
  return {
    ...actual,
    useMediaQuery: () => mockUseMediaQuery(),
  };
});

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseMediaQuery.mockReturnValue(false); // Default to desktop
    mockLocation.pathname = '/weaviate/documents';
  });

  const renderWithTheme = (ui: React.ReactElement) => {
    const theme = createTheme();
    return render(
      <ThemeProvider theme={theme}>
        {ui}
      </ThemeProvider>
    );
  };

  const getNavButton = (label: string): HTMLElement => {
    const buttons = screen.getAllByRole('button');
    const match = buttons.find((button) => within(button).queryByText(label));
    if (!match) {
      throw new Error(`Could not find nav button for label: ${label}`);
    }
    return match;
  };

  it('renders all navigation items', () => {
    renderWithTheme(<Sidebar />);

    expect(getNavButton('Documents')).toBeInTheDocument();
    expect(getNavButton('Dashboard')).toBeInTheDocument();
    expect(getNavButton('Settings')).toBeInTheDocument();
  });

  it('displays Weaviate title', () => {
    renderWithTheme(<Sidebar />);

    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument();
  });

  it('navigates to documents page', () => {
    renderWithTheme(<Sidebar />);

    const documentsButton = getNavButton('Documents');
    fireEvent.click(documentsButton);

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents');
  });

  it('navigates to dashboard page', () => {
    renderWithTheme(<Sidebar />);

    const dashboardButton = getNavButton('Dashboard');
    fireEvent.click(dashboardButton);

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/dashboard');
  });

  it('expands settings submenu', async () => {
    renderWithTheme(<Sidebar />);

    // Settings should be expanded by default
    expect(screen.getByText('Embeddings')).toBeInTheDocument();
    expect(screen.getByText('Database')).toBeInTheDocument();
    expect(screen.getByText('Schema')).toBeInTheDocument();
    expect(screen.getByText('Chunking')).toBeInTheDocument();
  });

  it('collapses and expands settings submenu', async () => {
    renderWithTheme(<Sidebar />);

    const settingsButton = getNavButton('Settings');

    // Click to collapse
    fireEvent.click(settingsButton);

    await waitFor(() => {
      expect(screen.queryByText('Embeddings')).not.toBeInTheDocument();
    });

    // Click to expand again
    fireEvent.click(settingsButton);

    await waitFor(() => {
      expect(screen.getByText('Embeddings')).toBeInTheDocument();
    });
  });

  it('navigates to settings subpages', () => {
    renderWithTheme(<Sidebar />);

    // Navigate to Embeddings
    const embeddingsButton = getNavButton('Embeddings');
    fireEvent.click(embeddingsButton);
    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/settings/embeddings');

    // Navigate to Database
    const databaseButton = getNavButton('Database');
    fireEvent.click(databaseButton);
    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/settings/database');

    // Navigate to Schema
    const schemaButton = getNavButton('Schema');
    fireEvent.click(schemaButton);
    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/settings/schema');

    // Navigate to Chunking
    const chunkingButton = getNavButton('Chunking');
    fireEvent.click(chunkingButton);
    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/settings/chunking');
  });

  it('highlights active route', () => {
    mockLocation.pathname = '/weaviate/settings/embeddings';
    renderWithTheme(<Sidebar />);

    // Check that the Embeddings item is selected
    const embeddingsButton = getNavButton('Embeddings');
    expect(embeddingsButton).toHaveClass('Mui-selected');
  });

  it('collapses sidebar with toggle button', async () => {
    renderWithTheme(<Sidebar />);

    // Find collapse button (ChevronLeft icon initially)
    const collapseButton = screen.getByTestId('ChevronLeftIcon').parentElement!;
    fireEvent.click(collapseButton);

    // Title should be hidden when collapsed
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Documents' })).not.toBeInTheDocument();
    });

    // Button should change to expand icon
    expect(screen.getByTestId('ChevronRightIcon')).toBeInTheDocument();
  });

  it('expands sidebar from collapsed state', async () => {
    renderWithTheme(<Sidebar />);

    // Collapse first
    const collapseButton = screen.getByTestId('ChevronLeftIcon').parentElement!;
    fireEvent.click(collapseButton);

    // Then expand
    const expandButton = screen.getByTestId('ChevronRightIcon').parentElement!;
    fireEvent.click(expandButton);

    // Title should be visible again
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument();
    });
  });

  it('renders as temporary drawer on mobile', () => {
    mockUseMediaQuery.mockReturnValue(true); // Mobile mode

    renderWithTheme(<Sidebar variant="permanent" />);

    // Drawer should be temporary on mobile even if variant is permanent
    const drawer = document.querySelector('.MuiDrawer-root');
    expect(drawer).toHaveClass('MuiDrawer-modal');
  });

  it('calls onToggle after navigation on mobile', () => {
    mockUseMediaQuery.mockReturnValue(true); // Mobile mode
    const onToggle = vi.fn();

    renderWithTheme(<Sidebar open={true} onToggle={onToggle} />);

    const documentsButton = getNavButton('Documents');
    fireEvent.click(documentsButton);

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents');
    expect(onToggle).toHaveBeenCalled();
  });

  it('does not call onToggle on desktop', () => {
    mockUseMediaQuery.mockReturnValue(false); // Desktop mode
    const onToggle = vi.fn();

    renderWithTheme(<Sidebar open={true} onToggle={onToggle} />);

    const documentsButton = getNavButton('Documents');
    fireEvent.click(documentsButton);

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents');
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('respects open prop', () => {
    const { rerender } = renderWithTheme(<Sidebar open={false} variant="temporary" />);

    // Content should not be visible when closed
    expect(screen.queryByRole('heading', { name: 'Documents' })).not.toBeInTheDocument();

    rerender(
      <ThemeProvider theme={createTheme()}>
        <Sidebar open={true} variant="temporary" />
      </ThemeProvider>
    );

    // Content should be visible when open
    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument();
  });

  it('uses custom width', () => {
    renderWithTheme(<Sidebar width={300} />);

    const drawer = document.querySelector('.MuiDrawer-paper');
    expect(drawer).toHaveStyle({ width: '300px' });
  });

  it('renders icons for navigation items', () => {
    renderWithTheme(<Sidebar />);

    expect(screen.getByTestId('DescriptionIcon')).toBeInTheDocument(); // Documents
    expect(screen.getByTestId('DashboardIcon')).toBeInTheDocument(); // Dashboard
    expect(screen.getByTestId('SettingsIcon')).toBeInTheDocument(); // Settings
  });

  it('renders icons for settings submenu', () => {
    renderWithTheme(<Sidebar />);

    expect(screen.getByTestId('CloudSyncIcon')).toBeInTheDocument(); // Embeddings
    expect(screen.getAllByTestId('StorageIcon').length).toBeGreaterThan(0); // Database/header
    expect(screen.getByTestId('SchemaIcon')).toBeInTheDocument(); // Schema
    expect(screen.getByTestId('TuneIcon')).toBeInTheDocument(); // Chunking
  });

  it('displays copyright notice', () => {
    renderWithTheme(<Sidebar />);

    expect(screen.getByText('© 2025 AI Curation System')).toBeInTheDocument();
  });

  it('hides copyright notice when collapsed', async () => {
    renderWithTheme(<Sidebar />);

    const collapseButton = screen.getByTestId('ChevronLeftIcon').parentElement!;
    fireEvent.click(collapseButton);

    await waitFor(() => {
      expect(screen.queryByText('© 2025 AI Curation System')).not.toBeInTheDocument();
    });
  });

  it('applies selected styles to active route', () => {
    mockLocation.pathname = '/weaviate/dashboard';
    renderWithTheme(<Sidebar />);

    const dashboardButton = getNavButton('Dashboard');
    expect(dashboardButton).toHaveClass('Mui-selected');
  });

  it('detects nested active routes', () => {
    mockLocation.pathname = '/weaviate/documents/123'; // Nested under /weaviate/documents
    renderWithTheme(<Sidebar />);

    const documentsButton = getNavButton('Documents');
    expect(documentsButton).toHaveClass('Mui-selected');
  });

  it('handles variant prop correctly', () => {
    const { rerender } = renderWithTheme(<Sidebar variant="permanent" />);

    let drawer = document.querySelector('.MuiDrawer-root');
    expect(drawer).toHaveClass('MuiDrawer-docked');

    rerender(
      <ThemeProvider theme={createTheme()}>
        <Sidebar variant="persistent" />
      </ThemeProvider>
    );

    drawer = document.querySelector('.MuiDrawer-root');
    expect(drawer).toHaveClass('MuiDrawer-docked');
  });

  it('does not navigate for parent items with children', () => {
    renderWithTheme(<Sidebar />);

    const settingsButton = getNavButton('Settings');
    fireEvent.click(settingsButton);

    // Should not navigate, only expand/collapse
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('shows expand/collapse icons for items with children', () => {
    renderWithTheme(<Sidebar />);

    // Settings is expanded by default
    expect(screen.getByTestId('ExpandLessIcon')).toBeInTheDocument();

    // Click to collapse
    const settingsButton = getNavButton('Settings');
    fireEvent.click(settingsButton);

    // Should show expand icon
    expect(screen.getByTestId('ExpandMoreIcon')).toBeInTheDocument();
  });

  it('hides submenu items when sidebar is collapsed', async () => {
    renderWithTheme(<Sidebar />);

    // Initially submenu is visible
    expect(screen.getByText('Embeddings')).toBeInTheDocument();

    // Collapse sidebar
    const collapseButton = screen.getByTestId('ChevronLeftIcon').parentElement!;
    fireEvent.click(collapseButton);

    // Submenu should be hidden when sidebar is collapsed
    await waitFor(() => {
      expect(screen.queryByText('Embeddings')).not.toBeInTheDocument();
    });
  });

  it('applies correct indentation for nested items', () => {
    renderWithTheme(<Sidebar />);

    const embeddingsButton = getNavButton('Embeddings');
    const documentsButton = getNavButton('Documents');

    // Nested items should have more padding
    expect(embeddingsButton).toHaveStyle({ paddingLeft: '24px' });
    expect(documentsButton).toHaveStyle({ paddingLeft: '20px' });
  });
});

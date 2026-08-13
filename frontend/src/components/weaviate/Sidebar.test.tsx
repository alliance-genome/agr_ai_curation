import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../test/test-utils';
import { ThemeProvider, createTheme } from '@mui/material';
import Sidebar from './Sidebar';

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
    mockUseMediaQuery.mockReturnValue(false);
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

  it('renders only the Documents navigation choices', () => {
    renderWithTheme(<Sidebar />);

    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument();
    expect(getNavButton('Library')).toBeInTheDocument();
    expect(getNavButton('Add Literature')).toBeInTheDocument();
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
    expect(screen.queryByText('Settings')).not.toBeInTheDocument();
    expect(screen.queryByText('Embeddings')).not.toBeInTheDocument();
  }, 15000);

  it('navigates to documents page', () => {
    renderWithTheme(<Sidebar />);

    fireEvent.click(getNavButton('Library'));

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents');
  });

  it('navigates to add literature page', () => {
    renderWithTheme(<Sidebar />);

    fireEvent.click(getNavButton('Add Literature'));

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/add-literature');
  });

  it('highlights nested document routes as Library', () => {
    mockLocation.pathname = '/weaviate/documents/123';
    renderWithTheme(<Sidebar />);

    expect(getNavButton('Library')).toHaveClass('Mui-selected');
  });

  it('highlights add literature route', () => {
    mockLocation.pathname = '/weaviate/add-literature';
    renderWithTheme(<Sidebar />);

    expect(getNavButton('Add Literature')).toHaveClass('Mui-selected');
  });

  it('highlights add literature for the temporary import mock alias', () => {
    mockLocation.pathname = '/weaviate/documents/import-mock';
    renderWithTheme(<Sidebar />);

    expect(getNavButton('Add Literature')).toHaveClass('Mui-selected');
    expect(getNavButton('Library')).not.toHaveClass('Mui-selected');
  });

  it('collapses and expands the sidebar with labeled controls', async () => {
    renderWithTheme(<Sidebar />);

    fireEvent.click(screen.getByRole('button', { name: 'Collapse Documents navigation' }));

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Documents' })).not.toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: 'Library' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add Literature' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Expand Documents navigation' }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument();
    });
  });

  it('renders as temporary drawer on mobile', () => {
    mockUseMediaQuery.mockReturnValue(true);

    renderWithTheme(<Sidebar variant="permanent" />);

    const drawer = document.querySelector('.MuiDrawer-root');
    expect(drawer).toHaveClass('MuiDrawer-modal');
  });

  it('calls onToggle after navigation on mobile', () => {
    mockUseMediaQuery.mockReturnValue(true);
    const onToggle = vi.fn();

    renderWithTheme(<Sidebar open={true} onToggle={onToggle} />);

    fireEvent.click(getNavButton('Library'));

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents');
    expect(onToggle).toHaveBeenCalled();
  });

  it('does not call onToggle on desktop', () => {
    mockUseMediaQuery.mockReturnValue(false);
    const onToggle = vi.fn();

    renderWithTheme(<Sidebar open={true} onToggle={onToggle} />);

    fireEvent.click(getNavButton('Library'));

    expect(mockNavigate).toHaveBeenCalledWith('/weaviate/documents');
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('respects open prop', () => {
    const { rerender } = renderWithTheme(<Sidebar open={false} variant="temporary" />);

    expect(screen.queryByRole('heading', { name: 'Documents' })).not.toBeInTheDocument();

    rerender(
      <ThemeProvider theme={createTheme()}>
        <Sidebar open={true} variant="temporary" />
      </ThemeProvider>
    );

    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument();
  });

  it('uses custom width', () => {
    renderWithTheme(<Sidebar width={300} />);

    const drawer = document.querySelector('.MuiDrawer-paper');
    expect(drawer).toHaveStyle({ width: '300px' });
  });

  it('renders icons for the remaining navigation items', () => {
    renderWithTheme(<Sidebar />);

    expect(screen.getByTestId('DescriptionIcon')).toBeInTheDocument();
    expect(screen.getByTestId('PostAddIcon')).toBeInTheDocument();
    expect(screen.queryByTestId('DashboardIcon')).not.toBeInTheDocument();
    expect(screen.queryByTestId('SettingsIcon')).not.toBeInTheDocument();
  });
});

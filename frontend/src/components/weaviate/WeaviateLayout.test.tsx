import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';
import WeaviateLayout from './WeaviateLayout';

const mockUseMediaQuery = vi.fn();

vi.mock('@mui/material', async () => {
  const actual = await vi.importActual<typeof import('@mui/material')>('@mui/material');
  return {
    ...actual,
    useMediaQuery: () => mockUseMediaQuery(),
  };
});

describe('WeaviateLayout', () => {
  beforeEach(() => {
    mockUseMediaQuery.mockReturnValue(false);
  });

  it('gives routed content a bounded flex frame for nested scroll surfaces', () => {
    render(<WeaviateLayout />);

    const main = document.querySelector('main');
    const outletFrame = screen.getByTestId('weaviate-outlet-frame');

    expect(main).toHaveStyle({
      display: 'flex',
      height: '100%',
      marginLeft: '0px',
      overflow: 'hidden',
    });
    expect(outletFrame).toHaveStyle({
      display: 'flex',
      flexDirection: 'column',
      maxWidth: 'none',
      overflow: 'hidden',
    });
  });

  it('does not reserve drawer margin on mobile', () => {
    mockUseMediaQuery.mockReturnValue(true);

    render(<WeaviateLayout />);

    const main = document.querySelector('main');
    expect(main).not.toBeNull();

    expect(window.getComputedStyle(main!).marginLeft).toBe('0px');
  });

  it('provides a mobile control to reopen the documents navigation', () => {
    mockUseMediaQuery.mockReturnValue(true);

    render(<WeaviateLayout />);

    expect(screen.getByRole('button', { name: 'Open Documents navigation' })).toBeInTheDocument();
  });
});

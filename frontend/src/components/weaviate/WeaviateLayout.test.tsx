import { describe, expect, it } from 'vitest';
import { render, screen } from '../../test/test-utils';
import WeaviateLayout from './WeaviateLayout';

describe('WeaviateLayout', () => {
  it('gives routed content a bounded flex frame for nested scroll surfaces', () => {
    render(<WeaviateLayout />);

    const main = document.querySelector('main');
    const outletFrame = screen.getByTestId('weaviate-outlet-frame');

    expect(main).toHaveStyle({
      display: 'flex',
      height: '100%',
      overflow: 'hidden',
    });
    expect(outletFrame).toHaveStyle({
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    });
  });
});

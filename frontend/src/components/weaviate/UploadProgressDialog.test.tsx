import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import UploadProgressDialog from './UploadProgressDialog';

const defaultProps = {
  fileName: 'article.pdf',
  message: 'Generating embeddings',
  open: true,
  progress: 60,
  stage: 'embedding',
};

describe('UploadProgressDialog', () => {
  it('blocks Escape during foreground processing when background close is disabled', () => {
    const onClose = vi.fn();
    render(
      <UploadProgressDialog
        {...defaultProps}
        allowBackgroundClose={false}
        onClose={onClose}
      />,
    );

    fireEvent.keyDown(screen.getAllByRole('presentation')[0], { key: 'Escape' });

    expect(onClose).not.toHaveBeenCalled();
  });

  it('allows Escape after processing completes', () => {
    const onClose = vi.fn();
    render(
      <UploadProgressDialog
        {...defaultProps}
        allowBackgroundClose={false}
        onClose={onClose}
        stage="completed"
      />,
    );

    fireEvent.keyDown(screen.getAllByRole('presentation')[0], { key: 'Escape' });

    expect(onClose).toHaveBeenCalledOnce();
  });
});

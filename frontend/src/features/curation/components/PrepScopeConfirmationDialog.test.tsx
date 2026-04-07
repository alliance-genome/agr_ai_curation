import { describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { render, screen } from '@/test/test-utils'

import PrepScopeConfirmationDialog from './PrepScopeConfirmationDialog'

describe('PrepScopeConfirmationDialog', () => {
  it('shows adapter scope without special-casing reference adapters', () => {
    render(
      <PrepScopeConfirmationDialog
        open={true}
        preview={{
          ready: true,
          summary_text: 'You discussed 4 candidate annotations in reference adapter. Prepare all for curation review?',
          candidate_count: 4,
          extraction_result_count: 1,
          conversation_message_count: 2,
          adapter_keys: ['reference_adapter'],
          submit_adapter_keys: ['reference_adapter'],
          requires_adapter_selection: false,
          blocking_reasons: [],
        }}
        selectedAdapterKey="reference_adapter"
        loading={false}
        submitting={false}
        error={null}
        onClose={vi.fn()}
        onSelectedAdapterKeyChange={vi.fn()}
        onConfirm={vi.fn()}
      />
    )

    expect(screen.getByText('Adapters')).toBeInTheDocument()
    expect(screen.getByText('Reference Adapter')).toBeInTheDocument()
  })

  it('humanizes visible scope values', () => {
    render(
      <PrepScopeConfirmationDialog
        open={true}
        preview={{
          ready: true,
          summary_text: 'Prepare all for curation review?',
          candidate_count: 2,
          extraction_result_count: 1,
          conversation_message_count: 2,
          adapter_keys: ['custom_adapter'],
          submit_adapter_keys: ['custom_adapter'],
          requires_adapter_selection: false,
          blocking_reasons: [],
        }}
        selectedAdapterKey="custom_adapter"
        loading={false}
        submitting={false}
        error={null}
        onClose={vi.fn()}
        onSelectedAdapterKeyChange={vi.fn()}
        onConfirm={vi.fn()}
      />
    )

    expect(screen.getByText('Custom Adapter')).toBeInTheDocument()
  })

  it('requires an explicit adapter choice before multi-adapter prep can start', async () => {
    const user = userEvent.setup()
    const onSelectedAdapterKeyChange = vi.fn()

    render(
      <PrepScopeConfirmationDialog
        open={true}
        preview={{
          ready: false,
          summary_text: 'This chat includes findings for multiple adapters. Narrow the extraction scope to one adapter before preparing for curation review.',
          candidate_count: 4,
          extraction_result_count: 2,
          conversation_message_count: 6,
          adapter_keys: ['gene', 'disease'],
          submit_adapter_keys: [],
          requires_adapter_selection: true,
          blocking_reasons: [
            'This chat includes findings for multiple adapters. Narrow the extraction scope to one adapter before preparing for curation review.',
          ],
        }}
        selectedAdapterKey={null}
        loading={false}
        submitting={false}
        error={null}
        onClose={vi.fn()}
        onSelectedAdapterKeyChange={onSelectedAdapterKeyChange}
        onConfirm={vi.fn()}
      />
    )

    expect(screen.getByText('Choose one adapter to prepare')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start prep/i })).toBeDisabled()

    await user.click(screen.getByRole('radio', { name: 'Gene' }))

    expect(onSelectedAdapterKeyChange).toHaveBeenCalledWith('gene')
  })
})

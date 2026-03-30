import { describe, expect, it, vi } from 'vitest'
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
          blocking_reasons: [],
        }}
        loading={false}
        submitting={false}
        error={null}
        onClose={vi.fn()}
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
          blocking_reasons: [],
        }}
        loading={false}
        submitting={false}
        error={null}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />
    )

    expect(screen.getByText('Custom Adapter')).toBeInTheDocument()
  })
})

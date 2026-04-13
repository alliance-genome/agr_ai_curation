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
          unscoped_candidate_count: 0,
          preparable_candidate_count: 4,
          extraction_result_count: 1,
          conversation_message_count: 2,
          adapter_keys: ['reference_adapter'],
          discussed_adapter_keys: ['reference_adapter'],
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
          unscoped_candidate_count: 0,
          preparable_candidate_count: 2,
          extraction_result_count: 1,
          conversation_message_count: 2,
          adapter_keys: ['custom_adapter'],
          discussed_adapter_keys: ['custom_adapter'],
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

  it('shows ready vs discussed scope when prep can only prepare a filtered subset', () => {
    render(
      <PrepScopeConfirmationDialog
        open={true}
        preview={{
          ready: true,
          summary_text:
            'You discussed 5 candidate annotations across gene and allele adapters. 2 evidence-verified candidate annotations in gene adapter are ready to prepare for curation review.',
          candidate_count: 5,
          unscoped_candidate_count: 0,
          preparable_candidate_count: 2,
          extraction_result_count: 2,
          conversation_message_count: 2,
          adapter_keys: ['gene'],
          discussed_adapter_keys: ['gene', 'allele'],
          blocking_reasons: [],
        }}
        loading={false}
        submitting={false}
        error={null}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />
    )

    expect(screen.getByText('Ready candidates')).toBeInTheDocument()
    expect(screen.getByText('Discussed')).toBeInTheDocument()
    expect(screen.getByText('Ready adapters')).toBeInTheDocument()
    expect(screen.getByText('Discussed adapters')).toBeInTheDocument()
    expect(screen.getByText('Gene')).toBeInTheDocument()
    expect(screen.getByText('Gene, Allele')).toBeInTheDocument()
  })

  it('prefers the visible chat message count when provided', () => {
    render(
      <PrepScopeConfirmationDialog
        open={true}
        preview={{
          ready: true,
          summary_text: 'Prepare all for curation review?',
          candidate_count: 2,
          unscoped_candidate_count: 0,
          preparable_candidate_count: 2,
          extraction_result_count: 1,
          conversation_message_count: 2,
          adapter_keys: ['gene'],
          discussed_adapter_keys: ['gene'],
          blocking_reasons: [],
        }}
        visibleConversationMessageCount={5}
        loading={false}
        submitting={false}
        error={null}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />
    )

    expect(screen.getByText('Messages')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
  })
})

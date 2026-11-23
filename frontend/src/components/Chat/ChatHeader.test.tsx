import { describe, expect, it, vi } from 'vitest'

import { render, screen } from '@/test/test-utils'

import ChatHeader from './ChatHeader'

function renderHeader(activeDocument: { id: string; filename?: string | null } | null) {
  return render(
    <ChatHeader
      activeDocument={activeDocument}
      conversationStatus={null}
      normalizedSessionId={null}
      sessionIdCopied={false}
      isResetting={false}
      isUnloadingPDF={false}
      prepButtonDisabled={false}
      prepButtonLabel="Prepare for Curation"
      onCopySessionId={vi.fn()}
      onResetConversation={vi.fn()}
      onUnloadPDF={vi.fn()}
      onOpenPrepDialog={vi.fn()}
    />,
  )
}

// Wording approved by Chris on 23 Nov 2026 (a paper may be a text file, so not "PDF").
describe('ChatHeader wording', () => {
  it('names the active paper and offers to unload it', () => {
    renderHeader({ id: 'doc-1', filename: 'made-up-paper.md' })

    expect(screen.getByText('Active paper: made-up-paper.md')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Unload paper/ })).toHaveAttribute('title', 'Unload the active paper')
  })

  it('says no paper is loaded', () => {
    renderHeader(null)

    expect(screen.getByText('No paper loaded')).toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationDocumentRef } from '@/features/curation/types'
import DocumentReferenceControl from './DocumentReferenceControl'

const mocks = vi.hoisted(() => {
  class CurationWorkspaceRequestError extends Error {
    constructor(public status: number, message: string) {
      super(message)
    }
  }
  return {
    CurationWorkspaceRequestError,
    findDocumentReference: vi.fn(),
    linkDocumentReference: vi.fn(),
    unlinkDocumentReference: vi.fn(),
  }
})

vi.mock('@/features/curation/services/curationWorkspaceService', () => mocks)

const presentation = {
  displayLabel: 'Fixture Source',
  referenceLabelPriority: null,
  identifierExamples: ['PMID:123'],
}

function document(overrides: Partial<CurationDocumentRef> = {}): CurationDocumentRef {
  return { document_id: 'doc-1', title: 'My uploaded paper', filename: 'upload.pdf', ...overrides }
}

function renderControl(
  overrides: Partial<CurationDocumentRef> = {},
  importEnabled = true,
  extraProps: { onRetrySource?: () => void; sourceCheckFailed?: boolean } = {},
) {
  const onChanged = vi.fn().mockResolvedValue(undefined)
  render(
    <DocumentReferenceControl
      document={document(overrides)}
      importEnabled={importEnabled}
      onChanged={onChanged}
      presentation={importEnabled ? presentation : null}
      sessionId="session-1"
      {...extraProps}
    />,
  )
  return { onChanged }
}

async function findPaper(identifier = 'PMID:1') {
  fireEvent.click(screen.getByRole('button', { name: 'Link to Fixture Source' }))
  fireEvent.change(await screen.findByLabelText('Identifier'), { target: { value: identifier } })
  fireEvent.click(screen.getByRole('button', { name: 'Find' }))
}

describe('DocumentReferenceControl', () => {
  beforeEach(() => {
    mocks.findDocumentReference.mockReset()
    mocks.linkDocumentReference.mockReset()
    mocks.unlinkDocumentReference.mockReset()
  })

  it('offers linking when a document has no reference', () => {
    renderControl()

    expect(screen.getByText('Not linked to Fixture Source')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Link to Fixture Source' })).toBeEnabled()
  })

  it('shows nothing when import is off and there is no reference', () => {
    renderControl({}, false)

    expect(screen.queryByText(/Not linked/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows an imported reference with no actions', () => {
    renderControl({ reference_curie: 'SOURCE:1', reference_source: 'imported' })

    expect(screen.getByText('Fixture Source: SOURCE:1')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps showing a curator link with no actions when import is off', () => {
    renderControl({ reference_curie: 'SOURCE:2', reference_source: 'curator_link' }, false)

    expect(screen.getByText('Reference: SOURCE:2')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows the found paper beside this document and saves only after confirming', async () => {
    mocks.findDocumentReference.mockResolvedValue({
      status: 'found',
      message: 'Found.',
      reference_curie: 'SOURCE:9',
      reference_title: 'The found paper',
    })
    mocks.linkDocumentReference.mockResolvedValue({
      status: 'linked',
      message: 'Linked.',
      reference_curie: 'SOURCE:9',
      reference_title: 'The found paper',
    })
    const { onChanged } = renderControl()

    await findPaper()

    expect(await screen.findByText('The found paper')).toBeInTheDocument()
    expect(screen.getByText('SOURCE:9')).toBeInTheDocument()
    expect(screen.getByText('My uploaded paper')).toBeInTheDocument()
    expect(screen.getByText('upload.pdf')).toBeInTheDocument()
    expect(mocks.findDocumentReference).toHaveBeenCalledWith('session-1', 'PMID:1')
    expect(mocks.linkDocumentReference).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Link this paper' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(mocks.linkDocumentReference).toHaveBeenCalledWith('session-1', 'PMID:1')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('shows the server message and offers no confirmation when the paper is not found', async () => {
    mocks.findDocumentReference.mockResolvedValue({
      status: 'not_found',
      message: 'We could not find this paper in the literature source.',
    })
    const { onChanged } = renderControl()

    await findPaper()

    expect(await screen.findByText('We could not find this paper in the literature source.'))
      .toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Link this paper' })).not.toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('drops the found paper when the identifier changes', async () => {
    mocks.findDocumentReference.mockResolvedValue({
      status: 'found',
      message: 'Found.',
      reference_curie: 'SOURCE:9',
      reference_title: 'The found paper',
    })
    renderControl()

    await findPaper()
    await screen.findByText('The found paper')
    fireEvent.change(screen.getByLabelText('Identifier'), { target: { value: 'PMID:2' } })

    expect(screen.queryByText('The found paper')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Link this paper' })).not.toBeInTheDocument()
  })

  it('stays open and saves nothing when linking fails after finding', async () => {
    mocks.findDocumentReference.mockResolvedValue({
      status: 'found',
      message: 'Found.',
      reference_curie: 'SOURCE:9',
      reference_title: 'The found paper',
    })
    mocks.linkDocumentReference.mockResolvedValue({
      status: 'source_unavailable',
      message: 'The literature source is unavailable.',
    })
    const { onChanged } = renderControl()

    await findPaper()
    await screen.findByText('The found paper')
    fireEvent.click(screen.getByRole('button', { name: 'Link this paper' }))

    expect(await screen.findByText('The literature source is unavailable.')).toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('closes without saving on cancel', async () => {
    const { onChanged } = renderControl()

    fireEvent.click(screen.getByRole('button', { name: 'Link to Fixture Source' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mocks.findDocumentReference).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('unlinks a curator link after confirming', async () => {
    mocks.unlinkDocumentReference.mockResolvedValue({ status: 'unlinked', message: 'Unlinked.' })
    const { onChanged } = renderControl({
      reference_curie: 'SOURCE:2',
      reference_source: 'curator_link',
    })

    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    expect(mocks.unlinkDocumentReference).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink this paper' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(mocks.unlinkDocumentReference).toHaveBeenCalledWith('session-1')
  })

  it('locks the identifier during Find and links the identifier that was found', async () => {
    let finishFind: (value: unknown) => void = () => {}
    mocks.findDocumentReference.mockReturnValue(new Promise((resolve) => { finishFind = resolve }))
    mocks.linkDocumentReference.mockResolvedValue({
      status: 'linked',
      message: 'Linked.',
      reference_curie: 'SOURCE:1',
    })
    renderControl()

    await findPaper('PMID:1')
    expect(screen.getByLabelText('Identifier')).toBeDisabled()
    finishFind({
      status: 'found',
      message: 'Found.',
      reference_curie: 'SOURCE:1',
      reference_title: 'Paper one',
    })
    await screen.findByText('Paper one')
    fireEvent.click(screen.getByRole('button', { name: 'Link this paper' }))

    await waitFor(() => expect(mocks.linkDocumentReference).toHaveBeenCalledWith('session-1', 'PMID:1'))
  })

  it('reports a saved link when only the screen refresh fails', async () => {
    mocks.findDocumentReference.mockResolvedValue({
      status: 'found',
      message: 'Found.',
      reference_curie: 'SOURCE:9',
      reference_title: 'The found paper',
    })
    mocks.linkDocumentReference.mockResolvedValue({
      status: 'linked',
      message: 'Linked.',
      reference_curie: 'SOURCE:9',
    })
    const { onChanged } = renderControl()
    onChanged.mockRejectedValue(new Error('Save your current edits first.'))

    await findPaper()
    await screen.findByText('The found paper')
    fireEvent.click(screen.getByRole('button', { name: 'Link this paper' }))

    expect(await screen.findByText('Saved, but the screen did not refresh. Reload this page.'))
      .toBeInTheDocument()
    expect(screen.queryByText('Save your current edits first.')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('reports a saved unlink when only the screen refresh fails', async () => {
    mocks.unlinkDocumentReference.mockResolvedValue({ status: 'unlinked', message: 'Unlinked.' })
    const { onChanged } = renderControl({
      reference_curie: 'SOURCE:2',
      reference_source: 'curator_link',
    })
    onChanged.mockRejectedValue(new Error('Refresh failed.'))

    fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Unlink this paper' }))

    expect(await screen.findByText('Saved, but the screen did not refresh. Reload this page.'))
      .toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('refreshes the header when the server says the screen is out of date', async () => {
    mocks.findDocumentReference.mockRejectedValue(
      new mocks.CurationWorkspaceRequestError(409, 'This paper is already linked.'),
    )
    const { onChanged } = renderControl()

    await findPaper()

    expect(await screen.findByText('This paper is already linked.')).toBeInTheDocument()
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
  })

  it('does not refresh the header on other request failures', async () => {
    mocks.findDocumentReference.mockRejectedValue(
      new mocks.CurationWorkspaceRequestError(500, 'Server error.'),
    )
    const { onChanged } = renderControl()

    await findPaper()

    expect(await screen.findByText('Server error.')).toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('says so and offers a retry when the source settings could not be read', () => {
    const onRetrySource = vi.fn()
    renderControl({}, false, { onRetrySource, sourceCheckFailed: true })

    expect(screen.getByText('Could not check the literature source.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetrySource).toHaveBeenCalledTimes(1)
  })

  it('keeps showing a reference when the source settings could not be read', () => {
    renderControl(
      { reference_curie: 'SOURCE:2', reference_source: 'curator_link' },
      false,
      { onRetrySource: vi.fn(), sourceCheckFailed: true },
    )

    expect(screen.getByText('Reference: SOURCE:2')).toBeInTheDocument()
    expect(screen.getByText('Could not check the literature source.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Unlink' })).not.toBeInTheDocument()
  })
})

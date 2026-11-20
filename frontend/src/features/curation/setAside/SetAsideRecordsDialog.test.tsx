import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { FlowRunSetAsideRecordsResponse } from '@/features/curation/types'
import SetAsideRecordsDialog, { type SetAsidePaper } from './SetAsideRecordsDialog'
import {
  SET_ASIDE_ALREADY_IN_FILE,
  SET_ASIDE_HELD_BY_CURATOR_NOTE,
  SET_ASIDE_INCLUDE_ADVICE,
  SET_ASIDE_UNPROFILED_NOTE,
} from './setAsideSummary'

const mockFetch = vi.fn()

function paper(overrides: Partial<SetAsidePaper> = {}): SetAsidePaper {
  return {
    documentId: 'doc-1',
    flowRunId: 'run-1',
    title: 'Plimsoll signalling in wing discs',
    count: 1,
    placement: 'left_out',
    ...overrides,
  }
}

function response(
  documentId: string,
  overrides: Partial<FlowRunSetAsideRecordsResponse['results'][number]> = {},
  label = 'plim antibody',
): FlowRunSetAsideRecordsResponse {
  return {
    flow_run_id: 'run-1',
    document_id: documentId,
    set_aside_count: 1,
    results: [{
      extraction_result_id: `result-${documentId}`,
      node_id: 'extract',
      step: 1,
      agent_name: 'Reagent extractor',
      review_fields_available: true,
      held_by_curator: false,
      records: [{
        extraction_result_id: `result-${documentId}`,
        label,
        review_status: 'set aside',
        review_reason: 'The supplier is named only in the acknowledgements.',
        fields: [
          { field_ref: 'object.pack.Reagent.name', label: 'Name', value: label },
          { field_ref: 'object.pack.Reagent.supplier', label: 'Supplier', value: null },
        ],
      }],
      ...overrides,
    }],
  }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

function renderDialog(papers: SetAsidePaper[], onClose = vi.fn()) {
  render(<SetAsideRecordsDialog open flowName="Reagent table" papers={papers} onClose={onClose} />)
  return onClose
}

describe('SetAsideRecordsDialog', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a loading state, then the record with its values, status and reason', async () => {
    let resolve: (value: Response) => void = () => {}
    mockFetch.mockReturnValue(new Promise<Response>((done) => { resolve = done }))

    renderDialog([paper()])

    const dialog = screen.getByRole('dialog', { name: '1 record set aside' })
    expect(within(dialog).getByText('Plimsoll signalling in wing discs · Flow: Reagent table')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Loading set-aside records')).toBeInTheDocument()
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/curation-workspace/flow-runs/run-1/set-aside-records?document_id=doc-1',
      expect.objectContaining({ credentials: 'include' }),
    )

    resolve(jsonResponse(response('doc-1')))

    const record = await within(dialog).findByRole('region', { name: 'Record 1' })
    expect(within(record).getByText('plim antibody', { selector: 'span' })).toBeInTheDocument()
    expect(within(record).getByText('Review status: set aside')).toBeInTheDocument()
    expect(within(record).getByText('Not in the file')).toBeInTheDocument()
    expect(within(record).getByText('Supplier')).toBeInTheDocument()
    expect(within(record).getByText('—')).toBeInTheDocument()
    expect(within(record).getByText('The supplier is named only in the acknowledgements.')).toBeInTheDocument()
    expect(within(dialog).getByText(SET_ASIDE_INCLUDE_ADVICE)).toBeInTheDocument()
    expect(within(dialog).getByText('Read only. Nothing here changes your results.')).toBeInTheDocument()
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('shows the load error and fetches again on request', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ detail: 'Flow run run-1 not found' }, 404))

    renderDialog([paper()])

    expect(await screen.findByText(/The set-aside records could not be loaded\. Flow run run-1 not found/))
      .toBeInTheDocument()

    mockFetch.mockResolvedValueOnce(jsonResponse(response('doc-1')))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('region', { name: 'Record 1' })).toBeInTheDocument()
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('replaces the advice with a plain note for general results that offer no review fields', async () => {
    mockFetch.mockResolvedValue(jsonResponse(response('doc-1', { review_fields_available: false })))

    renderDialog([paper()])

    expect(await screen.findByText(SET_ASIDE_UNPROFILED_NOTE)).toBeInTheDocument()
    expect(screen.queryByText(SET_ASIDE_INCLUDE_ADVICE)).not.toBeInTheDocument()
    expect(screen.getByText('Not in the file')).toBeInTheDocument()
  })

  it('says the records are already in the file when the output uses Review status', async () => {
    mockFetch.mockResolvedValue(jsonResponse(response('doc-1')))

    renderDialog([paper({ placement: 'included' })])

    expect(await screen.findByText('In the file')).toBeInTheDocument()
    expect(screen.getByText(SET_ASIDE_ALREADY_IN_FILE)).toBeInTheDocument()
    expect(screen.queryByText(SET_ASIDE_INCLUDE_ADVICE)).not.toBeInTheDocument()
  })

  it('combines several papers in one viewer with a folding section per paper', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('document_id=doc-1')) return Promise.resolve(jsonResponse(response('doc-1')))
      if (url.includes('document_id=doc-2')) {
        return Promise.resolve(jsonResponse(response('doc-2', {}, 'UAS-frzw-RNAi line')))
      }
      return Promise.reject(new Error(`Unexpected fetch: ${url}`))
    })

    renderDialog([
      paper(),
      paper({
        documentId: 'doc-2',
        title: 'Frizzlewort controls eye size',
        count: 2,
      }),
    ])

    const dialog = screen.getByRole('dialog', { name: '3 records set aside in 2 papers' })
    expect(within(dialog).getByText('Flow: Reagent table')).toBeInTheDocument()
    const first = within(dialog).getByRole('button', { name: /Plimsoll signalling in wing discs/ })
    const second = within(dialog).getByRole('button', { name: /Frizzlewort controls eye size/ })
    expect(first).toHaveAttribute('aria-expanded', 'true')
    expect(second).toHaveAttribute('aria-expanded', 'false')
    expect(within(second).getByText('2 set aside')).toBeInTheDocument()

    expect(await within(dialog).findByText('plim antibody', { selector: 'span' })).toBeVisible()
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))

    fireEvent.click(second)
    expect(second).toHaveAttribute('aria-expanded', 'true')
    expect(await within(dialog).findByText('UAS-frzw-RNAi line', { selector: 'span' })).toBeInTheDocument()
  })

  it('shows a result a curator is deciding, with a plain note in place of its records', async () => {
    mockFetch.mockResolvedValue(jsonResponse({
      ...response('doc-1', { held_by_curator: true, records: [] }),
      set_aside_count: 0,
    }))

    renderDialog([paper()])

    const dialog = screen.getByRole('dialog', { name: '1 record set aside' })
    expect(await within(dialog).findByText(SET_ASIDE_HELD_BY_CURATOR_NOTE)).toBeInTheDocument()
    expect(within(dialog).queryByRole('region', { name: 'Record 1' })).not.toBeInTheDocument()
    expect(within(dialog).queryByText(SET_ASIDE_INCLUDE_ADVICE)).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/No set-aside records are waiting/)).not.toBeInTheDocument()
  })

  it('lists the other results of a paper when a curator holds one of them', async () => {
    const listed = response('doc-1')
    mockFetch.mockResolvedValue(jsonResponse({
      ...listed,
      results: [
        { ...listed.results[0], extraction_result_id: 'held', agent_name: 'Stock extractor', held_by_curator: true, records: [] },
        listed.results[0],
      ],
    }))

    renderDialog([paper()])

    expect(await screen.findByText(SET_ASIDE_HELD_BY_CURATOR_NOTE)).toBeInTheDocument()
    expect(screen.getByText('Stock extractor')).toBeInTheDocument()
    expect(screen.getByText('Reagent extractor')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Record 1' })).toBeInTheDocument()
    expect(screen.getByText(SET_ASIDE_INCLUDE_ADVICE)).toBeInTheDocument()
  })

  it('closes from the close icon and the Close button', async () => {
    mockFetch.mockResolvedValue(jsonResponse(response('doc-1')))
    const onClose = renderDialog([paper()])

    fireEvent.click(screen.getByRole('button', { name: 'Close set-aside records' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(2)
    await screen.findByRole('region', { name: 'Record 1' })
  })
})

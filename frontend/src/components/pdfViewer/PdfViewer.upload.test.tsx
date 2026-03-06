import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { createEvent, fireEvent, render, screen, waitFor } from '../../test/test-utils'
import PdfViewer from './PdfViewer'

class MockEventSource {
  static instances: MockEventSource[] = []
  static autoPayload: Record<string, unknown> | null = {
    stage: 'completed',
    progress: 100,
    message: 'Processing completed successfully',
    final: true,
  }

  url: string
  withCredentials = false
  readyState = 0
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
    window.setTimeout(() => {
      if (MockEventSource.autoPayload && this.onmessage) {
        this.onmessage({ data: JSON.stringify(MockEventSource.autoPayload) } as MessageEvent)
      }
    }, 0)
  }

  close(): void {
    this.readyState = 2
  }

  addEventListener(): void {
    // no-op for tests
  }

  removeEventListener(): void {
    // no-op for tests
  }

  dispatchEvent(): boolean {
    return false
  }
}

const originalEventSource = globalThis.EventSource

const createFetchMock = () => {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input)

    if (url.includes('/api/weaviate/documents/upload')) {
      return new Response(JSON.stringify({ document_id: 'doc-1' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    if (url.includes('/api/chat/document/load')) {
      return new Response(
        JSON.stringify({
          active: true,
          document: {
            id: 'doc-1',
            filename: 'dropped.pdf',
          },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }
      )
    }

    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
}

describe('PdfViewer drag-and-drop upload', () => {
  beforeAll(() => {
    globalThis.EventSource = MockEventSource as unknown as typeof EventSource
  })

  afterAll(() => {
    globalThis.EventSource = originalEventSource
  })

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    MockEventSource.instances = []
    MockEventSource.autoPayload = {
      stage: 'completed',
      progress: 100,
      message: 'Processing completed successfully',
      final: true,
    }
  })

  it('toggles drop-zone affordance while dragging', () => {
    render(<PdfViewer />)

    const dropZone = screen.getByRole('region', { name: 'PDF drop zone' })
    expect(screen.getByText('Drag and drop a PDF here to upload')).toBeInTheDocument()
    expect(screen.getByText(/To upload one or many files, use the/i)).toBeInTheDocument()
    expect(screen.getByText(/tab upload controls\./i)).toBeInTheDocument()

    fireEvent.dragEnter(dropZone, { dataTransfer: { files: [] } })
    expect(screen.getByText('Drop PDF to upload and load for chat')).toBeInTheDocument()

    fireEvent.dragLeave(dropZone, { relatedTarget: document.body, dataTransfer: { files: [] } })
    expect(screen.getByText('Drag and drop a PDF here to upload')).toBeInTheDocument()
  })

  it('rejects non-PDF files with a user-facing error', async () => {
    const fetchSpy = createFetchMock()
    render(<PdfViewer />)

    const dropZone = screen.getByRole('region', { name: 'PDF drop zone' })
    const invalidFile = new File(['text'], 'notes.txt', { type: 'text/plain' })

    fireEvent.drop(dropZone, { dataTransfer: { files: [invalidFile] } })

    await waitFor(() => {
      expect(screen.getByText('Please select PDF files only')).toBeInTheDocument()
    })
    expect(fetchSpy).not.toHaveBeenCalled()
    fetchSpy.mockRestore()
  })

  it('uploads a dropped PDF file', async () => {
    const fetchSpy = createFetchMock()
    render(<PdfViewer />)

    const dropZone = screen.getByRole('region', { name: 'PDF drop zone' })
    const file = new File(['%PDF-1.4'], 'dropped.pdf', { type: 'application/pdf' })

    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } })

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([input]) => String(input).includes('/api/weaviate/documents/upload'))
      ).toBe(true)
    })

    fetchSpy.mockRestore()
  })

  it('loads document for chat and dispatches chat-document-changed on completion', async () => {
    const fetchSpy = createFetchMock()
    const eventSpy = vi.fn()
    window.addEventListener('chat-document-changed', eventSpy as EventListener)

    render(<PdfViewer />)

    const dropZone = screen.getByRole('region', { name: 'PDF drop zone' })
    const file = new File(['%PDF-1.4'], 'dropped.pdf', { type: 'application/pdf' })

    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } })

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([input, init]) =>
          String(input).includes('/api/chat/document/load') &&
          (init as RequestInit | undefined)?.method === 'POST'
        )
      ).toBe(true)
    })

    await waitFor(() => {
      expect(eventSpy).toHaveBeenCalled()
    })

    const dispatchedEvent = eventSpy.mock.calls[0][0] as CustomEvent
    expect(dispatchedEvent.detail).toMatchObject({
      active: true,
      document: {
        id: 'doc-1',
      },
    })

    window.removeEventListener('chat-document-changed', eventSpy as EventListener)
    fetchSpy.mockRestore()
  })

  it('prevents default browser drop behavior while an upload is already in progress', async () => {
    MockEventSource.autoPayload = null
    const fetchSpy = createFetchMock()
    render(<PdfViewer />)

    const dropZone = screen.getByRole('region', { name: 'PDF drop zone' })
    const firstFile = new File(['%PDF-1.4'], 'first.pdf', { type: 'application/pdf' })
    const secondFile = new File(['%PDF-1.4'], 'second.pdf', { type: 'application/pdf' })

    fireEvent.drop(dropZone, { dataTransfer: { files: [firstFile] } })

    await waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([input]) => String(input).includes('/api/weaviate/documents/upload'))
      ).toBe(true)
    })

    const secondDropEvent = createEvent.drop(dropZone, {
      cancelable: true,
      dataTransfer: { files: [secondFile] },
    })
    fireEvent(dropZone, secondDropEvent)

    expect(secondDropEvent.defaultPrevented).toBe(true)
    expect(screen.getByText('Upload in progress...')).toBeInTheDocument()

    expect(
      fetchSpy.mock.calls.filter(([input]) => String(input).includes('/api/weaviate/documents/upload'))
    ).toHaveLength(1)

    fetchSpy.mockRestore()
  })
})

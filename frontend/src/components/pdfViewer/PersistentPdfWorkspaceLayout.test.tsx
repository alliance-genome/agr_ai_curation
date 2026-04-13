import { useEffect, useRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import PersistentPdfWorkspaceLayout from './PersistentPdfWorkspaceLayout'

const viewerLifecycle = {
  mounts: 0,
  unmounts: 0,
  nextInstanceId: 1,
}

const originalMatchMedia = window.matchMedia

function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

vi.mock('./PdfViewer', () => ({
  default: function MockPdfViewer() {
    const instanceIdRef = useRef(`pdf-viewer-${viewerLifecycle.nextInstanceId++}`)

    useEffect(() => {
      viewerLifecycle.mounts += 1
      return () => {
        viewerLifecycle.unmounts += 1
      }
    }, [])

    return (
      <div data-instance-id={instanceIdRef.current} data-testid="pdf-viewer">
        PDF viewer
      </div>
    )
  },
}))

function HomeStub() {
  const navigate = useNavigate()

  return (
    <div>
      <div>Home route content</div>
      <button onClick={() => navigate('/curation/session-1')} type="button">
        Go to curation
      </button>
    </div>
  )
}

function CurationStub() {
  const navigate = useNavigate()

  return (
    <div>
      <div>Curation route content</div>
      <button onClick={() => navigate('/')} type="button">
        Back home
      </button>
    </div>
  )
}

function renderLayout(initialEntries: string[] = ['/']) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route element={<PersistentPdfWorkspaceLayout />}>
            <Route index element={<HomeStub />} />
            <Route path="curation/:sessionId" element={<CurationStub />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('PersistentPdfWorkspaceLayout', () => {
  beforeEach(() => {
    viewerLifecycle.mounts = 0
    viewerLifecycle.unmounts = 0
    viewerLifecycle.nextInstanceId = 1
    setMatchMedia(false)
  })

  afterEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: originalMatchMedia,
    })
  })

  it('keeps the same PdfViewer instance mounted when switching between home and curation routes', () => {
    renderLayout()

    const initialViewer = screen.getByTestId('pdf-viewer')
    const initialInstanceId = initialViewer.getAttribute('data-instance-id')

    expect(initialInstanceId).toBe('pdf-viewer-1')
    expect(screen.getByText('Home route content')).toBeInTheDocument()
    expect(viewerLifecycle.mounts).toBe(1)
    expect(viewerLifecycle.unmounts).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: 'Go to curation' }))

    const curationViewer = screen.getByTestId('pdf-viewer')
    expect(screen.getByText('Curation route content')).toBeInTheDocument()
    expect(curationViewer.getAttribute('data-instance-id')).toBe(initialInstanceId)
    expect(viewerLifecycle.mounts).toBe(1)
    expect(viewerLifecycle.unmounts).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: 'Back home' }))

    const returnedViewer = screen.getByTestId('pdf-viewer')
    expect(screen.getByText('Home route content')).toBeInTheDocument()
    expect(returnedViewer.getAttribute('data-instance-id')).toBe(initialInstanceId)
    expect(viewerLifecycle.mounts).toBe(1)
    expect(viewerLifecycle.unmounts).toBe(0)
  })

  it('keeps the shared viewer accessible and mounted across route switches in compact layout', () => {
    setMatchMedia(true)

    renderLayout()

    const initialViewer = screen.getByTestId('pdf-viewer')
    const initialInstanceId = initialViewer.getAttribute('data-instance-id')

    expect(screen.getByTestId('persistent-pdf-viewer-panel')).toBeInTheDocument()
    expect(screen.getByTestId('persistent-pdf-route-content')).toBeInTheDocument()
    expect(screen.queryByLabelText('Resize PDF and route content panels')).not.toBeInTheDocument()
    expect(screen.getByTestId('persistent-pdf-workspace-layout')).toHaveAttribute(
      'data-layout-kind',
      'home',
    )

    fireEvent.click(screen.getByRole('button', { name: 'Go to curation' }))

    expect(screen.getByText('Curation route content')).toBeInTheDocument()
    expect(screen.getByTestId('persistent-pdf-workspace-layout')).toHaveAttribute(
      'data-layout-kind',
      'curation',
    )
    expect(screen.getByTestId('pdf-viewer').getAttribute('data-instance-id')).toBe(initialInstanceId)
    expect(viewerLifecycle.mounts).toBe(1)
    expect(viewerLifecycle.unmounts).toBe(0)
  })
})

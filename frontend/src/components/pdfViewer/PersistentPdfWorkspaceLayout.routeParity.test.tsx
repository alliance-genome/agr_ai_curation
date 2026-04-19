import { useEffect, useMemo } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import PersistentPdfWorkspaceLayout from './PersistentPdfWorkspaceLayout'
import {
  HOME_PDF_VIEWER_OWNER,
  buildCurationPDFViewerOwner,
  dispatchPDFDocumentChanged,
} from './pdfEvents'

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { uid: 'user-1' },
  }),
}))

const originalMatchMedia = window.matchMedia
const chatStorageKeys = getChatLocalStorageKeys('user-1')

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

function HomeRouteStub() {
  const navigate = useNavigate()

  return (
    <div>
      <div>Home route content</div>
      <button
        onClick={() => {
          dispatchPDFDocumentChanged(
            'doc-home',
            '/fixtures/home.pdf',
            'home.pdf',
            12,
            { ownerToken: HOME_PDF_VIEWER_OWNER },
          )
        }}
        type="button"
      >
        Load home document
      </button>
      <button onClick={() => navigate('/curation/session-1')} type="button">
        Go to curation
      </button>
    </div>
  )
}

function CurationRouteStub() {
  const navigate = useNavigate()
  const curationOwnerToken = useMemo(
    () => buildCurationPDFViewerOwner('session-1'),
    [],
  )

  return (
    <div>
      <div>Curation route content</div>
      <button
        onClick={() => {
          dispatchPDFDocumentChanged(
            'doc-curation',
            '/fixtures/curation.pdf',
            'curation.pdf',
            8,
            { ownerToken: curationOwnerToken },
          )
        }}
        type="button"
      >
        Load curation document
      </button>
      <button
        onClick={() => {
          dispatchPDFDocumentChanged(
            'doc-home-stale',
            '/fixtures/home-stale.pdf',
            'home-stale.pdf',
            7,
            { ownerToken: HOME_PDF_VIEWER_OWNER },
          )
        }}
        type="button"
      >
        Dispatch stale home document
      </button>
      <button
        onClick={() => {
          window.dispatchEvent(new CustomEvent('chat-document-changed', {
            detail: {
              active: false,
              document: null,
              ownerToken: HOME_PDF_VIEWER_OWNER,
            },
          }))
        }}
        type="button"
      >
        Dispatch stale home clear
      </button>
      <button onClick={() => navigate('/')} type="button">
        Back home
      </button>
    </div>
  )
}

function HydratingCurationRouteStub() {
  const curationOwnerToken = useMemo(
    () => buildCurationPDFViewerOwner('session-1'),
    [],
  )

  useEffect(() => {
    dispatchPDFDocumentChanged(
      'doc-home',
      '/fixtures/home.pdf',
      'home.pdf',
      12,
      { ownerToken: curationOwnerToken },
    )
  }, [curationOwnerToken])

  return <div>Curation hydration route content</div>
}

function renderLayout(initialEntries: string[] = ['/']) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route element={<PersistentPdfWorkspaceLayout />}>
            <Route index element={<HomeRouteStub />} />
            <Route path="curation/:sessionId" element={<CurationRouteStub />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

function renderHydrationLayout(initialEntries: string[] = ['/']) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route element={<PersistentPdfWorkspaceLayout />}>
            <Route index element={<HomeRouteStub />} />
            <Route path="curation/:sessionId" element={<HydratingCurationRouteStub />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('PersistentPdfWorkspaceLayout route parity', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    Element.prototype.scrollIntoView = vi.fn()
    setMatchMedia(false)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 200 }),
    )
  })

  afterEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: originalMatchMedia,
    })
  })

  it('keeps the curation document active when stale home events arrive after a route transition', async () => {
    renderLayout()

    fireEvent.click(screen.getByRole('button', { name: 'Load home document' }))
    expect(await screen.findByText('home.pdf')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Go to curation' }))
    expect(await screen.findByText('Curation route content')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Load curation document' }))
    expect(await screen.findByText('curation.pdf')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Dispatch stale home document' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dispatch stale home clear' }))

    await waitFor(() => {
      expect(screen.getByText('curation.pdf')).toBeInTheDocument()
      expect(screen.queryByText('home-stale.pdf')).not.toBeInTheDocument()
    })
  })

  it('preserves the live viewer document and iframe when the route owner changes before a new document is selected', async () => {
    renderLayout()

    fireEvent.click(screen.getByRole('button', { name: 'Load home document' }))
    expect(await screen.findByText('home.pdf')).toBeInTheDocument()
    expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)
    expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('"documentId":"doc-home"')

    fireEvent.click(screen.getByRole('button', { name: 'Go to curation' }))
    expect(await screen.findByText('Curation route content')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('home.pdf')).toBeInTheDocument()
      expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)
      expect(localStorage.getItem(chatStorageKeys.pdfViewerSession)).toContain('"documentId":"doc-home"')
    })
  })

  it('does not reload the PDF when curation hydration re-emits the same document after a route transition', async () => {
    renderHydrationLayout()

    fireEvent.click(screen.getByRole('button', { name: 'Load home document' }))
    expect(await screen.findByText('home.pdf')).toBeInTheDocument()

    const initialIframe = screen.getByTitle('PDF Viewer')
    const initialSrc = initialIframe.getAttribute('src')

    fireEvent.click(screen.getByRole('button', { name: 'Go to curation' }))
    expect(await screen.findByText('Curation hydration route content')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('home.pdf')).toBeInTheDocument()
      expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)
      expect(screen.getByTitle('PDF Viewer')).toBe(initialIframe)
      expect(screen.getByTitle('PDF Viewer')).toHaveAttribute('src', initialSrc)
    })
  })

  it('returns control to the home route without remounting a second viewer host', async () => {
    renderLayout()

    fireEvent.click(screen.getByRole('button', { name: 'Load home document' }))
    expect(await screen.findByText('home.pdf')).toBeInTheDocument()

    const initialViewerPanel = screen.getByTestId('persistent-pdf-viewer-panel')
    expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Go to curation' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Load curation document' }))
    expect(await screen.findByText('curation.pdf')).toBeInTheDocument()
    expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Back home' }))
    expect(await screen.findByText('Home route content')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('curation.pdf')).toBeInTheDocument()
      expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)
      expect(screen.getByTestId('persistent-pdf-viewer-panel')).toBe(initialViewerPanel)
    })
  })

  it('keeps the shared viewer usable in compact layout during a Home to Curation transition', async () => {
    setMatchMedia(true)
    renderLayout()

    fireEvent.click(screen.getByRole('button', { name: 'Load home document' }))
    expect(await screen.findByText('home.pdf')).toBeInTheDocument()
    expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: 'Go to curation' }))
    expect(await screen.findByText('Curation route content')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('home.pdf')).toBeInTheDocument()
      expect(screen.getAllByTitle('PDF Viewer')).toHaveLength(1)
      expect(screen.getByTestId('persistent-pdf-workspace-layout')).toHaveAttribute(
        'data-layout-kind',
        'curation',
      )
    })
  })
})

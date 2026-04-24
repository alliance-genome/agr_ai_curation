import { render, waitFor } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, describe, expect, it } from 'vitest'

import { PDF_VIEWER_SETTINGS_STORAGE_KEY } from '@/components/pdfViewer/highlightSettings'
import { createAppTheme } from '@/theme'
import ViewerSettings from './ViewerSettings'

function renderSettings(mode: 'dark' | 'light' = 'dark') {
  return render(
    <ThemeProvider theme={createAppTheme(mode)}>
      <ViewerSettings />
    </ThemeProvider>,
  )
}

describe('ViewerSettings', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('dispatches theme-aware defaults using the PDF viewer settings event contract', async () => {
    const theme = createAppTheme('light')
    const events: CustomEvent[] = []
    const listener = (event: Event) => {
      events.push(event as CustomEvent)
    }
    window.addEventListener('highlight-settings-changed', listener)

    try {
      render(
        <ThemeProvider theme={theme}>
          <ViewerSettings />
        </ThemeProvider>,
      )

      await waitFor(() => expect(events).toHaveLength(1))
      expect(events[0].detail).toEqual({
        color: theme.palette.success.main,
        opacity: 0.35,
        clearOnNewQuery: true,
      })
    } finally {
      window.removeEventListener('highlight-settings-changed', listener)
    }
  })

  it('maps persisted highlight settings to the active viewer event fields', async () => {
    localStorage.setItem(
      PDF_VIEWER_SETTINGS_STORAGE_KEY,
      JSON.stringify({
        highlightColor: '#123456',
        highlightOpacity: 0.55,
        clearOnNewQuery: false,
      }),
    )
    const events: CustomEvent[] = []
    const listener = (event: Event) => {
      events.push(event as CustomEvent)
    }
    window.addEventListener('highlight-settings-changed', listener)

    try {
      renderSettings()

      await waitFor(() => expect(events).toHaveLength(1))
      expect(events[0].detail).toEqual({
        color: '#123456',
        opacity: 0.55,
        clearOnNewQuery: false,
      })
    } finally {
      window.removeEventListener('highlight-settings-changed', listener)
    }
  })
})

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { describe, expect, it } from 'vitest'

const frontendRoot = join(__dirname, '../../..')
const pdfJsCompatVersion = '20260506-map-polyfill'

const importFreshFile = async (path: string) => {
  const url = pathToFileURL(path)
  url.searchParams.set('testRun', `${Date.now()}-${Math.random()}`)
  return import(url.href)
}

describe('PDF.js compatibility assets', () => {
  it('polyfills Map.getOrInsertComputed for browsers without the new Map API', async () => {
    const original = Map.prototype.getOrInsertComputed
    delete Map.prototype.getOrInsertComputed

    try {
      await importFreshFile(join(frontendRoot, 'public/pdfjs/compat/map_get_or_insert_computed.mjs'))

      const map = new Map<string, string | undefined>([['cached-undefined', undefined]])
      let existingCallbackCalled = false

      const existing = map.getOrInsertComputed('cached-undefined', () => {
        existingCallbackCalled = true
        return 'replacement'
      })
      const inserted = map.getOrInsertComputed('missing', (key) => `${key}-value`)

      expect(existing).toBeUndefined()
      expect(existingCallbackCalled).toBe(false)
      expect(inserted).toBe('missing-value')
      expect(map.get('missing')).toBe('missing-value')
    } finally {
      if (original) {
        Map.prototype.getOrInsertComputed = original
      } else {
        delete Map.prototype.getOrInsertComputed
      }
    }
  })

  it('keeps the PDF.js worker wrapper compatible with fake-worker fallback', async () => {
    const workerModule = await importFreshFile(join(frontendRoot, 'public/pdfjs/build/pdf.worker.compat.mjs'))

    expect(typeof workerModule.WorkerMessageHandler).toBe('function')
  })

  it('uses cache-busted viewer entrypoints for the HTML shell, modules, and worker', async () => {
    const viewerHtml = await readFile(join(frontendRoot, 'public/pdfjs/web/viewer.html'), 'utf8')
    const viewerModule = await readFile(join(frontendRoot, 'public/pdfjs/web/viewer.mjs'), 'utf8')
    const viewerTypes = await readFile(join(frontendRoot, 'src/components/pdfViewer/pdfViewerTypes.ts'), 'utf8')

    expect(viewerTypes).toContain(`/pdfjs/web/viewer.html?v=${pdfJsCompatVersion}`)
    expect(viewerHtml).toContain(`./viewer.mjs?v=${pdfJsCompatVersion}`)
    expect(viewerModule).toContain(`../compat/map_get_or_insert_computed.mjs?v=${pdfJsCompatVersion}`)
    expect(viewerModule).toContain(`../build/pdf.mjs?v=${pdfJsCompatVersion}`)
    expect(viewerModule).toContain(`./pdf_viewer.mjs?v=${pdfJsCompatVersion}`)
    expect(viewerModule).toContain(`../build/pdf.worker.compat.mjs?v=${pdfJsCompatVersion}`)
  })
})

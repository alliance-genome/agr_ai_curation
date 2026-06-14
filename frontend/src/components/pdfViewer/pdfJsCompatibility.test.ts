import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { describe, expect, it } from 'vitest'

const frontendRoot = join(__dirname, '../../..')
const pdfJsCompatVersion = '20260614-tohex-polyfill'

const importFreshFile = async (path: string) => {
  const url = pathToFileURL(path)
  url.searchParams.set('testRun', `${Date.now()}-${Math.random()}`)
  return import(url.href)
}

describe('PDF.js compatibility assets', () => {
  it('polyfills Map.getOrInsertComputed for browsers without the new Map API', async () => {
    const proto = Map.prototype as unknown as {
      getOrInsertComputed?: <K, V>(key: K, callback: (key: K) => V) => V
    }
    const original = proto.getOrInsertComputed
    delete proto.getOrInsertComputed

    try {
      await importFreshFile(join(frontendRoot, 'public/pdfjs/compat/map_get_or_insert_computed.mjs'))

      const map = new Map<string, string | undefined>([['cached-undefined', undefined]])
      const polyfilledMap = map as unknown as {
        getOrInsertComputed: (
          key: string,
          callback: (key: string) => string | undefined,
        ) => string | undefined
      }
      let existingCallbackCalled = false

      const existing = polyfilledMap.getOrInsertComputed('cached-undefined', () => {
        existingCallbackCalled = true
        return 'replacement'
      })
      const inserted = polyfilledMap.getOrInsertComputed('missing', (key) => `${key}-value`)

      expect(existing).toBeUndefined()
      expect(existingCallbackCalled).toBe(false)
      expect(inserted).toBe('missing-value')
      expect(map.get('missing')).toBe('missing-value')
    } finally {
      if (original) {
        proto.getOrInsertComputed = original
      } else {
        delete proto.getOrInsertComputed
      }
    }
  })

  it('polyfills Uint8Array.toHex for browsers without the new typed-array API', async () => {
    const proto = Uint8Array.prototype as unknown as { toHex?: () => string }
    const original = proto.toHex
    delete proto.toHex

    try {
      await importFreshFile(join(frontendRoot, 'public/pdfjs/compat/uint8array_to_hex.mjs'))

      const bytes = new Uint8Array([0x00, 0x0f, 0xff, 0xab]) as unknown as { toHex(): string }
      const empty = new Uint8Array([]) as unknown as { toHex(): string }

      expect(bytes.toHex()).toBe('000fffab')
      expect(empty.toHex()).toBe('')
    } finally {
      if (original) {
        proto.toHex = original
      } else {
        delete proto.toHex
      }
    }
  })

  it('keeps the PDF.js worker wrapper compatible with fake-worker fallback', async () => {
    const workerModule = await importFreshFile(join(frontendRoot, 'public/pdfjs/build/pdf.worker.compat.mjs'))

    expect(typeof workerModule.WorkerMessageHandler).toBe('function')
  })

  it('loads both compat polyfills in the worker wrapper before the worker', async () => {
    const workerCompat = await readFile(
      join(frontendRoot, 'public/pdfjs/build/pdf.worker.compat.mjs'),
      'utf8',
    )

    expect(workerCompat).toContain(`../compat/map_get_or_insert_computed.mjs?v=${pdfJsCompatVersion}`)
    expect(workerCompat).toContain(`../compat/uint8array_to_hex.mjs?v=${pdfJsCompatVersion}`)
    expect(workerCompat).toContain(`./pdf.worker.mjs?v=${pdfJsCompatVersion}`)
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

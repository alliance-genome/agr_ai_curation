# PDF text localization spike

This spike documents the sentence-level text-localization prototype added for `ALL-94` / `KANBAN-1110`.

## Prototype scope

- Viewer integration lives in `frontend/src/components/pdfViewer/PdfViewer.tsx`.
- Rendered-text helpers live in `frontend/src/components/pdfViewer/textLocalization.ts`.
- Manual probing UI lives in `frontend/src/components/pdfViewer/PdfHighlightTester.tsx`.
- The prototype is intentionally separate from the production evidence-anchor UX and does not replace bbox-driven overlays.

## What the prototype does

1. Reads the rendered PDF.js `.textLayer` DOM inside the existing iframe viewer.
2. Normalizes snippet text by collapsing whitespace and removing zero-width glyphs that commonly appear in rendered text layers.
3. Builds a searchable string index across the currently rendered pages only.
4. Converts the selected text match into a DOM `Range`, calls `getClientRects()`, and maps those client rects back to page-relative coordinates for overlay rendering.
5. Reports probe metadata back to the tools panel:
   - rendered pages scanned,
   - total document pages,
   - search duration,
   - match count,
   - cross-page status,
   - whether rect extraction succeeded.

## Findings

### Accuracy

- Exact snippet search is feasible when the target sentence already exists in the rendered PDF.js text layer.
- Multi-span matches on the same page work reliably because the search index is built from text nodes rather than individual span boundaries.
- Cross-page matches also work when both pages have rendered text layers; the prototype inserts a synthetic page-boundary space so sentences can bridge page breaks naturally.
- Whitespace normalization matters. PDF.js frequently introduces line-break and spacing artifacts that would make naive string matching fail.
- Zero-width glyph cleanup matters. Soft hyphen and zero-width characters can appear in rendered text nodes and need normalization before matching.

### Performance

- Search cost is low for the rendered subset because the prototype only scans pages whose text layers exist in the iframe.
- The tool panel reports the measured duration for each probe so reviewers can compare short and long documents interactively.
- Re-running localization on `textlayerrendered` and zoom changes is inexpensive enough for a spike, but production evidence highlighting should avoid repeated whole-document scans if we later support large rendered ranges or many concurrent anchors.

### Cross-page behavior

- Cross-page localization is possible with DOM `Range` selection and `getClientRects()`.
- It is not guaranteed for pages that PDF.js has not rendered yet. Off-screen pages without text layers are invisible to this approach.
- The probe therefore reports partial coverage as `not-ready` instead of treating it as a hard `not-found`.

### Fallback constraints

- Rendered-text-only localization is insufficient as the sole production strategy because off-screen or not-yet-rendered pages cannot be searched.
- A successful string match can still fail to return visible rects if PDF.js has not fully painted that text layer.
- Production evidence UX still needs a degraded path, most likely stored bbox anchors or a pre-render text-content source, for:
  - off-screen pages,
  - incomplete text-layer render state,
  - PDFs whose rendered text diverges from canonical extracted text,
  - future exact-anchor cases where strict snippet fidelity matters more than viewer convenience.

## Recommended follow-up

- Use rendered-text localization as the preferred interactive path when the sentence is already visible in PDF.js.
- Preserve bbox fallback for off-screen, incomplete-render, or extraction-mismatch cases.
- Keep the viewer contract typed around exact anchor inputs in the follow-on implementation ticket rather than expanding this spike further.

# PDF Highlight Verification

Legacy note:
This guide is for the older `CHUNK_PROVENANCE` / `doc_items` bounding-box overlay path.
It is not the canonical design for evidence quote highlighting anymore.
If you are debugging evidence quote matching/highlighting, stop here and use the newer docs below instead.
The remainder of this file applies only to legacy chunk-overlay diagnostics.

Current evidence-highlighting source of truth:
- `docs/plans/2026-03-20-localization-policy.md`
- `docs/design/pdf-evidence-fuzzy-anchoring.md`

Those newer docs define the intended behavior:
- use the real PDF.js text layer as the highlightable surface
- treat stored page/section data as hints, not pixel coordinates
- degrade through quote -> section -> page -> document states instead of relying on bbox overlays

Use this checklist when debugging or validating PDF chunk highlighting regressions.

## Quick Verification Flow

1. Load the target PDF in the viewer and confirm the active document ID matches the chat/document context.
2. Trigger the chunk selection or question that should emit a `CHUNK_PROVENANCE` event.
3. Confirm the browser receives a `pdf-overlay-update` event for the expected `chunkId`.
4. Verify the overlay renders on the expected page and roughly matches the cited text region.
5. If no overlay appears, inspect the browser console for `[PDF OVERLAY DIAGNOSTICS]` warnings.

## What To Capture In Bug Reports

- Document ID and filename shown in the viewer.
- The `chunk_id` and `document_id` from the `CHUNK_PROVENANCE` event.
- At least one `doc_item` sample, including `page` or `page_no` plus `bbox.left`, `bbox.top`, `bbox.right`, and `bbox.bottom`.
- Any `[PDF OVERLAY DIAGNOSTICS]` warning payloads from the console.
- Whether the highlight failed completely, rendered on the wrong page, or rendered in the wrong location.
- A screenshot or screen recording when the overlay is visibly offset.

## Diagnostic Meanings

- `missing-page`: the viewer received a doc item without a usable `page`/`page_no`.
- `missing-bbox`: the viewer received a doc item without bounding box coordinates.
- `invalid-bbox`: one or more bbox coordinates were non-finite or collapsed to zero width/height, so the overlay was dropped.

## Useful Browser Checks

- In DevTools, inspect the `pdf-overlay-update` event detail to confirm the selected chunk matches the rendered overlay.
- Search the console for `[PDF OVERLAY DIAGNOSTICS]` to quickly find dropped items and sample payloads.
- If the event payload looks correct but the page is wrong, confirm the backend provenance page numbering matches the PDF viewer page numbering.

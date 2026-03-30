# Curation Workspace UI Redesign — Gene Entity Tagging

**Date:** 2026-03-30
**Status:** Draft
**Scope:** Curation workspace screen (not inventory screen)

## Goal

Redesign the AI curation workspace to align with the Alliance's existing curation interfaces — primarily the literature UI's entity tagging workflow (TopicEntityCreate + TopicEntityTable) and secondarily the AGR curation interface's table-centric patterns. Curators should be able to see everything they need to make a curation decision on a single screen without scrolling.

The first use case is **gene extraction**: confirming that genes mentioned in a paper exist in the Alliance database and tagging them appropriately.

## Design Principles

1. **Everything visible at once.** The curator should never scroll to find information needed for a curation decision. The PDF, entity table, and evidence quote must all be visible simultaneously.
2. **Follow the literature UI's vocabulary.** Column names, entity types, controlled vocabularies, and workflow concepts should match what curators already know from the literature UI.
3. **AI pre-populates, curator validates.** Unlike the literature UI where curators type everything manually, the AI fills the entity table. The curator's primary action is confirming or rejecting rows, with editing and manual addition as secondary actions.
4. **Reuse existing infrastructure.** The PDF viewer, evidence-to-PDF highlighting event system, and authentication all stay as-is.

## Reference Interfaces

| Interface | Tech Stack | Key Patterns Adopted |
|-----------|-----------|---------------------|
| Literature UI (`agr_literature_ui`) | React 18, Bootstrap 4, AG Grid | Column structure (topic, entity type, species, entity), entity validation workflow, typeahead autocomplete for topics/entities, curator validation model |
| AGR Curation (`agr_curation/src/main/cliapp`) | React 19, PrimeReact, PrimeFlex | Inline table editing, dialog-based field editors, DataTable column toggling, toast notifications |
| AI Curation (current) | React 18, MUI v5, dark theme | PDF viewer, evidence-to-PDF highlighting event system, workspace context/state management, authentication |

## Screen Layout

The curation workspace is a two-panel horizontal split:

```
+---------------------------+-------------------------------+
|                           |  Toolbar: "Entity Tags (N)"   |
|                           |  [Accept All Validated] [+Add] |
|                           +-------------------------------+
|                           |                               |
|      PDF Viewer           |     Entity Tag Table          |
|      (~45% width)         |     (~55% width, ~70% height) |
|                           |                               |
|  Shows the paper.         |  Editable rows. AI-populated. |
|  Selected sentence is     |  Click row to select.         |
|  highlighted in the text. |                               |
|                           +-------------------------------+
|                           |  Evidence Preview Pane        |
|                           |  (~30% height)                |
|                           |                               |
|                           |  Sentence quote, metadata,    |
|                           |  "Show in PDF" link           |
+---------------------------+-------------------------------+
```

- **Left panel (~45%):** PDF viewer. Unchanged from current implementation. When a row is selected in the entity table, the corresponding sentence quote is highlighted in the PDF using the existing `dispatchPDFViewerNavigateEvidence()` event system.
- **Right panel (~55%):** Split vertically:
  - **Top (~70%):** Editable entity tag table
  - **Bottom (~30%):** Evidence preview pane
- Panels are resizable via `react-resizable-panels` (already in the project).
- The existing workspace header stays above the layout.

## Entity Tag Table

### Columns

| Column | Width | Content | Editable | Notes |
|--------|-------|---------|----------|-------|
| Entity | ~80px | Gene/allele name (e.g. "daf-2") | Yes | Free text with autocomplete lookup against Alliance DB |
| Type | ~55px | gene, allele, species, strain, etc. | Yes | Controlled dropdown. Values from literature UI: ATP:0000005 (gene), ATP:0000006 (allele), ATP:0000123 (species), ATP:0000027 (strain), ATP:0000025 (genotype), ATP:0000026 (fish), ATP:0000013 (transgenic construct), ATP:0000110 (transgenic allele), ATP:0000285 (classical allele), ATP:0000093 (sequence targeting reagent) |
| Species | ~80px | Italicized species name | Yes | Controlled dropdown. Defaults to curator's MOD species (same behavior as literature UI) |
| Topic | ~75px | Expression, phenotype, etc. | Yes | Typeahead search against topic ontology (same as literature UI's AsyncTypeahead) |
| DB Status | ~85px | Validation badge | No | Auto-populated from backend validation. States: "validated" (green), "ambiguous" (yellow), "not found" (red) |
| Source | ~50px | "AI" or "Manual" | No | Set automatically based on row origin |
| Decision | flex | Accept/Reject buttons or status badge | Yes | Primary curator action |

### Row States

- **Pending:** Default white/dark background. Accept and Reject buttons visible in Decision column. These are the rows needing curator attention.
- **Accepted:** Subtle green background tint. Decision column shows "Accepted" badge. Edit icon remains visible.
- **Rejected:** Reduced opacity. Decision column shows "Rejected" badge. Edit icon remains visible. Row stays in the table (not removed).

### Row Selection

- Clicking a row selects it (blue left border accent).
- Selecting a row populates the evidence preview pane with that row's sentence quote.
- Selecting a row triggers `dispatchPDFViewerNavigateEvidence()` with `mode: 'select'` to highlight the sentence in the PDF.
- Only click-based selection. No hover highlighting behavior.

### Inline Editing

- Clicking the edit icon on a row switches it to edit mode.
- Text fields become inputs, controlled vocabulary fields become dropdowns.
- Topic field uses typeahead autocomplete (same pattern as literature UI).
- Entity field uses autocomplete against the Alliance database.
- A Save/Cancel button pair appears in the Decision column.
- Saving triggers re-validation (DB Status updates).
- Only one row can be in edit mode at a time.

### Manual Addition

- "Add Entity" button in the toolbar opens a new blank row at the bottom in edit mode.
- All fields are empty. Source column auto-sets to "Manual."
- Curator fills in the fields and saves.

### Batch Actions

- "Accept All Validated" button in the toolbar accepts all pending rows where DB Status = "validated."
- This is the fast path for papers where the AI extraction is clean.

## Evidence Preview Pane

A slim panel below the entity tag table. Displays evidence for the currently selected row.

### When a row is selected

- **Header:** "Evidence for **daf-2**" on the left. "Show in PDF" link on the right.
- **Quote block:** The sentence extracted from the paper, displayed in a card with a colored left border accent. The entity name is bolded within the quote.
- **Metadata row:** Page number, section name, source label (AI-extracted), resolved database ID (e.g. WBGene00000898).
- Clicking "Show in PDF" triggers `dispatchPDFViewerNavigateEvidence()` — the PDF scrolls to the sentence and highlights it.

### When no row is selected

- Shows: "Select a row to view evidence."

### When a manually-added row is selected

- Shows: "No AI evidence — manually added." With an option to link evidence by selecting text in the PDF (future enhancement).

## Evidence-to-PDF Integration

Reuses the existing event-based architecture entirely:

| Component | File | Role |
|-----------|------|------|
| Event dispatch | `pdfEvents.ts` | `dispatchPDFViewerNavigateEvidence(command)` |
| Command builder | `chatEvidenceNavigation.ts` | `buildChatEvidenceNavigationCommand()` — adapt or create a parallel `buildEntityTagNavigationCommand()` |
| PDF listener | `PdfViewer.tsx` | Already listens for `pdf-viewer-navigate-evidence` events |
| Evidence navigation | `useEvidenceNavigation.ts` | Manages selection state, builds commands |

The entity tag table will dispatch the same `pdf-viewer-navigate-evidence` custom window event that the chat evidence cards already use. No changes needed to `PdfViewer.tsx`.

## Theme

Keep the current MUI v5 dark theme (#121212 background, #2196f3 primary blue). The layout and interaction patterns align with the literature UI and AGR curation, but the visual theme stays as-is. A theme toggle can be added later as a separate effort.

## Components Replaced

The current curation workspace right panel contains:

| Current Component | Disposition |
|-------------------|-------------|
| CandidateQueue (middle sidebar) | **Removed.** Was half-empty. Entity rows in the table replace the concept of "candidates." |
| AnnotationEditor (grouped field sections) | **Replaced** by editable table cells. Fields that were vertical form sections become table columns. |
| EvidencePanel (quality scoring, anchor chips) | **Replaced** by the slim evidence preview pane. Quality scoring and ambiguity matching are no longer needed (evidence now requires sentence quotes). |
| CuratorDecisionToolbar (Accept/Reject/Reset) | **Replaced** by inline Accept/Reject per row in the Decision column. |
| FieldRow, ValidationBadge, EvidenceChipGroup | **Removed.** Superseded by table cell renderers and the DB Status column. |

Components that stay unchanged:

| Component | Notes |
|-----------|-------|
| PdfViewer | Unchanged. Already handles evidence highlighting. |
| WorkspaceHeader | Stays above the layout. May need minor updates for session metadata. |
| WorkspaceShell | Resizable panel container. Panel configuration changes but the shell stays. |
| CurationWorkspaceContext | State management. Will need updates to support the new table data model instead of candidates/fields. |
| Auth, routing, services layer | Unchanged. |

## Data Model Implications

The current workspace model uses `CurationCandidate` objects with `CurationDraftField` arrays. The new table model aligns more with the literature UI's `topic_entity_tag` structure:

```typescript
interface EntityTag {
  tag_id: string
  entity_name: string
  entity_type: string          // ATP code (e.g. "ATP:0000005" for gene)
  species: string              // NCBITaxon code
  topic: string                // Topic ontology term
  db_status: 'validated' | 'ambiguous' | 'not_found'
  db_entity_id: string | null  // Resolved Alliance DB ID (e.g. "WBGene00000898")
  source: 'ai' | 'manual'
  decision: 'pending' | 'accepted' | 'rejected'
  evidence: {
    sentence_text: string
    page_number: number | null
    section_title: string | null
    chunk_ids: string[]
  } | null
  notes: string | null
}
```

The backend adapter/prep pipeline will need to produce `EntityTag` objects instead of (or in addition to) `CurationCandidate` objects. This is a backend concern outside the scope of this frontend spec, but the `EntityTag` interface above defines the contract between backend and frontend. The frontend will consume this shape from the workspace API. During the transition, the frontend should support both the old `CurationCandidate` model (for non-gene adapters that haven't been migrated) and the new `EntityTag` model.

## Out of Scope

- Inventory screen redesign (separate effort)
- Theme toggle
- Hover-based PDF highlighting
- Multi-entity evidence (one sentence referencing multiple genes) — handle as separate rows with the same quote
- Literature UI feature parity beyond entity tagging (workflow tags, file management, etc.)
- Backend adapter/prep pipeline changes to produce EntityTag objects

# GO Annotation Extraction and Flow View Plan

Date: 2026-05-16

Status: planning document for a GO demo extension to AI Curation.

Repository baseline: pulled `origin/main` before drafting; local HEAD was
`7dc8defa`.

Review revision: updated after an independent code/source review to make the
route, workspace context, graph data contract, registration work, and validator
scope more explicit.

## RESUME

Current state as of 2026-05-17:

- The near-term goal is a demo branch, not a complete GO extraction pipeline.
- The lowest-work implementation is still a standalone top-level
  `/go-flow-demo/:sessionId?` page launched from the existing curation workspace
  header with a `Flow view` button.
- The demo should use static GO-shaped graph data with optional read-only
  workspace metadata. It should avoid backend endpoints, GO-CAM persistence,
  Noctua/Barista write-back, runtime candidate hydration, PDF route integration,
  and accept/reject/delete actions.
- The selected paper for the first demo is Shivers et al. 2010,
  `PMID:20369020`, because it is open access, has existing WormBase/QuickGO
  annotations, and maps cleanly to a GO-CAM activity model.
- The graph should be GO-CAM activity-unit shaped, not a plain pathway drawing.
  The defensible extracted core is `PMK-1 MAP kinase activity` directly
  positively regulating `ATF-7 DNA-binding transcription factor activity`.
- Use existing GO-CAM model `gomodel:568b0f9600000284` as the semantic north
  star and visual reference, but do not claim the demo has automatically
  recreated the full model.
- If the graph needs more visual density, show the broader existing-GO-CAM
  scaffold `TIR-1 -> NSY-1 -> SEK-1 -> PMK-1 -> ATF-7` as context, with badges
  distinguishing `selected paper evidence` from `existing GO-CAM context`.

Next practical implementation step:

1. Add `GOFlowDemoPage.tsx` and a small `demoGraph.ts` containing the Shivers
   PMK-1/ATF-7 core plus optional context nodes.
2. Wire `/go-flow-demo/:sessionId?` in `frontend/src/App.tsx`.
3. Add a `Flow view` button beside `Preview submission` in
   `frontend/src/pages/CurationWorkspacePage.tsx`.
4. Render graph nodes with gene product, molecular function, process/component
   chips, evidence badges, and a details panel showing identifiers, predicate,
   PMID, source system, and paper evidence pointers.

## Purpose

The demo goal is to extend the existing paper-first AI Curation workflow so it
can draft Gene Ontology annotations from a publication, then present the result
in a Noctua/GO-CAM-style flow view inside AI Curation.

This should not try to replace Noctua in the first pass. The useful demo shape
is:

1. Curator opens a paper-backed AI Curation session.
2. A GO extraction flow proposes evidence-supported GO annotation candidates.
3. Existing validation and review machinery checks entities, GO terms,
   references, and duplicate/related GO annotations.
4. A `Flow view` button opens a graph page for reviewing the extracted activity
   model.
5. Clicking graph nodes and edges shows the exact paper evidence, candidate
   fields, validation findings, and review controls.

The flow view is a review projection over AI Curation candidates. Exporting or
writing to Noctua/Minerva/Barista can come later.

## Demo Branch Cut

If the goal is a demo in a few days, do not start with the hardened
`/curation/:sessionId/flow` architecture. That route is the right long-term
shape, but it pulls in workspace runtime hydration, PDF owner routing, curation
candidate navigation, and `PersistentPdfWorkspaceLayout` route matching.

The smallest credible demo should be a standalone top-level page:

```text
/go-flow-demo
/go-flow-demo/:sessionId
```

This page can still be launched from the curation workspace, but it should not
live inside the curation/PDF nested route for the first demo branch.

### What the demo proves

- AI Curation can present a paper-derived GO draft as a curator-reviewable
  activity graph.
- Nodes and edges can carry evidence quotes, reference details, confidence, and
  validation-style badges.
- The UI can mirror Noctua/GO-CAM mental models without needing Noctua write
  access, a GO domain pack, or a backend graph endpoint.

### What to intentionally fake or defer

- No production Noctua/Barista write-back.
- No GO-CAM persistence.
- No dedicated backend graph endpoint.
- No final GO domain pack or structured adapter registration.
- No PDF split-pane route integration.
- No real graph extraction if time is short; use static sample graph data with
  optional workspace context.
- No blocking GO policy validators beyond what is already available.

### Minimal frontend branch

Minimum files likely involved:

- `frontend/src/App.tsx`
- `frontend/src/pages/CurationWorkspacePage.tsx`
- `frontend/src/pages/GOFlowDemoPage.tsx`
- `frontend/src/features/goFlowDemo/demoGraph.ts`

Optional files if the branch grows beyond the simplest demo:

- `frontend/src/features/goFlowDemo/types.ts`
- `frontend/src/features/goFlowDemo/buildGoFlowDemoGraph.ts`
- `frontend/src/features/goFlowDemo/GOFlowDemoCanvas.tsx`
- `frontend/src/features/goFlowDemo/GOFlowDemoNode.tsx`
- `frontend/src/features/goFlowDemo/GOFlowDemoDetailsPanel.tsx`
- `frontend/src/features/goFlowDemo/GOFlowDemoLegend.tsx`

For a fast branch, it is fine to inline the small demo types, node rendering,
legend, and details panel inside `GOFlowDemoPage.tsx`. Split them only if the
page becomes uncomfortable to edit.

Route wiring:

- Add a lazy `GOFlowDemoPage`.
- Add a top-level route outside `PersistentPdfWorkspaceLayout`:
  `/go-flow-demo/:sessionId?`.
- Do not add a global AppBar link unless the demo needs one. A workspace button
  is enough and avoids introducing a new permanent navigation concept.

Workspace launch button:

- Add `Flow view` beside `Preview submission` in the current workspace header
  navigation slot.
- Link to `/go-flow-demo/${workspace.session.session_id}`.
- Pass the current workspace path in route state so the demo page can offer a
  clean `Back to workspace` button.

Demo page behavior:

- If `sessionId` is present, call the existing `fetchCurationWorkspace(sessionId)`
  service to show the real document title, PMID/DOI, candidate count, and any
  available evidence snippets.
- Try `fetchCurationWorkspaceEnvelopeReviewRows(workspace)` only if the branch
  wants to demonstrate joining real review-row metadata. This is optional.
- Always fall back to `demoGraph.ts` so the page works even when the selected
  session has no GO-shaped candidates.
- Label the graph as `Draft GO activity model` so the fallback/static nature is
  honest.
- Keep the workspace fetch decorative and read-only. The demo page should avoid
  `CurationWorkspaceRuntimeProvider`, candidate hydration, PDF dispatch,
  autosave, accept/reject/delete actions, and evidence jump-to-PDF behavior.

Graph content for the demo:

- 3-6 activity nodes.
- Primary node text: gene product or gene symbol.
- Secondary node text: molecular function GO label.
- Chips for biological process, cellular component, evidence code, and review
  state.
- 1-2 causal edges only where the sample paper text explicitly supports them.
- A compact legend for activation, inhibition, upstream, and input/output
  relation families.
- A details panel with paper quote, page/figure, reference ID, extracted fields,
  and validation-style badges.

Validation for the demo branch:

- Frontend type-check/build if time allows.
- A small unit test for `buildGoFlowDemoGraph` if an adapter is added.
- A smoke test that `/go-flow-demo` and `/go-flow-demo/:sessionId` render.
- Do not spend demo time on backend contract tests unless the branch adds a
  backend endpoint.

### Why this is the least work

This cut uses existing dependencies (`reactflow`, MUI, React Query, and the
curation workspace service) while avoiding the parts Franklin identified as
expensive: curation route matching, PDF owner dispatch, runtime hydration, GO
domain-pack registration, and backend graph projection. It creates the visual
and review experience that GO curators can react to, then the robust plan below
can harden the winning shape after the demo. In the demo branch, treat any
shared PDF/runtime extraction work below as post-demo hardening, not prerequisite
work.

## Candidate Papers for a Static Demo Graph

The original Jiang and Wu 2014 paper is a clean mechanistic example, but
QuickGO currently returns only one paper-backed annotation for
`PMID:25144461`. For a richer demo graph, the better choices are papers with
several curator-backed annotations and a clear causal story that can be drawn in
4-8 nodes.

Recommended first pick:

- Shivers et al. 2010,
  `Phosphorylation of the conserved transcription factor ATF-7 by PMK-1 p38
  MAPK regulates innate immunity in Caenorhabditis elegans`.
  `PMID:20369020`, `DOI:10.1371/journal.pgen.1000892`.
  Article:
  https://journals.plos.org/plosgenetics/article?id=10.1371/journal.pgen.1000892
  PDF:
  https://journals.plos.org/plosgenetics/article/file?id=10.1371/journal.pgen.1000892&type=printable
  QuickGO:
  https://www.ebi.ac.uk/QuickGO/services/annotation/search?reference=PMID:20369020&limit=100
  QuickGO count checked on 2026-05-17: 13 annotations. Demo graph:
  pathogen exposure or Gram-negative bacterial infection -> PMK-1 p38 MAPK
  activity -> ATF-7 regulation/phosphorylation -> immune gene transcription ->
  defense response. Keep the claim centered on PMK-1 and ATF-7; upstream
  pathogen sensing is pathway context unless the evidence panel says otherwise.

Strong alternate picks:

- Ishimaru et al. 2004,
  `PVR plays a critical role via JNK activation in thorax closure during
  Drosophila metamorphosis`. `PMID:15457211`,
  `DOI:10.1038/sj.emboj.7600417`.
  Article: https://pmc.ncbi.nlm.nih.gov/articles/PMC524349/
  PDF: https://pmc.ncbi.nlm.nih.gov/articles/PMC524349/pdf/7600417a.pdf
  QuickGO:
  https://www.ebi.ac.uk/QuickGO/services/annotation/search?reference=PMID:15457211&limit=100
  QuickGO count checked on 2026-05-17: 32 annotations. Demo graph:
  Pvr receptor tyrosine kinase activity -> Crk/Mbc/Ced-12 -> Rac1/Cdc42 ->
  Slpr -> Hep -> Bsk/JNK -> thorax closure. This is the best Drosophila-native
  candidate because the annotation coverage and graph shape are both strong.

- Laor et al. 2021,
  `Tripartite suppression of fission yeast TORC1 signaling by the GATOR1-Sea3
  complex, the TSC complex, and Gcn2 kinase`. `PMID:33534698`,
  `DOI:10.7554/eLife.60969`.
  Article: https://elifesciences.org/articles/60969
  PDF: https://pmc.ncbi.nlm.nih.gov/articles/PMC7857730/pdf/elife-60969.pdf
  QuickGO:
  https://www.ebi.ac.uk/QuickGO/services/annotation/search?reference=PMID:33534698&limit=100
  QuickGO count checked on 2026-05-17: 55 annotations. Demo graph:
  nitrogen or amino-acid starvation -> GATOR1-Sea3, TSC, and Gcn2 branches ->
  negative regulation of TORC1 signaling -> autophagy/growth response. This is
  the richest graph candidate, but it can sprawl unless the branch count is
  deliberately constrained.

- Klatt Shaw et al. 2018,
  `Intracellular calcium mobilization is required for Sonic Hedgehog signaling`.
  `PMID:29754802`, `DOI:10.1016/j.devcel.2018.04.013`.
  Article: https://pmc.ncbi.nlm.nih.gov/articles/PMC6007892/
  PDF: https://pmc.ncbi.nlm.nih.gov/articles/PMC6007892/pdf/nihms966503.pdf
  QuickGO:
  https://www.ebi.ac.uk/QuickGO/services/annotation/search?reference=PMID:29754802&limit=100
  QuickGO count checked on 2026-05-17: 100 annotations. Demo graph:
  RyR calcium release channel genes -> intracellular calcium mobilization ->
  Smoothened/Shh signaling -> developmental patterning outputs. This has high
  annotation density, but many annotations are developmental outputs; avoid
  drawing every phenotype as a direct molecular edge.

Good backups:

- Hirose and Horvitz 2013, `PMID:23851392`, Sp1/SPTF-3 coordination of
  caspase-dependent and caspase-independent apoptosis. QuickGO count checked on
  2026-05-17: 11 annotations. Good for a compact forked transcriptional graph.
- Inoue et al. 2005, `PMID:16166371`, C. elegans p38 MAPK regulation of SKN-1
  nuclear localization during oxidative stress. QuickGO count checked on
  2026-05-17: 14 annotations. Good kinase -> transcription factor localization
  flow.
- Alpar et al. 2018, `PMID:31088910`, Drosophila Toll-family ligands and
  receptors. QuickGO count checked on 2026-05-17: 37 annotations. Intuitive
  ligand/receptor story, but family-wide evidence can sprawl.

### GO-CAM-Aligned Mapping for the Shivers Demo

Use the existing GO-CAM model as the visual and semantic north star, not as a
thing we claim to have recreated automatically:

- Model: `gomodel:568b0f9600000284`
- Title: `Antibacterial innate immune response in the intestine via MAPK
  cascade (C. elegans)`
- Source TTL:
  https://raw.githubusercontent.com/geneontology/noctua-models/master/models/568b0f9600000284.ttl

The demo graph should be activity-centric. A node should represent a GO-CAM
activity unit, not just a gene or a biological process.

Core paper-backed activity nodes:

| Demo node | GO-CAM fields | Evidence posture |
| --- | --- | --- |
| `PMK-1 MAP kinase activity` | `enabled_by`: `WB:WBGene00004055` / `pmk-1`; `molecular_function`: `GO:0004707` / `MAP kinase activity`; `occurs_in`: `GO:0005829` / `cytosol`; `part_of`: `GO:0140367` / `antibacterial innate immune response`; `has_input`: `WB:WBGene00000223` / `atf-7` | `PMID:20369020`, direct assay evidence for PMK-1-dependent ATF-7 phosphorylation; show the specific figure/evidence snippet in the details panel. |
| `ATF-7 DNA-binding transcription factor activity` | `enabled_by`: `WB:WBGene00000223` / `atf-7`; `molecular_function`: `GO:0000981` / `DNA-binding transcription factor activity, RNA polymerase II-specific`; `occurs_in`: `GO:0005634` / `nucleus`; `part_of`: `GO:0140367` / `antibacterial innate immune response` | `PMID:20369020`, with evidence from ATF-7 nuclear localization, genetics, and pathogen-induced gene-expression assays. |

Core paper-backed causal edge:

| Source activity | Predicate | Target activity | Evidence posture |
| --- | --- | --- | --- |
| `PMK-1 MAP kinase activity` | `RO:0002629` / `directly positively regulates` | `ATF-7 DNA-binding transcription factor activity` | `PMID:20369020`, `ECO:0000314` in the existing GO-CAM evidence for this edge. In the UI, do not label the graph edge as `phosphorylates`; use `directly positively regulates` and put phosphorylation in the evidence detail. |

Useful paper-backed process badges for the ATF-7 node, from current QuickGO
annotations for `PMID:20369020`:

- `GO:0000122` / `negative regulation of transcription by RNA polymerase II`
- `GO:0045944` / `positive regulation of transcription by RNA polymerase II`
- `GO:0045089` / `positive regulation of innate immune response`
- `GO:0050829` / `defense response to Gram-negative bacterium`
- `GO:0140367` / `antibacterial innate immune response`

Optional context scaffold, if the graph needs to feel more pathway-like:

| Context node | GO-CAM fields | Demo caveat |
| --- | --- | --- |
| `TIR-1 signaling adaptor activity` | `enabled_by`: `WB:WBGene00006575` / `tir-1`; `molecular_function`: `GO:0035591` / `signaling adaptor activity`; `part_of`: `GO:0140367` | Existing GO-CAM and prior-pathway context. Do not imply this edge was newly extracted from Shivers 2010 alone. |
| `NSY-1 MAP kinase kinase kinase activity` | `enabled_by`: `WB:WBGene00003822` / `nsy-1`; `molecular_function`: `GO:0004709` | Existing GO-CAM and prior-pathway context. |
| `SEK-1 MAP kinase kinase activity` | `enabled_by`: `WB:WBGene00004758` / `sek-1`; `molecular_function`: `GO:0004708` | Existing GO-CAM and prior-pathway context. |
| `VHP-1 MAPK phosphatase activity` | `enabled_by`: `WB:WBGene00006923` / `vhp-1`; `molecular_function`: `GO:0017017` / `MAP kinase tyrosine/serine/threonine phosphatase activity`; edge to PMK-1 uses `RO:0002630` / `directly negatively regulates` | Good if we want to show inhibition styling, but it is not the main Shivers demo claim. |

Recommended demo posture:

- Default view: the two-node Shivers core plus process/context chips. This is
  the most defensible paper-extraction story.
- Toggle or faint background layer: the broader existing GO-CAM scaffold
  `TIR-1 -> NSY-1 -> SEK-1 -> PMK-1 -> ATF-7`, with badges marking which claims
  came from the selected paper and which came from existing GO-CAM context.
- Details panel: show identifiers, evidence code, PMID, source system
  (`WormBase`, `QuickGO`, or `existing GO-CAM`), and the paper snippet/figure
  pointer.
- Never make biological process terms such as `defense response to
  Gram-negative bacterium` the main flow nodes. In GO-CAM they are context for
  molecular activities, so render them as chips, swimlanes, grouping labels, or
  output/context cards.

## Why This Fits AI Curation

Standard GO curation is naturally paper based: a gene product is associated with
a GO term, an evidence code, and a supporting reference. GO-CAM goes further by
linking those annotations into activity-centric models. That means AI Curation
can start with the same loop it already does well:

- retrieve relevant paper text
- extract structured, evidence-backed candidate objects
- attach quote/page evidence
- validate normalized fields
- ask a curator to accept, reject, or edit

The visual graph should come after extraction. The agent does not need to "draw
a pathway"; it needs to extract molecular activity claims, cellular/process
context, and causal relationships when the paper explicitly supports them. The
frontend can then render those candidates as a flow-like review graph.

## External Reference Points

Sources inspected:

- GO-CAM overview:
  https://geneontology.org/docs/gocam-overview/
- GO Web Components repository:
  https://github.com/geneontology/web-components
- GO Web Components `go-gocam-viewer` docs:
  https://geneontology.github.io/web-components/docs/components/gocam-viewer
- GO Web Components `go-gocam-viewer` source:
  https://github.com/geneontology/web-components/blob/main/packages/web-components/src/components/gocam-viewer/gocam-viewer.tsx
- Noctua repository:
  https://github.com/geneontology/noctua
- Noctua Python client:
  https://github.com/geneontology/noctua-py
- GO-CAM LinkML schema:
  https://github.com/geneontology/gocam-py
- Public GO-CAM schema docs:
  https://geneontology.github.io/gocam-py/
- DisMech knowledge base:
  https://dismech.monarchinitiative.org/
- DisMech repository:
  https://github.com/monarch-initiative/dismech

### Noctua

Noctua is the Gene Ontology Consortium's collaborative graphical editor for
standard GO annotations and GO-CAM models. For our demo, it is mostly a product
reference: it tells us curators expect activity units, semantic relationships,
evidence, and model review, not just a flat table.

Direct Noctua integration should be treated as a later phase because it implies
authentication, Barista/Minerva write semantics, GO-CAM editing permissions, and
GO-specific curator training. The public Noctua repository is useful reference
material, but it should not be treated as a stable external integration contract
for this demo.

### GO Web Components

The most relevant UI reference is the official `go-gocam-viewer` web component.
It renders a GO-CAM as:

- a network diagram of activities and causal relationships
- a sidebar grouping activities by biological process
- node labels centered on the gene product carrying out the activity
- activity detail cards showing molecular function, gene product, cellular
  context, evidence, and references
- a relation legend for activation, inhibition, upstream, input/output, and
  related causal predicates

Implementation detail: the official component uses Cytoscape with a dagre
layout. AI Curation already has React Flow in Agent Studio, so we should mirror
the interaction model and visual vocabulary while using our existing React Flow
stack.

### GO-CAM Data Model

The GO-CAM schema is activity-centric. A model has a set of activities. Each
activity can include:

- `enabled_by`: the gene product, complex, or molecule that carries out the
  activity
- `molecular_function`: the GO molecular function for the activity
- `occurs_in`: cellular component context
- `part_of`: biological process context
- `causal_associations`: outgoing relationships to downstream activities
- evidence/provenance attached to associations

This is the right mental model for our flow view. A single activity node should
represent "gene product X enables molecular function Y in context Z", with
paper evidence attached to each claim.

### DisMech Pattern

Chris's DisMech suggestion is useful, but mostly as a process reference rather
than the UI we should demo. DisMech stores schema-backed literature claims,
renders browsable pages, and uses LinkML-style validation, ontology checks,
reference checks, compliance dashboards, and GitHub review loops.

For AI Curation, the transferable idea is the feedback loop:

- agent proposes structured, evidence-backed claims
- validators check schema, ontology terms, references, and coverage
- humans review aggregate QC and tune rubrics
- a GitHub or dashboard layer can expose the work outside the immediate agent
  loop

That is valuable for GO, but it should support the paper-to-GO review workflow
rather than replace it.

## Existing AI Curation Assets

### Frontend Flow Technology

AI Curation already uses React Flow in Agent Studio:

- `frontend/src/components/AgentStudio/FlowBuilder/FlowBuilder.tsx`
- `frontend/src/components/AgentStudio/FlowBuilder/FlowNode.tsx`
- `frontend/src/components/AgentStudio/FlowBuilder/types.ts`

The Agent Studio flow builder is agent-workflow specific, so we should not reuse
its types directly for GO biology. We should reuse the canvas interaction
pattern: fit view, minimap, controls, selectable nodes, styled edges, stable
layout, and node/detail panels.

### Curation Workspace

The existing workspace is the right host for the demo:

- `frontend/src/pages/CurationWorkspacePage.tsx`
- `frontend/src/features/curation/workspace/WorkspaceHeader.tsx`
- `frontend/src/features/curation/workspace/EnvelopeObjectReviewTable.tsx`
- `frontend/src/features/curation/contracts.ts`

The workspace already has:

- session-scoped paper context
- candidate selection
- evidence anchor projections
- validation summary projections
- domain-envelope review rows
- a header `navigationSlot` where a `Flow view` button can live

The first implementation should add the button to the curation workspace header
rather than the global AppBar. A workspace-scoped button preserves paper/session
context and avoids opening an empty global page with no session selected. A
global top-nav entry can come later once there is a GO flow inventory or landing
view.

Important implementation caveat: `CurationWorkspacePage` currently owns more
than page rendering. It fetches the workspace, initializes the runtime provider,
dispatches the PDF document into `PersistentPdfWorkspaceLayout`, and hydrates
candidate selection. A sibling `GOFlowViewPage` will not inherit all of that
automatically.

Before building the flow page, extract or share the workspace/PDF/session setup
so both the table page and flow page can use it. The flow page should avoid
candidate hydration behavior that navigates back to
`/curation/:sessionId/:candidateId` when no candidate is selected.

Relevant current files:

- `frontend/src/pages/CurationWorkspacePage.tsx`
- `frontend/src/features/curation/workspace/CurationWorkspaceRuntimeProvider.tsx`
- `frontend/src/features/curation/workspace/useSessionHydration.ts`
- `frontend/src/components/pdfViewer/PersistentPdfWorkspaceLayout.tsx`

### Backend Projection Surface

The backend already materializes domain-envelope review rows:

- `GET /api/curation-workspace/domain-envelopes/{envelope_id}/review-rows`
- implemented in `backend/src/api/curation_workspace.py`

The GO flow view can start with frontend-side projection from existing
workspace candidates and review rows, but the preferred medium-term design is a
dedicated backend graph projection endpoint:

```http
GET /api/curation-workspace/sessions/{session_id}/go-flow
```

That endpoint can return a graph optimized for review instead of forcing the UI
to rediscover biological semantics from generic review rows.

### Existing GO and Validator Agents

The repository already has useful building blocks:

- `packages/alliance/agents/gene_ontology`: QuickGO-backed GO term lookup
- `packages/alliance/agents/go_annotations`: GO API-backed gene annotation
  lookup
- `packages/alliance/agents/ontology_term`: generic ontology validator
- `packages/alliance/agents/reference`: reference validator
- `packages/alliance/agents/subject_entity`: gene/entity validator

The pull from `origin/main` added more validator/domain tooling that is useful
for the GO plan. The new GO work should mostly be a new extractor and domain
pack projection, not a rewrite of these lookup agents.

## Proposed Product Shape

### Workspace Navigation

Add a `Flow view` button beside `Preview submission` in the workspace header.
The route should be session scoped:

```text
/curation/:sessionId/flow
```

Optional later route for opening with a selected candidate:

```text
/curation/:sessionId/:candidateId/flow
```

The page should live inside `PersistentPdfWorkspaceLayout` so the PDF context
and existing curation session behavior remain available.

Because the current app already has `/curation/:sessionId/:candidateId`, the
flow route should be declared explicitly as a static child route. React Router
will prefer the static segment when both routes exist, but the route test should
cover that `/curation/{sessionId}/flow` opens the flow page instead of treating
`flow` as a candidate ID.

Also update `PersistentPdfWorkspaceLayout` itself. That layout currently performs
manual route matching for curation paths, so the implementation must reserve the
`flow` segment and ensure it is not treated as a candidate ID. If the later
`/curation/:sessionId/:candidateId/flow` route is added, it also needs an
explicit layout match.

### Flow View Layout

The page should be a dense review tool, not a landing page.

Primary regions:

- top workspace header, reusing the existing session header
- left or right activity list grouped by biological process
- central React Flow canvas
- detail panel for selected node or edge
- compact legend for relation glyphs/colors
- empty state for "no GO flow candidates yet"

Interaction:

- click an activity node to select the underlying candidate/activity
- click an edge to inspect the relation, supporting quote, and confidence
- click evidence to jump the PDF viewer to the quote/page when available
- use review actions from the existing candidate review model where practical
- show validation status on nodes and edges

The flow view should be read-only for the first demo. Editing should happen in
the existing field editor/table, or in a focused follow-up phase.

## Visual Model to Mirror

The official GO viewer uses activity nodes and relation-specific edges. AI
Curation can approximate this with React Flow:

### Nodes

Node label:

- primary: gene symbol or gene product label
- secondary: molecular function GO label
- chips: cellular component, biological process, evidence code, validation
  status

Node types:

- activity: gene product plus molecular function
- process/context group: optional swimlane or grouping by BP
- molecule/complex: optional later, if extracted from paper
- orphan annotation: standard GO annotation without a causal relationship

Node status:

- proposed: extracted but not reviewed
- accepted: curator accepted
- rejected: curator rejected
- blocked: validation blocking issue
- warning: non-blocking validation finding

### Edges

Use relation labels and styling inspired by the GO Web Components relation map:

| Relation family | Example RO relation | Suggested visual |
| --- | --- | --- |
| Direct positive regulation | `RO:0002629` | solid green arrow/triangle |
| Direct negative regulation | `RO:0002630` | solid red inhibition marker |
| Indirect positive regulation | `RO:0002407` | dashed green arrow/triangle |
| Indirect negative regulation | `RO:0002409` | dashed red inhibition marker |
| Causally upstream positive effect | `RO:0002304` | dashed light-green arrow |
| Causally upstream negative effect | `RO:0002305` | dashed light-red inhibition marker |
| Provides input for | `RO:0002413` | purple input edge |
| Removes input for | `RO:0012010` | muted red removal edge |
| Has input / output | `RO:0002233` / `RO:0002234` | blue/pink context edges |

React Flow markers will not perfectly match the Cytoscape glyph vocabulary on
day one. That is acceptable if labels, colors, dashed/solid lines, and the
legend make the relation unambiguous.

## Draft Domain Model

The first domain pack should distinguish flat GO annotation candidates from the
optional flow projection.

### GOAnnotationDraft

Represents a standard GO annotation candidate extracted from a paper.

Core fields:

- `object_id`
- `subject_id`
- `subject_label`
- `subject_taxon_id`
- `subject_taxon_label`
- `go_term_id`
- `go_term_label`
- `go_aspect`: `molecular_function`, `biological_process`,
  `cellular_component`
- `annotation_relation`: for example `enables`, `involved_in`, `located_in`,
  `acts_upstream_of`
- `evidence_code`
- `eco_id`
- `reference_id`
- `with_or_from`
- `qualifier`
- `annotation_extension`
- `assigned_by`
- `confidence`
- `evidence_record_ids`
- `extraction_notes`
- `review_status`

### GOActivityDraft

Represents the activity-centric projection used by the flow page. This can be
derived from one or more `GOAnnotationDraft` objects.

Core fields:

- `activity_id`
- `source_annotation_object_ids`
- `enabled_by`
- `molecular_function`
- `occurs_in`
- `part_of`
- `happens_during`
- `evidence_record_ids`
- `validation_summary`
- `review_status`

### GOFlowRelationDraft

Represents an edge between activity nodes.

Core fields:

- `relation_id`
- `source_activity_id`
- `target_activity_id`
- `relation_curie`
- `relation_label`
- `evidence_record_ids`
- `confidence`
- `validation_summary`
- `review_status`

For the demo, `GOActivityDraft` and `GOFlowRelationDraft` can be projection
objects rather than separately persisted database rows. Persistence can come
after the team decides whether the canonical output is standard GO annotations,
GO-CAM draft models, or both.

Initial contract decision:

- `GOAnnotationDraft` is the primary domain-envelope curatable object for the
  first implementation.
- `GOActivityDraft` and `GOFlowRelationDraft` are graph projection DTOs in the
  first implementation, not independently persisted review rows.
- The GO domain envelope must still include enough explicit flow metadata to
  build the graph deterministically: stable activity IDs, annotation object refs,
  relation endpoints, relation CURIEs/labels, and evidence record IDs.
- If curators need to accept/reject causal edges separately, promote
  `GOFlowRelationDraft` to its own curatable object role in a later domain-pack
  revision.

## Proposed API Contract

Preferred graph endpoint, after the frontend contract settles:

```ts
interface GOFlowGraphResponse {
  session_id: string
  graph_revision: string
  source_envelopes: Array<{
    envelope_id: string
    envelope_revision: number
    domain_pack_id: string
  }>
  nodes: GOFlowNode[]
  edges: GOFlowEdge[]
  warnings: GOFlowWarning[]
}

interface GOFlowNode {
  id: string
  candidate_ids: string[]
  object_ids: string[]
  source_annotation_object_ids: string[]
  type: 'activity' | 'context' | 'molecule' | 'orphan_annotation'
  label: string
  enabled_by?: GOTermOrEntityRef
  molecular_function?: GOTermOrEntityRef
  occurs_in?: GOTermOrEntityRef
  part_of?: GOTermOrEntityRef
  evidence_record_ids: string[]
  evidence_anchor_ids: string[]
  validation_summary_ids: string[]
  validation_status: string
  review_status: string
  metadata: Record<string, unknown>
}

interface GOFlowEdge {
  id: string
  source: string
  target: string
  source_object_ids: string[]
  target_object_ids: string[]
  relation_curie: string
  relation_label: string
  evidence_record_ids: string[]
  evidence_anchor_ids: string[]
  validation_summary_ids: string[]
  validation_status: string
  review_status: string
  metadata: Record<string, unknown>
}

interface GOTermOrEntityRef {
  id: string
  label?: string
  category?: string
  aspect?: string
}

interface GOFlowWarning {
  code: string
  message: string
  candidate_ids?: string[]
  object_ids?: string[]
}
```

The response should reference existing evidence anchor IDs and candidate IDs
rather than duplicating the full workspace. The frontend can join the graph to
the already-loaded workspace state.

For the MVP, a client-side adapter can produce this shape from GO-shaped review
row metadata. That should be treated as a temporary bridge. The implementation
tickets should decide whether the graph is produced from:

- explicit `go_flow` metadata in a persisted domain envelope
- a backend graph projection endpoint over persisted envelope objects
- both, with the backend endpoint becoming the stable UI contract

## Extraction Agent Plan

Add a new GO extractor, likely under:

```text
packages/alliance/agents/go_annotation_extractor/
```

It should produce a domain-envelope-compatible output with GO annotation draft
objects and evidence records.

Adding the folder is not enough. The implementation must also register the
agent/domain pack and adapters in the package/config surfaces used by the
Alliance package.

Registration/config files to expect:

- `packages/alliance/package.yaml`
- `packages/alliance/tools/bindings.yaml`
- GO domain-pack config under `config/` or `packages/alliance/`, following the
  current domain-pack conventions
- `packages/alliance/python/src/agr_ai_curation_alliance/curation_adapters.py`
  if a structured submission/preview adapter is introduced

Prompt responsibilities:

- extract only paper-supported GO-relevant claims
- distinguish experimental findings from background, methods-only text,
  enrichment analysis, and prior literature citations
- identify gene product/entity, organism, GO-relevant term text, and evidence
  quote
- decide whether the claim supports MF, BP, CC, or a causal activity relation
- propose an ECO/evidence code when the paper method supports it, with a clear
  reason and uncertainty when ambiguous
- attach exact quotes and page/figure/table evidence through the existing
  evidence machinery
- abstain when the paper does not support a curatable assertion

Useful validation attachments to wire or implement:

- subject/entity validator for gene product identifiers
- ontology term validator for GO terms and ECO terms
- reference validator for PMID/DOI/source reference
- GO annotation lookup validator for existing annotations on the gene
- relation/aspect validator for GO relation compatibility, if added
- duplicate/near-duplicate validator against existing GO annotations, if added

## Validation Rules Needed

The demo does not need every GO production rule, but it should catch obvious
curator-facing issues. Only rules backed by existing validators should be
blocking in the first implementation. GO-specific policy checks that have not
yet been implemented should start as non-blocking curator warnings.

- GO term exists and is not obsolete
- GO aspect matches the annotation relation
- subject identifier is normalized to an Alliance/GO-accepted identifier
- reference is normalized to PMID/DOI/GO_REF where possible
- evidence code is compatible with the paper method and evidence type
- evidence quote supports the exact claim
- annotation is not just a background statement or prior-work citation
- duplicate existing annotation is flagged, not silently proposed as new
- causal edges require explicit textual support, not inferred pathway lore
- qualifiers such as negation are preserved when the claim is negative

## Implementation Slices

### Slice 0: Shared Workspace and PDF Context

Create a shared workspace-loading and PDF-context path before introducing the
flow page. This prevents the new route from duplicating
`CurationWorkspacePage` behavior or accidentally triggering candidate-route
hydration redirects.

Files likely involved:

- `frontend/src/App.tsx`
- `frontend/src/pages/CurationWorkspacePage.tsx`
- `frontend/src/components/pdfViewer/PersistentPdfWorkspaceLayout.tsx`
- `frontend/src/features/curation/workspace/CurationWorkspaceRuntimeProvider.tsx`
- `frontend/src/features/curation/workspace/useSessionHydration.ts`
- a new shared hook or container for curation workspace loading
- route/layout tests covering `/curation/:sessionId/flow`

Acceptance for this slice:

- `/curation/:sessionId/flow` is not interpreted as a candidate route
- `PersistentPdfWorkspaceLayout` recognizes the flow route
- the flow page can load the same session/PDF context without selecting or
  navigating to a candidate
- the existing curation workspace route keeps its current behavior

### Slice 1: Demo Graph Without Backend Endpoint

Use the current workspace payload and review rows to render a first graph.

Files likely involved:

- `frontend/src/App.tsx`
- `frontend/src/pages/CurationWorkspacePage.tsx`
- `frontend/src/pages/GOFlowViewPage.tsx`
- `frontend/src/features/goFlow/types.ts`
- `frontend/src/features/goFlow/goFlowGraphAdapter.ts`
- `frontend/src/features/goFlow/GOFlowCanvas.tsx`
- `frontend/src/features/goFlow/GOActivityNode.tsx`
- `frontend/src/features/goFlow/GOFlowDetailsPanel.tsx`
- `frontend/src/features/goFlow/GOFlowLegend.tsx`
- frontend route/layout tests for the new static `flow` segment

This is enough to prove the UX if we seed or extract GO-shaped domain-envelope
rows.

For layout, start with a deterministic client-side layout: biological process
grouping as columns or swimlanes, then causal/source-to-target rank when edges
are present. Add `dagre` only if the first graph is unreadable with the light
layout. The official GO viewer uses dagre, but a new layout dependency is not
required for the first review demo.

### Slice 2: Domain Pack and Extraction Output

Add a GO domain pack that emits `GOAnnotationDraft` objects and review-row
metadata rich enough for the graph adapter.

Files likely involved:

- `packages/alliance/agents/go_annotation_extractor/*`
- `packages/alliance/package.yaml`
- `packages/alliance/tools/bindings.yaml`
- domain-pack config under `config/` or `packages/alliance/`, following the
  current domain-pack conventions
- `packages/alliance/python/src/agr_ai_curation_alliance/curation_adapters.py`
  if the GO domain needs a submission/preview adapter
- backend materialization tests for GO review rows
- backend contract tests for GO domain-pack object materialization

### Slice 3: Backend Graph Projection Endpoint

Add a dedicated backend graph endpoint once the frontend contract settles.

Files likely involved:

- `backend/src/api/curation_workspace.py`
- `backend/src/schemas/curation_workspace.py`
- `backend/src/lib/curation_workspace/*`
- unit tests for graph projection from persisted domain envelopes
- contract tests that prove node/edge evidence and validation refs join back to
  workspace candidates

### Slice 4: Export/Handoff

Later, support one or more export paths:

- GAF/GPAD preview for standard GO annotation candidates
- GO-CAM LinkML/YAML draft using `gocam-py` schema concepts
- Noctua/Barista draft handoff through `noctua-py` or another stable GO-owned
  integration surface

Do not make this part of the first demo unless the GO team specifically asks
for write-back. The review UX is the stronger near-term story.

## Demo MVP

For next week's demo, the safest target is:

- one paper processed by a GO extraction flow
- 3-8 GO annotation candidates
- at least two molecular function activity nodes
- one biological process grouping, if the paper supports it
- zero or one causal edge, only if the paper explicitly supports it
- evidence quotes visible from the graph detail panel
- validator badges for entity, GO term, and reference
- existing-annotation/duplicate warning when that lookup is available
- `Flow view` button from the curation workspace header
- no production write to Noctua

If there are no supported causal edges, the graph should still be useful:
multiple activity nodes can be grouped under the biological process context, and
the UI should clearly label the model as a draft review projection rather than a
complete GO-CAM.

## Open Questions for Chris/GO

- Should the first demo emphasize standard GO annotations, GO-CAM activity
  drafts, or both?
- Which paper should we use as the demonstration paper?
- Which organism/database conventions matter most for the audience?
- What evidence code policy should the demo use for ambiguous paper methods?
- Is duplicate detection against current GO annotations expected in the first
  demo?
- Should the graph allow editing, or is read-only review with links back to the
  table/editor acceptable?
- If write-back is discussed, should the target be GAF/GPAD, GO-CAM LinkML,
  Noctua/Barista, or a GitHub PR workflow?
- Did Chris mean `noctua-py` or another newer GO-owned integration surface for
  agentic handoff, or mainly the DisMech-style LinkML validation/QC loop?

## Recommended Next Step

For a demo branch needed within a few days, build the `Demo Branch Cut` first:

1. Add `/go-flow-demo/:sessionId?` outside `PersistentPdfWorkspaceLayout`.
2. Add a workspace `Flow view` launch button beside `Preview submission`.
3. Build the React Flow demo page with static GO activity graph data.
4. Fetch workspace metadata when `sessionId` is present, but keep static graph
   fallback mandatory.
5. Add a small adapter only if the current workspace has GO-shaped metadata
   worth showing.
6. Run frontend build/type-check or a narrow smoke test.
7. Use the demo to ask GO curators whether the candidate fields and graph
   review affordances match their mental model before investing in write-back.

After the demo, harden the winning approach with the session-scoped
`/curation/:sessionId/flow` route, shared workspace/PDF context, domain-pack
registration, and backend graph projection described in the implementation
slices above.

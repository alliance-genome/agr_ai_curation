# Agent Studio UI Redesign — Design Spec

Date: 2026-06-03. Status: **DESIGN APPROVED by Chris (live-prototype review).** Not yet implemented for real — the design below was validated as a *throwaway* hot-reload prototype; the production implementation is a separate, clean build (see "Implementation notes").

## Context & goal

Chris flagged that the Agent Studio is hard to navigate and confusing: the **Agent Workshop** was a ~3,500px wall of stacked boxes with "so many lines and boxes," and the **Agents-tab detail** view is dense. Goal: make both screens clearly navigable and approachable for **working biocurators with zero tech background** (same audience constraint as the doc-migration work), and give the app a less-generic, more usable look. Validated via live prototyping against the sandbox backend.

## Approved design

### A. Agent Workshop layout (PromptWorkshop.tsx)

Restructure the right-hand config panel from one long scroll into a **fixed header + section-tabbed body**:

- **Fixed header (does not scroll):**
  - Overline "CONFIGURE YOUR AGENT" + the agent name as a prominent heading (read-only display of current name; "New Agent" placeholder when empty) + a provenance chip ("Template: {name}" / "Custom — cloned from {source}" / "Custom — from scratch").
  - A segmented nav: **Setup · Prompt · Tools · Reference** (MUI ToggleButtonGroup). Because it lives in the non-scrolling header, it never overlaps content / intercepts clicks (the original sticky-nav bug).
- **Scrollable body — one section at a time:**
  - **Setup:** ALL identity grouped together, top-down — Starting point (Template/Scratch/Clone) → Template/Clone source → Icon + Agent Name → Description → a "MODEL & BEHAVIOR" subheading → Model / Visibility / model-details.
    - **Field sizing:** Agent Name is single-line, `maxWidth ~420` (not full-width). Description is multiline `minRows={2} maxRows={5}` (grows; not a giant empty box).
    - **Model cleanups:** the model dropdown shows only the models in use — **GPT-5.5** and **GPT-5.4 Mini** (filter out `openai/gpt-oss-120b`). **Output Schema Key** input removed from the UI (curators don't need it; keep the `outputSchemaKey` state for hydration/save). The selected-model detail box simplified to one consistent label font + neutral outlined chips (no mixed success/warning colors); keep the Reasoning-level control.
  - **Prompt:** the editable **Your custom instructions** (Curator Overlay) open by default + **Final instructions (preview)**. Friendlier overlay placeholder: *"Write any extra instructions you want this agent to follow. They're added on top of the agent's built-in instructions, so you don't need to repeat anything that's already there."* Keep the "Discuss prompt changes with Claude" action in this card's header.
  - **Tools:** the tool picker (existing Advanced/Tools content).
  - **Reference:** a plain-language read-only intro box — *"These are the built-in instruction layers that make up this agent. They're read-only here — shown so you can see what your own instructions (on the Prompt tab) build on. You don't need to change anything on this tab."* — followed by the read-only layers.

### Curator-voice label renames (Workshop prompt layers)

| Old (jargon) | New (plain) | Chip |
|---|---|---|
| Core Prompt | **Built-in instructions** | Locked |
| Generated Contract | **Output structure** | Automatic |
| Base Prompt | **Template instructions** | From template |
| Group Rules | **Species & group rules** | (override-count chip unchanged) |
| Curator Overlay | **Your custom instructions** | — |
| Effective Prompt Preview | **Final instructions (preview)** | — |

### B. App-wide visual theme (theme.ts + index.html)

- **Font:** swap Roboto → **Geist** (Google Fonts: `family=Geist:wght@300;400;500;600;700&display=swap` + preconnects in `index.html`). `typography.fontFamily = '"Geist","Inter","Roboto","Helvetica","Arial",sans-serif'`, `fontWeightMedium: 500`.
- **Palette (dark mode) — colorful with real contrast (NOT monochrome):**
  - primary: `main #3b82f6`, `light #60a5fa`, `dark #2563eb`, `contrastText #ffffff` (vivid blue accent on tabs/toggles/links/selected).
  - **AppBar: a confident deep-blue bar** — `backgroundColor #1c5fb8`, `color #ffffff`, `borderBottom 1px solid rgba(255,255,255,0.16)`, elevation 0. (Refined deep blue, not the original neon `#2196f3`, but clearly colored — an earlier all-dark bar read as flat black and was rejected.)
  - surfaces (clearly layered, faint cool tint): `backgroundDefault #0f1217`, `backgroundPaper #1b212b`, `dataGridHeader #222a36`, `divider rgba(255,255,255,0.16)`.
  - Functional chips (info/success/warning) keep their own colors — not everything forced to blue.
- **Tinted shadows:** elevated surfaces (Dialog/Drawer/Menu paper, Card) use `0 4px 24px rgba(2,8,20,0.5)` (carries the dark-blue hue) instead of harsh black; flat Paper stays unshadowed.
- **Tabular figures:** `MuiCssBaseline` body `font-variant-numeric: tabular-nums` so IDs/counts/parameter tables align.
- Light mode: keep a comparable colored bar + the existing light tokens; this spec focused on dark mode (the default).

### C. Component upgrades (Agent Studio)

- **Tool Details = right-anchored slide-over** (`Drawer anchor="right"`, width `{ xs: '100%', sm: 520 }`) instead of a centered modal. Same open/onClose/content.
- **Agent Browser composed empty state:** replace the bare "Select an agent…" with a centered icon (`AutoAwesomeOutlined`) + "Browse your agents" + *"Pick an agent on the left to see what it does, the tools it uses, and the validation that applies."*

### D. Agents-tab detail view (AgentDetailsPanel) — apply the same patterns

Carry the Workshop's principles to the agent detail view Chris also flagged as cluttered: an identity crown for the selected agent, sectioning to reduce the simultaneous box/line density, curator-voice labels, and the new theme. **Less-specified than the Workshop** — to be designed concretely during implementation (likely its own short design pass), reusing the same structure/voice/theme decisions above.

**Follow-up status (2026-06-03, after the redesign implementation):** The detail view already inherits the app-wide theme (Geist/accent/layered surfaces), the Tool-Details slide-over, and the composed empty state, so it reads better immediately. The deeper restructure remains a **separate brainstorm → spec → plan** effort. Concrete pain points to address there: (1) the Overview/Guidance/Envelope/Prompts tabs lack an identity "crown" for the selected agent (apply the Workshop pattern); (2) the **Envelope tab** (`DomainEnvelopeMetadataPanel`, ~42KB) is the densest surface, especially for validators — the main target for density reduction; (3) align tool-chip + section spacing with the new theme. Not started.

## Implementation notes

- The validated design was a **throwaway hot-reload prototype** (uncommitted edits to `theme.ts`, `index.html`, `App.tsx`, `PromptWorkshop.tsx`, `ToolDetailsDialog.tsx`, `AgentDetailsPanel.tsx`, plus a dev-only `vite.config.ts` proxy-target env var). It is the **visual reference**, not the shippable code.
- Production build should be done **cleanly with the frontend-design skill + tests**, reproducing the exact values above. It is **separate from the Phase 1/2 doc-migration** (which is committed and PR-ready on `agent-studio-phase1-doc-migration`) — give the UI redesign its own branch so the two PRs stay independent.
- Theme changes are **app-wide** (every page), so verify other screens (Home, Curation, Documents, Batch) still read well in the new palette.
- Sequence suggestion: (1) theme (font + palette + shadows + tabular), (2) Workshop restructure + labels + model cleanups, (3) component upgrades (Drawer, empty state), (4) Agents-detail pass.

## Out of scope / decisions
- Output Schema Key removed from the UI only (state retained). GPT-OSS-120B removed from the model picker (not in use).
- One accent (blue); functional status colors retained.
- Light-mode polish is a follow-up; dark mode (default) is the priority.

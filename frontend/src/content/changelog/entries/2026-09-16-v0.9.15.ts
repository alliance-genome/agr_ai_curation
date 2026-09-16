import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-16-v0.9.15',
  version: '0.9.15',
  date: 'September 16, 2026',
  title: 'AI Curation v0.9.15 — Extraction, handoff and Agent Studio fixes',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.15',
  sections: [
    {
      heading: 'Extraction and validation',
      bullets: [
        'Disease extractions that find no candidates in a paper now finish and report that outcome instead of failing at the last step.',
        'Allele lookups return richer candidate details and more matches, and state plainly when a search could not cover every possibility. This supports allele validation without changing identity or evidence rules.',
        'Extractors can pass finding-specific guidance to validators so that a validator sees the context behind each extracted object, not only the shared paper evidence.',
        'Papers whose sections were only partly indexed no longer break document context preparation. Sections without a known position are kept, and the application reports when context could not be retrieved.',
      ],
    },
    {
      heading: 'Curation Handoff and saved flows',
      bullets: [
        'Curation Handoff now keeps your group access when it prepares results for review, so extractions run under a group no longer fail at the handoff step. Earlier failed handoffs are not repaired automatically; use Review & Curate on the affected paper to retry.',
        'OpenRouter models are no longer available for selection or execution. Saved agents and flows that still reference an OpenRouter model are kept as-is but will not run until they are moved to an approved model.',
        'Custom validators you own can now be attached as validation steps when editing a flow in Agent Studio, using the exact saved revision you choose.',
      ],
    },
    {
      heading: 'Agent Studio',
      bullets: [
        'Code searches with an invalid pattern now return a correctable message instead of a generic failure, and a literal (exact text) search mode is available.',
        'Studio reports the outcome of tool calls more truthfully: a tool that returned an error is no longer shown as a successful step.',
      ],
    },
    {
      heading: 'Under the hood',
      bullets: [
        'Internal failure reporting is more precise: returned tool errors, document import problems and PDF processing failures are classified and reported once with safe context. Delivery of failure notifications to the team remains a separate follow-up.',
      ],
    },
  ],
};

export default entry;

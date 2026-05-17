import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-05-11-v0.7.0',
  version: '0.7.0',
  date: 'May 11, 2026',
  title: 'Domain Envelopes and Automatic Validation',
  sections: [
    {
      heading: 'Curation Review',
      bullets: [
        'Extraction results are now stored as domain envelopes: durable objects with field paths, validation findings, evidence links, and history.',
        'The curation review table shows projected envelope object rows, so edits and decisions stay tied to the saved envelope revision.',
        'Field-level validation findings and evidence anchors are visible while reviewing projected objects.',
      ],
    },
    {
      heading: 'Agent Studio and Flows',
      bullets: [
        'Agent Studio shows domain-envelope metadata for extraction agents, including object types, field paths, schema/provider references, and validation policy.',
        'Flow Builder automatically attaches active validators from domain-pack metadata and shows under-development validators as read-only metadata.',
        'Active validators can be unchecked when replacing automatic validation with custom validation; opt-out reasons are requested only when a specific validator policy requires one.',
      ],
    },
    {
      heading: 'Export and Submission',
      bullets: [
        'Export and submission previews now check envelope revision, required fields, validation findings, definition state, and adapter readiness before allowing final actions.',
        'Readiness blockers identify the affected envelope object and field path, with curator override labels when policy permits an override.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Domain packs now define object metadata, automatic validation, repair policy, materialized review projections, and export/submission adapters.',
        'Database lookup attempts are preserved as an audit trail, including transient attempts that may occur before a later successful retry.',
        'Release-gate tests cover provider-neutral fixtures, Alliance domain packs, LinkML grounding, live DB opt-in contracts, repair, materialization, export/submission, and TraceReview support.',
      ],
    },
  ],
};

export default entry;

import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-05-11-v0.7.0',
  version: '0.7.0',
  date: 'May 11, 2026',
  title: 'Domain Envelopes and Automatic Validation',
  sections: [
    {
      heading: 'Automatic Validation',
      bullets: [
        'Standard validators (genes, alleles, diseases, phenotypes, ontology terms, and more) now run automatically based on the kind of data being curated, so you no longer attach them to flows by hand.',
        'Flow Builder wires in the right validators for you and shows validators still under development as read-only, so it is clear what is actually running.',
        'You only add validation to a flow yourself for custom checks that replace or supplement the automatic ones for a specific object, field, or question.',
        'Validation findings now appear at the field level while you review, right next to the value they apply to.',
      ],
    },
    {
      heading: 'New: Gene Expression Curation',
      bullets: [
        'A new end-to-end Gene Expression pipeline turns each expression statement in a paper into its own reviewable record.',
        'Genes, ontology terms (anatomy, stage, and so on), assay methods, and paper references are resolved to Alliance identifiers automatically.',
        'Expression records have their own review layout, field editing, and export/submission, all grounded in the Alliance data model.',
      ],
    },
    {
      heading: 'Curation Review',
      bullets: [
        'Extraction results are now saved as durable, structured records, each with its source field, validation findings, evidence links, and full history.',
        'The review table shows these saved records directly, so your edits and decisions stay tied to the exact version you reviewed.',
        'Unresolved or still-to-be-decided values are shown clearly, and anything that would block an export or submission points at the specific object and field.',
      ],
    },
    {
      heading: 'Export and Submission',
      bullets: [
        'Before an export or submission, the system checks the saved version, required fields, validation findings, definition state, and whether the target is ready to receive the data.',
        'If something is not ready, the blocker names the exact object and field involved, and is clearly labeled as overridable when policy allows it.',
      ],
    },
    {
      heading: 'Agent Studio and Flows',
      bullets: [
        'Agent Studio shows the data shape behind each extraction agent: the object types it produces, their fields, the schema it is grounded in, and its validation policy.',
        'Agent Workshop "review with Claude" now uses your current draft after manual edits, and Agent Studio chats keep their context and tool-call history.',
        'The flow designer has a dedicated, always-visible Save button, and Ctrl-S now saves.',
      ],
    },
    {
      heading: 'Other Improvements',
      bullets: [
        'Restored the PDF viewer\'s search, zoom, and page-navigation controls, with a fix for browsers that previously broke it.',
        'Feedback forms are now a single movable popup that no longer blocks the rest of the screen, and behave the same across chat, batch, and Agent Studio.',
        'Fixed a false "document loading" timeout after a successful document selection, and a custom-agent save failure tied to the allele extractor\'s evidence tool.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Database lookups during validation now keep a full audit trail, including transient attempts that happened before a later successful retry.',
        'Malformed structured output from a model now fails clearly instead of being quietly patched up, so problems surface during review rather than slipping through.',
        'A large release-gate test suite now covers the whole pipeline: provider-neutral fixtures, the Alliance domain packs, live-database checks, export/submission, and TraceReview support.',
      ],
    },
  ],
};

export default entry;

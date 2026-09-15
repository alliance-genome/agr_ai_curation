import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-15-v0.9.14',
  version: '0.9.14',
  date: 'September 15, 2026',
  title: 'AI Curation v0.9.14 — Chat, evidence and saved-flow fixes',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.14',
  sections: [
    {
      heading: 'Chat and downloadable results',
      bullets: [
        'Improved conversation continuity when opening Agent Workshop and saved history when a flow is stopped or interrupted.',
        'Chat now retains explanatory answers alongside downloadable files and more reliably recognizes files that were successfully saved.',
        'Completed extractions that find no matching objects now report that outcome without inventing a downloadable file.',
        'The assistant more clearly explains unsupported searches and edits, without suggesting that rephrasing a request will enable a missing feature.',
      ],
    },
    {
      heading: 'Evidence and flow editing',
      bullets: [
        'Supporting evidence can retain multiple distinct quotes and their locations across the supported data types. This does not relax identity or validation requirements.',
        'Summary exports better preserve links between extracted mentions, their validation results and the original evidence, including unresolved mentions.',
        'Improved inspection of proposed flow edits and guidance for inspecting the exact saved revision of a custom agent.',
        'Existing failed results are not automatically repaired. Saved allele output layouts may need review and resaving before use with the updated evidence support.',
      ],
    },
    {
      heading: 'Under the hood',
      bullets: [
        'Improved model-cost reporting and tool-error reporting. Delivery of tool-error email alerts remains a separate follow-up.',
      ],
    },
  ],
};

export default entry;

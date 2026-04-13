import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-13-v0.5.5',
  version: '0.5.5',
  date: 'April 13, 2026',
  title: 'Curation Evidence Navigation Hotfix',
  sections: [
    {
      heading: 'Evidence Navigation',
      text: 'This hotfix aligns curation review evidence navigation with the chat viewer so both surfaces use the same quote-centric PDF navigation contract.',
      bullets: [
        'Curation review now dispatches the richer workspace evidence record instead of drifting to flattened preview text when both are available.',
        'Quote-centric viewer commands now describe the live quote being navigated, which keeps chat and curation aligned on native PDF.js highlighting behavior.',
        'Added regression coverage for chat-vs-curation viewer equivalence and for the review-table evidence dispatch path.',
      ],
    },
  ],
};

export default entry;

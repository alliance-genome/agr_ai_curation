import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-14-v0.8.14',
  version: '0.8.14',
  date: 'July 14, 2026',
  title: 'Review & Curate Flow Hotfix',
  sections: [
    {
      heading: 'Flow Review',
      bullets: [
        'Completed flows with extracted annotations can once again prepare and open a curation workspace even when the flow did not create a review session in advance.',
        'Flows with existing prepared review sessions continue to open the exact session or offer the same adapter-specific choice.',
        'Runs without extracted candidates or complete document and flow scope remain unavailable for review instead of opening an ambiguous workspace.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added regression coverage for extraction-only flow completion and the corresponding zero-extraction safety case.',
      ],
    },
  ],
};

export default entry;

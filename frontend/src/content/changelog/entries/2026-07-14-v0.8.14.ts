import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-14-v0.8.14',
  version: '0.8.14',
  date: 'July 14, 2026',
  title: 'Curation & Extraction Hotfix',
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
      heading: 'Controlled Vocabulary Extraction',
      bullets: [
        'PDF extraction and file-formatting specialists now receive the complete current request, preserving curator-supplied vocabularies, exclusions, and output rules instead of relying on a shortened handoff.',
        'Long controlled-vocabulary prompts remain intact when routed to PDF extraction and file-export specialists.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added regression coverage for extraction-only flow completion and the corresponding zero-extraction safety case.',
        'Added regression coverage proving that long controlled vocabularies reach isolated specialists without truncation.',
      ],
    },
  ],
};

export default entry;

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
      heading: 'Chat & File Exports',
      bullets: [
        'Active chat and flow runs now remain visible and continue streaming when you move from Home to another section and return.',
        'New CSV, TSV, and JSON flow steps default to readable filenames based on the source PDF, with explicit source, custom-prefix, and existing formatter-controlled choices.',
        'Structured export filenames now begin with their readable paper or custom descriptor so files from batch runs sort together, while retaining trace and branch identifiers for uniqueness.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added regression coverage for extraction-only flow completion and the corresponding zero-extraction safety case.',
        'Added regression coverage proving that long controlled vocabularies reach isolated specialists without truncation.',
        'Added regression coverage for cross-page stream survival and descriptor-first structured output naming.',
      ],
    },
  ],
};

export default entry;

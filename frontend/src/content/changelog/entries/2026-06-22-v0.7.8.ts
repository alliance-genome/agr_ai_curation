import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-06-22-v0.7.8',
  version: '0.7.8',
  date: 'June 22, 2026',
  title: 'Same-Turn CSV Export Hotfix',
  sections: [
    {
      heading: 'Downloadable Extraction Exports',
      bullets: [
        'Extraction chats can now create downloadable CSV, TSV, or JSON files in the same turn that produces the extracted rows.',
        'When a curator asks for an export, the supervisor can use the formatter after the extraction finishes instead of saying that a CSV export tool is unavailable.',
        'This fixes the tumor-term extraction workflow where valid rows were found but the CSV content was only shown inline.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Formatter tools now bind to the latest saved extraction result at call time, including results saved earlier in the same supervisor turn.',
        'Added regression coverage for formatter availability before an extraction result exists and for same-turn formatter dispatch after the extractor saves rows.',
      ],
    },
  ],
};

export default entry;

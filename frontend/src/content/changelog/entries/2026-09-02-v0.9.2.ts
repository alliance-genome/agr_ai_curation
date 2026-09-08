import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-02-v0.9.2',
  version: '0.9.2',
  date: 'September 2, 2026',
  title: 'Reliable Source Labels in Reagent TSV Exports',
  sections: [
    {
      heading: 'TSV Exports',
      bullets: [
        'Reagent TSV exports can now apply the flow’s source-label rule to each row: reagents introduced in the paper display “New in paper,” while existing reagents display their source and identifier.',
        'The same source-label formatting now works in both batch and AI Assistant flow runs, including rows with multiple source identifiers.',
      ],
    },
  ],
};

export default entry;

import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-15-v0.8.15',
  version: '0.8.15',
  date: 'July 15, 2026',
  title: 'Flow Builder Visual Hotfix',
  sections: [
    {
      heading: 'Flow Builder',
      bullets: [
        'CSV, TSV, and JSON formatter steps now use the same clean node styling as other agents instead of appearing inside an extra white box.',
        'Connections into formatter steps animate consistently with the rest of the flow, making the data path easier to follow.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added regression coverage for both newly connected and reloaded formatter steps.',
      ],
    },
  ],
};

export default entry;

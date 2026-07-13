import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-13-v0.8.12',
  version: '0.8.12',
  date: 'July 13, 2026',
  title: 'Saved Flow Compatibility Fix',
  sections: [
    {
      heading: 'Saved Flows',
      bullets: [
        'Existing saved flows continue to run after the formatter-output update, including flows that need curator review before adopting a typed output attachment.',
        'Unambiguous saved formatter flows are upgraded automatically to use their single extraction source.',
        'Older flows missing an Initial Instructions step receive the same default run instruction they used previously.',
      ],
    },
    {
      heading: 'Flow Execution',
      bullets: [
        'Output formatters run after ordinary flow steps while still using their explicitly selected extraction result.',
        'Agent Studio identifies flows retained in compatibility mode without blocking them from running.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added an audited, owner-aware flow migration, preserved legacy aggregation for ambiguous graphs, and added production-inventory and migration regression coverage.',
      ],
    },
  ],
};

export default entry;

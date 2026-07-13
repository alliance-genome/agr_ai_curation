import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-13-v0.8.13',
  version: '0.8.13',
  date: 'July 13, 2026',
  title: 'Complete Saved Flow Upgrade',
  sections: [
    {
      heading: 'Saved Flows',
      bullets: [
        'Every runnable saved flow now uses the current typed graph format instead of a legacy compatibility path.',
        'Existing allele, multi-domain, custom-text, validation, and GO workflows were upgraded according to their saved instructions and execution history.',
        'Flows whose retired or inactive agents are no longer available are preserved as inactive archives for later rebuilding.',
      ],
    },
    {
      heading: 'Flow Outputs',
      bullets: [
        'One output formatter can now use several explicitly selected extraction or structured validation results without running more than once.',
        'Chat and file outputs retain the full list of source steps for audit and batch downloads.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added an audited, fail-closed production migration with exact pre-change checks, complete rollback records, and a zero-legacy postcondition.',
        'Removed the temporary v1.0 formatter compatibility execution and resave paths.',
      ],
    },
  ],
};

export default entry;

import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-11-v0.5.2',
  version: '0.5.2',
  date: 'April 11, 2026',
  title: 'System Agent Recovery',
  sections: [
    {
      heading: 'Startup Reliability',
      text: 'Supersedes the 0.5.1 candidate after fixing a live startup regression in the unified system-agent sync path.',
      bullets: [
        'Shipped system agents are now reactivated when their current config source is still present, allowing the dev runtime to recover cleanly after an earlier validation disable.',
        'This prevents stale inactive Agent Studio rows from blocking backend startup with missing active system-agent errors.',
      ],
    },
    {
      heading: 'Release Validation',
      bullets: [
        'Added regression coverage for reactivating discovered system agents during sync.',
        'Re-validates the dev deployment after the earlier 0.5.1 candidate exposed the live startup gap.',
      ],
    },
  ],
};

export default entry;

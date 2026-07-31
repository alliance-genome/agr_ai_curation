import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-31-v0.8.18',
  version: '0.8.18',
  date: 'July 31, 2026',
  title: 'Literature Upload Recovery Hotfix',
  sections: [
    {
      heading: 'Documents',
      bullets: [
        'Literature imports now continue with the curator-uploaded PDF when converted text cannot be accessed.',
        'Failed uploads can be deleted and retried instead of remaining blocked by stale processing status.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Upload job and progress states now agree when provider processing fails, while active retries remain protected from deletion.',
      ],
    },
  ],
};

export default entry;

import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-10-v0.8.8',
  version: '0.8.8',
  date: 'July 10, 2026',
  title: 'Flow Completion Hotfix',
  sections: [
    {
      heading: 'Flow Execution',
      bullets: [
        'Flows can now complete required steps placed after a chat-output step, including creating Review & Curate handoff sessions.',
        'The chat response is preserved while downstream required steps finish and is delivered after the handoff is ready.',
      ],
    },
  ],
};

export default entry;

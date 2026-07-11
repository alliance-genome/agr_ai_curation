import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-11-v0.8.10',
  version: '0.8.10',
  date: 'July 11, 2026',
  title: 'Agent Studio Reliability Hotfix',
  sections: [
    {
      heading: 'Agent Studio',
      bullets: [
        'Restored Chat with Claude in Agent Studio after a runtime packaging problem prevented its system instructions from loading.',
        'Restored Agent Studio trace analysis so Claude can retrieve trace summaries, conversations, tool calls, diagnostics, and related evidence again.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added deployment-aware tests for the Agent Studio prompt source and service-to-service TraceReview authentication.',
        'Expanded release verification to require a real Agent Studio trace-summary tool call before production traffic is restored.',
      ],
    },
  ],
};

export default entry;

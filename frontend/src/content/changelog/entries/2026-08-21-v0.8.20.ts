import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-08-21-v0.8.20',
  version: '0.8.20',
  date: 'August 21, 2026',
  title: 'Production Monitoring Hotfix',
  sections: [
    {
      heading: 'Reliability',
      bullets: [
        'Application errors and AI workflow performance can now be diagnosed more quickly when a curator reports a problem.',
        'Monitoring keeps prompts, paper content, credentials, and personal information out of captured diagnostic events.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added bounded Sentry request sampling, redacted error-log promotion, and privacy-safe agent and tool telemetry.',
        'Known harmless connection-cleanup noise is filtered so real failures remain visible.',
      ],
    },
  ],
};

export default entry;

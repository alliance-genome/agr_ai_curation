import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-03-05-v0.3.3',
  version: '0.3.3',
  date: 'March 5, 2026',
  title: 'AI Curation Platform Update',
  sections: [
    {
      heading: 'Background PDF Processing Is More Reliable',
      text: 'Large uploads now continue processing in durable background jobs so your curation flow is less likely to be interrupted.',
      bullets: [
        'Long-running PDF tasks are more resilient and can recover cleanly after interruptions.',
        'Canceling queued or running PDF jobs is now handled safely and consistently.',
      ],
    },
    {
      heading: 'Improved Batch Upload Experience',
      text: 'Working through multiple papers is smoother, with clearer progress and better handling for multi-file uploads.',
      bullets: [
        'Batch upload behavior was improved to better support real curation workloads.',
        'Status and progress updates are clearer while background work is running.',
      ],
    },
    {
      heading: 'Curation Prompt Quality Improvements',
      text: 'Agent prompts were refined using paper-informed updates to improve extraction quality and reduce noisy outputs.',
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added an automated Agent PR Gate workflow to validate pull requests with repository standards.',
        'Added scheduled harness hygiene checks for ongoing workflow health monitoring.',
        'Fixed audit history not clearing properly on chat reset.',
      ],
    },
  ],
};

export default entry;

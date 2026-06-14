import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-06-14-v0.7.6',
  version: '0.7.6',
  date: 'June 14, 2026',
  title: 'Chat Extraction Handoff, Persistence, and PDF Loading Fix',
  sections: [
    {
      heading: 'PDF Loading',
      bullets: [
        'Fixed PDFs failing to load (endless spinner) on some browsers; the viewer no longer depends on a very new browser feature.',
      ],
    },
    {
      heading: 'Better Extraction Answers in Chat',
      bullets: [
        'When you ask the assistant what it found, it now answers from the full list of extracted objects instead of just the first few.',
        'You can list, inspect, and export those objects without re-running the extractor.',
      ],
    },
    {
      heading: 'Extractions Are Saved Right Away',
      bullets: [
        'A validated extraction is saved as soon as it finishes, so cancelling the rest of a turn or asking a follow-up no longer loses it.',
        'Follow-up questions can refer back to the extraction you just produced and export those rows.',
      ],
    },
    {
      heading: 'Clearer Audit Messages',
      bullets: [
        'A non-fatal validator issue now shows as a warning instead of looking like the specialist failed.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Replaced the supervisor result-lookup with a single read-only result browser plus immediate, idempotent persistence of validated extractions.',
        'Flow runs no longer write a duplicate chat-source extraction row.',
        'Polyfilled the PDF.js worker for older browsers (Uint8Array.toHex).',
        'Added unit and integration coverage for inline persistence, idempotency, result inspection, audit severity, and the batch release smoke.',
      ],
    },
  ],
};

export default entry;

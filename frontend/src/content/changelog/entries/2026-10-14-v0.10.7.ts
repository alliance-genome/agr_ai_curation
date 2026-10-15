import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-14-v0.10.7',
  version: '0.10.7',
  date: 'October 14, 2026',
  title: 'AI Curation v0.10.7 — Shared Agents and More Efficient Chat',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.10.7',
  sections: [
    {
      heading: 'Sharing and Cloning',
      bullets: [
        'Fixed cloning shared extraction agents and flows when their saved output structure belongs to the original agent owner.',
        'Cloning a colleague’s agent gives you a private copy of its output structure that you can edit independently.',
        'Clone failures show an explanation and suggested next steps instead of only an HTTP error number.',
      ],
    },
    {
      heading: 'Longer Extraction and Chat Workflows',
      bullets: [
        'Reduced repeated document text and repeated lists of extracted objects sent during a run.',
        'Agents can look up complete earlier results when they need more detail, including validation findings and discarded candidates.',
        'When a request is too large to continue safely, chat explains the problem and whether to try later or split the task into smaller requests.',
      ],
    },
    {
      heading: 'Starting Flows',
      bullets: [
        'Fixed a case where a flow could report that no paper was loaded even though the paper was already selected for chat.',
        'Flow launches now use the current chat document and prevent starting while the paper selection is still changing. Resetting chat preserves the selected paper.',
      ],
    },
    {
      heading: 'Under the Hood',
      bullets: [
        'Added exact run-history storage and measurements to track context size, recovery overhead and storage performance.',
        'Added checks for source quotes, negative findings, multiple validation results and concurrent object updates.',
      ],
    },
    {
      heading: 'Your Saved Work',
      text: 'Existing saved agents, flows, prompts and results are preserved. These changes do not backfill earlier run history or change your scientific extraction criteria. Flow copies continue to use their selected agents and require access to those agents.',
    },
  ],
};

export default entry;

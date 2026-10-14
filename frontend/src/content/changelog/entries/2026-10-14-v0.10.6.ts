import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-14-v0.10.6',
  version: '0.10.6',
  date: 'October 14, 2026',
  title: 'AI Curation v0.10.6 — Clearer Results and Recoverable Chat Errors',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.10.6',
  sections: [
    {
      heading: 'Complete Results, Manageable Conversations',
      bullets: [
        'The chat supervisor receives the extractor’s full explanation alongside a compact view of the saved results. It can inspect the complete details when needed.',
        'New validation runs retain their original results. Summaries distinguish current decisions from earlier checks, report what was left out of a preview, and prioritize findings that still need attention.',
        'A validator’s correction does not automatically make the final object unresolved. Current decisions and earlier findings remain distinguishable for review.',
      ],
    },
    {
      heading: 'Expression Results and Evidence',
      bullets: [
        'Gene-expression extraction preserves an explicit negative finding when the extractor supplies one.',
        'Chat output receives each statement together with its linked supporting evidence, including multiple quotes. Missing or ambiguous links are made explicit.',
        'Extractors receive consistent instructions for finishing with no qualifying results and explaining exclusions, without inventing a result to complete a request.',
      ],
    },
    {
      heading: 'When a Request Cannot Finish',
      bullets: [
        'Chat and flow errors explain what happened in clearer language, including temporary provider overload and incomplete output.',
        'Failed and stopped chats retain the request, the failure notice, and available partial text. Saved extraction results remain accessible; files from completed flow steps are retained when a later step fails.',
        'Use Restore request to place a failed request back in the empty chat box, then send it when you are ready. The application does not automatically repeat the request.',
      ],
    },
    {
      heading: 'Project Sharing',
      text: 'Corrected default project membership provisioning so eligible curators can share agents with their project. Existing access restrictions still apply.',
    },
    {
      heading: 'Under the Hood',
      text: 'Added measurements of chat context compaction and model output budgets, plus diagnostic links between validation findings and their saved originals.',
    },
    {
      heading: 'Your Saved Work',
      text: 'These improvements apply to new runs. Earlier results are retained, but missing historical validator originals cannot be reconstructed. Failed requests are not restarted automatically.',
    },
  ],
};

export default entry;

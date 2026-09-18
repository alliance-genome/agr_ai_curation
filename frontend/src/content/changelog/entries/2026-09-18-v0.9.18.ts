import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-18-v0.9.18',
  version: '0.9.18',
  date: 'September 18, 2026',
  title: 'AI Curation v0.9.18 — Evidence checks, flow reliability and diagnostics',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.18',
  sections: [
    {
      heading: 'Flow runs and evidence',
      bullets: [
        'Fixed a failure where a flow step that used an attached validator stopped with "Record does not conform to its saved output structure", even though the saved Output Structure was correct. The run was rejecting the application\'s own internal evidence details, not anything the curator had configured.',
        'When this kind of internal problem happens, the application now says so plainly and states that your Output Structure does not need to change, instead of pointing you at your own settings.',
        'Supporting evidence keeps its quotes, page and section details, document links and revision history through validation. Existing saved results are not changed by this release.',
      ],
    },
    {
      heading: 'Agent Workshop',
      bullets: [
        'The extraction diagnostic report now opens for large runs. Previously a run with many steps returned nothing at all, so the assistant could not explain what happened. It now returns a short overview first and fetches each section on request.',
      ],
    },
    {
      heading: 'Under the hood',
      bullets: [
        'Evidence records are now converted to their saved form in one shared place across all data types, instead of seven separate copies that had quietly drifted apart.',
        'Repeated evidence recording within one step no longer leaves a stale hidden copy that later edits could not reach.',
        'Error and trace details are no longer trimmed before they are stored, so production failures can be diagnosed without asking a curator to reproduce them. Credentials are still removed.',
      ],
    },
  ],
};

export default entry;

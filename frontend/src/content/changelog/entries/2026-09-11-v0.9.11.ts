import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-11-v0.9.11',
  version: '0.9.11',
  date: 'September 11, 2026',
  title: 'PDF uploads, CSV exports and flow usability',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10842',
  sections: [
    {
      heading: 'PDF uploads',
      bullets: [
        'Improved drag-and-drop handling across the empty PDF panel, including its header, so dropped files reach the uploader.',
        'Upload instructions now point to Documents in the top navigation bar, then Add Literature, then Upload PDFs.',
      ],
    },
    {
      heading: 'CSV exports and output choices',
      bullets: [
        'Fixed missing nested fields in exports from saved extraction results, including gene-expression data.',
        'When a new file-output step needs a mode choice, Agent Studio asks whether to export saved values directly or use AI formatting. Direct export skips formatter prompts; existing flows keep their current choices.',
        'Validation findings now keep their links to the correct extracted objects when explicit, unambiguous references are available.',
      ],
    },
    {
      heading: 'Flow and chat usability',
      bullets: [
        'Flows now stop before execution when a required document or step is unavailable, with guidance on how to fix it.',
        'Flow titles wrap above the Stop and Hide buttons when the panel is narrow.',
        'Chat no longer asks you to start a conversation while a flow is already running.',
        'Agent Studio shows a spinner in the message box while it is working. Automatic follow-ups after Apply no longer appear as messages written by you.',
        'Objects with open validation findings are marked as needing review. Checks already performed are distinguished from checks that passed.',
      ],
    },
    {
      heading: 'Under the hood',
      bullets: [
        'Release checks now cover stored PDFs and recorded artifacts across all users and processing statuses, including pending and failed uploads, and verify server write access to document folders.',
        'Fixed a server-side permission problem that could prevent uploaded papers from completing processing.',
        'Stopping a flow now stops its background agent work before closing connections.',
        'Fixed flows that failed when multiple validation checks shared the same label.',
        'Restored the literature-reference search connection and added a release check to catch missing configuration.',
        'Updated diagnostic tools to retrieve sessions from the current tracing service and correlate retained error traces, with explicit warnings when evidence is incomplete.',
      ],
    },
  ],
};

export default entry;

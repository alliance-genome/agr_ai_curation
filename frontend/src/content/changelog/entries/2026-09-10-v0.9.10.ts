import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-10-v0.9.10',
  version: '0.9.10',
  date: 'September 10, 2026',
  title: 'Fixes for PDF uploads and curation preparation',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10841',
  sections: [
    {
      heading: 'PDF uploads',
      bullets: [
        'Papers with whitespace-heavy tables no longer fail because a generated chunk contains only blank space. Retry uploads that failed with “Content must not be empty.”',
      ],
    },
    {
      heading: 'Preparing records for curation',
      bullets: [
        'Chat previews the saved results and candidate count before preparation, then requires your confirmation for that exact selection.',
        'Selecting only some candidates within a saved result is not yet supported. Chat now explains that limitation instead of preparing additional candidates.',
      ],
    },
    {
      heading: 'Custom agents and allele requests',
      bullets: [
        'Saving a custom extractor in Workshop now selects record-building tools that match its Custom Output Structure. Existing affected agents need a reviewed new save; their saved flow steps are not updated automatically.',
        'Chat guidance now distinguishes database identity lookup from extracting mentions in a PDF, and explains when direct lookup is unavailable.',
      ],
    },
  ],
};

export default entry;

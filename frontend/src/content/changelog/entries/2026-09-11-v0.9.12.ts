import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-11-v0.9.12',
  version: '0.9.12',
  date: 'September 11, 2026',
  title: 'Chat history cleanup',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10843',
  sections: [
    {
      heading: 'Chat history',
      bullets: [
        'Fixed a background sign-in check that could create a blank conversation every five minutes.',
        'Empty conversations no longer crowd the history list or inflate its conversation count.',
        'Existing messages and active chats are preserved. An empty chat appears in history once it has a stored message.',
      ],
    },
  ],
};

export default entry;

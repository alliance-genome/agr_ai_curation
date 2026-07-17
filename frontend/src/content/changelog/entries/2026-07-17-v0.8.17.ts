import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-17-v0.8.17',
  version: '0.8.17',
  date: 'July 17, 2026',
  title: 'Duplicate Upload Filename Hotfix',
  sections: [
    {
      heading: 'Documents',
      bullets: [
        'When a PDF matches a document you already uploaded under another name, the duplicate message now identifies the existing filename.',
        'The message tells you exactly which filename to search for in Documents, without deleting or re-uploading the existing paper.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Kept duplicate matching private to each curator account and covered both normal and simultaneous-upload duplicate paths.',
      ],
    },
  ],
};

export default entry;

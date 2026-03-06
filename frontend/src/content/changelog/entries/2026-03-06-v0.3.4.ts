import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-03-06-v0.3.4',
  version: '0.3.4',
  date: 'March 6, 2026',
  title: 'AI Curation Platform Update',
  sections: [
    {
      heading: 'Drag-and-Drop PDF Upload',
      text: 'You can now drag and drop PDF files directly into the viewer panel to start curation.',
      bullets: [
        'Drop one or more PDFs onto the viewer to upload them instantly.',
        'Works alongside the existing file picker for flexible upload options.',
      ],
    },
    {
      heading: 'Better Upload Feedback',
      bullets: [
        'A notification now appears when PDF background processing begins, so you know your upload is being handled.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Updated the AI model to the latest GPT release for improved extraction quality.',
        'Improved workspace isolation for multi-environment development and testing.',
      ],
    },
  ],
};

export default entry;

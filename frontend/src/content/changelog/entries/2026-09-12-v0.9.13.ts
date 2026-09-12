import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-12-v0.9.13',
  version: '0.9.13',
  date: 'September 12, 2026',
  title: 'AI Curation v0.9.13 — Paper access and upload fixes',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.13',
  sections: [
    {
      heading: 'WormBase paper access',
      bullets: [
        'Corrected recognition of WormBase curator accounts so their existing permissions are applied when opening papers from the Alliance literature collection and using WormBase agents.',
      ],
    },
    {
      heading: 'More reliable PDF uploads',
      bullets: [
        'When the figure-reading step returns an incomplete or inconsistent response, the application now asks it to correct the response up to two times before reporting an upload failure.',
        'Responses must still pass the same checks before processing continues. Repeated errors can still cause an upload to fail.',
        'Papers that already uploaded successfully do not need to be uploaded again.',
      ],
    },
  ],
};

export default entry;

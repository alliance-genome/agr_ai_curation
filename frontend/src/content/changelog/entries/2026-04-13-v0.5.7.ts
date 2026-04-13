import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-13-v0.5.7',
  version: '0.5.7',
  date: 'April 13, 2026',
  title: 'Shared PDF Workspace and Curation Prep Reliability',
  sections: [
    {
      heading: 'PDF Evidence Parity',
      text: 'Home chat and curation review now share one persistent PDF workspace so evidence highlighting behaves consistently when you move between the two surfaces.',
      bullets: [
        'Chat and curation quote clicks now use the same shared evidence interaction card and the same quote-centric PDF.js navigation path.',
        'The curation review table no longer auto-navigates the PDF on row selection, which removes a major source of drift from the explicit quote-click behavior used in chat.',
        'Route changes between Home and Curation now preserve the loaded PDF viewer instead of remounting a second PDF.js session.',
      ],
    },
    {
      heading: 'Prepare for Curation',
      text: 'Preparing reviewed extraction results for curation is more reliable and the confirmation dialog now reflects the real prep scope.',
      bullets: [
        'The prep dialog now distinguishes discussed candidates from evidence-verified candidates, so it no longer reports contradictory counts.',
        'Allele extractor results stored under specialized `alleles[]` payloads are now recognized as preparable candidates when they carry verified evidence records.',
        'Opening prep scope confirmation no longer mutates chat session stats just to compute preview counts.',
      ],
    },
  ],
};

export default entry;

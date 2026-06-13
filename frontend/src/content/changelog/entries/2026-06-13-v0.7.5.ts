import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-06-13-v0.7.5',
  version: '0.7.5',
  date: 'June 13, 2026',
  title: 'PDF, TSV, and Evidence Reliability Hotfix',
  sections: [
    {
      heading: 'PDF Extraction Exports',
      bullets: [
        'TSV exports from PDF extraction flows now use the extracted curation objects, not a one-row summary of the flow artifact.',
        'Generic PDF extraction now keeps structured rows available for export, including reagent-style tables produced from papers.',
        'Curation TSV exports are stricter about using backend extraction results, so prose answers and artifact summaries cannot silently become curation rows.',
      ],
    },
    {
      heading: 'More Reliable Runs',
      bullets: [
        'OpenAI websocket streaming is more tolerant of transport hiccups, so large extraction runs should avoid the slower non-streaming fallback.',
        'PDF upload and viewer metadata limits now support larger files up to 150 MB.',
        'Saving edited flows now sends the expected payload shape, avoiding the repeated "Extra inputs are not permitted" validation error.',
      ],
    },
    {
      heading: 'Allele Evidence Fixes',
      bullets: [
        'Allele evidence now stays attached to the allele object through validation, which helps multi-allele papers avoid cross-talk between unrelated evidence quotes.',
        'Validators no longer borrow paper-wide evidence when an extracted object has no specific evidence attached.',
        'Flow validation specialists now need a valid extractor connection, so standalone validator steps cannot run from document metadata alone.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added generic PDF builder support and stricter TSV source binding for curation exports.',
        'Added regression coverage for artifact-summary TSV output, legacy row sources, explicit validator evidence, and validator placement rules.',
      ],
    },
  ],
};

export default entry;

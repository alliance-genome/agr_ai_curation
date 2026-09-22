import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-22-v0.9.20',
  version: '0.9.20',
  date: 'September 22, 2026',
  title: 'AI Curation v0.9.20 — More reliable validation results',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.20',
  sections: [
    {
      heading: 'Validation fixes',
      bullets: [
        'Database search results now retain the reported match counts and the source database recorded on each result. Missing source information stays missing rather than being guessed.',
        'The standard allele validator no longer asks for an extra identifier lookup just to confirm details already supplied by the search. Additional lookups remain available when needed to distinguish possible matches.',
        'Choosing a custom validator in place of the standard validator now takes effect before the extraction step starts, preventing both from running for the same check.',
        'Experimental-condition checks now give the model explicit instructions for each part of a composite condition and specific feedback when its response needs correction.',
      ],
    },
    {
      heading: 'Under the hood',
      bullets: [
        'Validators now focus on choosing and explaining a match. The application fills in database facts, search counts and supporting evidence from the records already retrieved, reducing the information the model must copy into its answer.',
        'These changes apply to new runs. Existing saved results and curator-authored instructions are not rewritten.',
      ],
    },
  ],
};

export default entry;

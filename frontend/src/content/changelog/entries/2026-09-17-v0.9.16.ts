import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-17-v0.9.16',
  version: '0.9.16',
  date: 'September 17, 2026',
  title: 'AI Curation v0.9.16 — Allele checks, evidence quotes and flow editing',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.16',
  sections: [
    {
      heading: 'Allele checks and evidence',
      bullets: [
        'Allele validation considers gene, species, allele design, and supplier or laboratory clues together. Supplier and creator names are treated separately; an unexplained difference keeps the identity unresolved for review.',
        'For mouse alleles, synonym matches help find candidates but no longer establish identity on their own, even when there is only one match. Validation requires supporting information that distinguishes the specific allele.',
        'Allele searches can use database-supported gene aliases while retaining the relevant organism and database scope.',
        'Newly processed PDFs keep section boundaries intact so evidence quotes do not pick up unrelated text from a preceding section. Existing processed papers need to be reprocessed to receive this correction.',
      ],
    },
    {
      heading: 'Saved agents and flows',
      bullets: [
        'Selecting a newer saved agent revision refreshes its database checks before the flow is saved. Save errors provide a specific explanation and next step.',
        'Flow Builder shows a dismissible reminder when an extraction step has no database validators connected, with help explaining how to add them. The reminder does not interrupt saving or running a flow.',
      ],
    },
    {
      heading: 'Workspace and document imports',
      bullets: [
        'Resizing the main workspace panels stops when the drag ends, including at the PDF viewer boundary. The right-hand Audit and Tools panel remains reachable.',
        'PDF imports have more time to finish. If an import times out, its status remains accurate while outstanding storage work finishes.',
      ],
    },
  ],
};

export default entry;

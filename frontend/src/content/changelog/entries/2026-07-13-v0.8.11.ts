import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-07-13-v0.8.11',
  version: '0.8.11',
  date: 'July 13, 2026',
  title: 'Flow Output and Literature Hotfix',
  sections: [
    {
      heading: 'Flow Files',
      bullets: [
        'Output formatters can now branch from a specific extraction step, so one flow can continue extracting while producing multiple CSV, TSV, or JSON files.',
        'Flow and batch results now list every generated file and preserve a clear explanation when a formatter cannot produce its requested output.',
        'Agent Studio shows which extraction feeds each formatter and guides curators through repairing older formatter flows.',
      ],
    },
    {
      heading: 'Add Literature and Library',
      bullets: [
        'Resolve now shows a persistent result beside the action and places Identifier Results before background PDF jobs.',
        'Provider Markdown with recoverable heading or table structure can continue through the modern ingestion pipeline instead of failing at 25%.',
        'Failed imports no longer remain visually stuck as pending, and edited display titles persist without renaming the original PDF file.',
      ],
    },
    {
      heading: 'Under The Hood',
      bullets: [
        'Added typed formatter-source attachments, multi-file provenance, durable batch manifests, and regression coverage for the reported curator failures.',
      ],
    },
  ],
};

export default entry;

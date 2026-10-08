import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-10-08-v0.10.4",
  "version": "0.10.4",
  "date": "October 8, 2026",
  "title": "AI Curation v0.10.4 — Benchmark and Export Fixes",
  "sections": [
    {
      "heading": "Benchmark Runs",
      "bullets": [
        "Fixed a configuration problem that could prevent benchmark runs from extracting a publication’s abstract.",
        "Fixed a problem that could prevent completed benchmark extractions from producing a downloadable CSV."
      ]
    },
    {
      "heading": "PDF Names in Exported Files",
      "bullets": [
        "CSV filenames from saved flows now use the selected PDF’s filename, even when the active-document selection is missing or out of date."
      ]
    },
    {
      "heading": "Under the Hood",
      "bullets": [
        "Improved error reporting for sign-in problems, saved chat-analysis records, and document-processing progress, helping us diagnose failures more reliably."
      ]
    },
    {
      "heading": "Your Saved Work",
      "text": "These fixes apply to future runs. Existing prompts, agents, flows, and results are unchanged, and failed runs are not restarted automatically."
    }
  ]
};

export default entry;

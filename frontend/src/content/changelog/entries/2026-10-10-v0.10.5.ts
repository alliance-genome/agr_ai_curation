import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-10-10-v0.10.5",
  "version": "0.10.5",
  "date": "October 10, 2026",
  "title": "AI Curation v0.10.5 — Custom Benchmark Data and Validation Fixes",
  "sections": [
    {
      "heading": "Custom Data in Benchmarks",
      "bullets": [
        "Create reusable data types with the fields you want to compare, alongside genes and alleles.",
        "Add each paper’s expected records by uploading a CSV or TSV, or by adding and editing rows on the paper page. Download a template using your data type’s fields.",
        "Choose an extraction flow and match its outputs to your fields. Your paper draft is saved while you set up the data type or flow."
      ]
    },
    {
      "heading": "One Benchmark Sign-In",
      "bullets": [
        "Signing into the benchmark service also connects it to AI Curation automatically.",
        "Existing benchmark sessions will need one fresh sign-in after the update. Saved papers, flows and results are retained."
      ]
    },
    {
      "heading": "Clearer Validation and Saved Results",
      "bullets": [
        "Removed older text and identifier filters that could reject a validator’s scientific judgment after it had finished checking the evidence. Validators can use lookup tools when needed, and database-backed values still require supporting records.",
        "When a validator cannot finish correcting missing or inconsistent output, its explanation and usable results are retained and clearly marked incomplete.",
        "If a chat fails after saving extraction results, the saved results remain available when you reopen the conversation, including through Review & Curate."
      ]
    },
    {
      "heading": "Your Saved Work",
      "text": "These changes apply to new runs. Earlier failed runs are not restarted automatically."
    }
  ]
};

export default entry;

import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-07-v0.11.4",
  "version": "0.11.4",
  "date": "November 7, 2026",
  "title": "Results that stay on your question",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10964",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "When you ask a narrow question, such as which transgenic flies a paper uses, the assistant now saves only the items that answer it. Controls, wild-type strains and other look-alikes are named in its written answer instead of being saved as records. A control that is itself in the requested category, such as a transgenic control line, is still saved.",
        "The supervisor can now remove a saved record that does not answer your question, and it records why. Removed records stay listed with that reason, and you can restore any of them for review.",
        "Review no longer keeps running when nothing more can change. When it ends with records still pending, chat names each one and says why.",
        "Each record gets one re-check per review round, and asking for a new round with Revalidate gives every record a fresh re-check."
      ]
    },
    {
      "heading": "Easier to Follow",
      "bullets": [
        "While the extraction agent re-checks records for the supervisor, chat shows what it is doing, such as reading a section or saving evidence, and when the re-check finishes.",
        "When the supervisor removes records, its progress line says so.",
        "Agent Studio chat can explain these changes, including why an item appears in the written answer instead of as a record."
      ]
    },
    {
      "heading": "Under the Hood",
      "bullets": [
        "A fast quick check now runs beside supervisor review and only records what it would have done, so we can measure whether it could speed up review later. It never changes a decision or a result."
      ]
    },
    {
      "heading": "Your Saved Work",
      "bullets": [
        "Your saved agents, flows, custom instructions and earlier results are unchanged. Custom agents saved from the General PDF Extraction template keep their own instructions.",
        "In a review round you request, the supervisor will not remove the records you asked to re-review. You decide those."
      ]
    }
  ]
};

export default entry;

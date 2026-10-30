import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-10-30-v0.11.1",
  "version": "0.11.1",
  "date": "October 30, 2026",
  "title": "Clearer chat actions and review progress",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10961",
  "sections": [
    {
      "heading": "Easier-to-use chat controls",
      "bullets": [
        "Chat actions now appear on a solid background when you hover over a reply, move keyboard focus to its controls, or select the small three-dot button.",
        "Copy and Review & Curate remain easy to reach. The last menu contains Provide Feedback, Open in Agent Studio, and Copy Debug ID when available, and closes when you click elsewhere.",
        "Copy buttons for your messages and evidence quotes now sit below the text, aligned to the right, so they no longer overlap it."
      ]
    },
    {
      "heading": "Clearer review progress",
      "bullets": [
        "The activity panel now shows when the supervisor is reviewing extracted findings and validation results, including when you continue an existing chat.",
        "Initial validation checks, active review, completed review, and stopped or failed work now have distinct status messages. Progress dots stop when the run is no longer active."
      ]
    }
  ]
};

export default entry;

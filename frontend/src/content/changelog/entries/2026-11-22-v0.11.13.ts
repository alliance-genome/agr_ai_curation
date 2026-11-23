import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-22-v0.11.13",
  "version": "0.11.13",
  "date": "November 22, 2026",
  "title": "Papers uploaded before late February can be read again",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/11040",
  "sections": [
    {
      "heading": "Fixed",
      "bullets": [
        "Some papers uploaded between December 2025 and February 2026 were fully processed but still marked as pending, so the AI stopped reading them in October. They are now marked as processed, and chat, flows and batches can use them again. Nothing else about these papers changes."
      ]
    },
    {
      "heading": "Under the Hood",
      "bullets": [
        "A maintenance tool that cleans up uploads that never started processing now leaves alone any paper that was processed, so it can never mark a complete paper as failed."
      ]
    },
    {
      "heading": "Your Saved Work",
      "bullets": [
        "Your saved agents, flows, custom instructions and earlier results are unchanged."
      ]
    }
  ]
};

export default entry;

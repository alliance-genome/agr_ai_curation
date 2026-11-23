import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-23-v0.11.14",
  "version": "0.11.14",
  "date": "November 23, 2026",
  "title": "Newer GPT-6.2 models, and fewer papers fail when the AI service is busy",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/11041",
  "sections": [
    {
      "heading": "Fixed",
      "bullets": [
        "Sometimes OpenAI, the AI service, says its servers are overloaded just after it has accepted a request, before any answer arrives. When that happened, the paper failed, even though AI Curation is meant to wait and try again when the service is busy. AI Curation now tries again by itself in this case too. If the service is still busy after a few tries, the paper stops with the same message as before, and you can run it again later.",
        "If a paper in a batch or flow failed this way, please run it again. Papers that already failed are not restarted automatically."
      ]
    },
    {
      "heading": "Changed",
      "bullets": [
        "Sol and Astra now run on their newer versions, GPT-6.2 Sol and GPT-6.2 Astra. Your saved agents and flows moved automatically with the same reasoning levels; you do not need to re-save anything. The model picker shows the new versions."
      ]
    },
    {
      "heading": "Your Saved Work",
      "bullets": [
        "Your instructions and earlier results are unchanged."
      ]
    }
  ]
};

export default entry;

import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-06-v0.11.3",
  "version": "0.11.3",
  "date": "November 6, 2026",
  "title": "Faster, lower-cost curation assistant",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10963",
  "sections": [
    {
      "heading": "Faster Results",
      "bullets": [
        "Validated chats and flows finish much sooner. In our tests, a validated phenotype chat that took about 12 minutes finished in under 5, and a 3-column reagent flow that took 9 to 13 minutes finished in 5 to 7.",
        "Papers with a long list of records (15 or more) are now extracted by several writers working at the same time: the assistant plans the list, the writers fill in the records, and a final check tidies the combined list. Extraction on those papers took about 40% less time in our tests.",
        "The supervisor now reviews each record from one compact summary of its values, quotes and validator answers, and saves its decisions in one step. Review of a validated phenotype chat dropped from about 8 minutes to under 2.",
        "Validators start sooner after extraction, receive shorter requests, and check an identical request only once. An Experimental Condition answer that picks out its parts is now accepted the first time, instead of being sent back for a rewrite.",
        "Exported TSV, CSV and JSON files no longer wait for an extra closing step after the file is saved.",
        "Each step starts a little faster: a separate size check that ran before every AI request is now skipped when the request clearly fits.",
        "Pages outside Agent Studio load faster, because agent details now load only in Agent Studio."
      ]
    },
    {
      "heading": "Easier to Follow",
      "bullets": [
        "During a long-list extraction, chat shows when the assistant is planning, writing and checking.",
        "While the supervisor reviews, chat shows a short line for each step it takes.",
        "When an answer is ready, the browser tab title says \"Answer ready\", so you can work in another tab and see when to come back.",
        "Agent Studio chat can explain these changes, including the parallel writers, what validators receive, and the review summary."
      ]
    },
    {
      "heading": "Fixes",
      "bullets": [
        "The server no longer freezes for everyone while a supervisor review saves its decisions after a follow-up on a large paper.",
        "When one review decision can't be saved, the error now names that entry, so the supervisor can correct it and keep its other notes instead of dropping them."
      ]
    },
    {
      "heading": "Under the Hood",
      "bullets": [
        "The supervisor and the file formatters now run on GPT-6.1 Sol, which answers faster at lower cost. Agent Studio chat, the feedback button and the Benchmark Assistant stay on GPT-6 Astra.",
        "Extraction tools send less repeated text back to the AI, which lowers the cost of every extraction."
      ]
    },
    {
      "heading": "Your Saved Work",
      "bullets": [
        "Your saved agents, flows, custom instructions and earlier results are unchanged.",
        "Saved flows get these improvements automatically. There is nothing to re-save."
      ]
    }
  ]
};

export default entry;

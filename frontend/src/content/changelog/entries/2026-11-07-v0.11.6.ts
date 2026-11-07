import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  "id": "2026-11-07-v0.11.6",
  "version": "0.11.6",
  "date": "November 7, 2026",
  "title": "Review saves that stick",
  "releaseUrl": "https://agr-jira.atlassian.net/projects/KANBAN/versions/10966",
  "sections": [
    {
      "heading": "Fixes",
      "bullets": [
        "When the supervisor removes a record after accepting validated entries such as alleles or genes, the removal now saves. Before, that save could fail, and review kept trying again until it gave up with records still pending.",
        "If removing a record would leave another record pointing at it, such as an allele mention still used by its paper association, the supervisor is told which record that is and removes them together.",
        "A validator answer the supervisor set aside comes back for a decision once there is something new to go on, such as a re-check of the paper.",
        "Flows that use a custom agent limited to one curator group, such as FlyBase genetic reagent agents, now give that agent its own output layout. Before, those agents could not read their own settings during a flow, and every flow run used the agent's latest saved settings instead of the version the flow was set up with.",
        "When you ask a new question in a chat whose earlier review was interrupted, the supervisor now finishes that earlier review against the question that produced those records. Before, it could judge them against your new question and remove records that correctly answered the earlier one.",
        "Reviews of papers whose terms cannot be matched to an ontology yet, such as some ZFIN phenotype terms, finish sooner. The supervisor no longer tries to mark those terms as unresolved, which they already are, and then has to save again."
      ]
    },
    {
      "heading": "Easier to Follow",
      "bullets": [
        "Validator lookup lines in chat now show the validator's answer, such as resolved or review required, instead of the result of an earlier search step.",
        "If a system error ever stops a save, review pauses with a plain message, nothing is changed, and the team is alerted automatically."
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

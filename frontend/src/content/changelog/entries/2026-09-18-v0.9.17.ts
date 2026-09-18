import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-09-18-v0.9.17',
  version: '0.9.17',
  date: 'September 18, 2026',
  title: 'AI Curation v0.9.17 — Extraction model, revision selection and clearer flow errors',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.9.17',
  sections: [
    {
      heading: 'Extraction model',
      bullets: [
        'Built-in extraction agents now use GPT-5.6 Sol with medium reasoning instead of GPT-6 Astra with low reasoning. New custom agents start with GPT-5.6 Sol, and your saved custom agents that used GPT-6 Astra were moved to GPT-5.6 Sol with medium reasoning. Their instructions and output structures did not change, and you can still choose another model in Agent Studio. Runs may take somewhat longer.',
      ],
    },
    {
      heading: 'Flow errors',
      bullets: [
        'When a flow cannot start, the message now names each affected step and the reason. For example, it says which steps need a PDF and asks you to load a paper from Documents before running the flow again.',
        'In Agent Studio, the assistant can now look up your recent runs of the open flow, including runs that stopped before they began. When you ask why a flow failed, it can answer from the recorded reason instead of asking for a Run ID.',
        'If the AI provider’s automatic safety check stops a flow because it flagged the request as possible biological risk, the message now says so. This check is run by the provider and can flag routine research content; please report the paper with the feedback button so we can follow up.',
      ],
    },
    {
      heading: 'Flow Builder',
      bullets: [
        'Choosing a different saved revision for a custom agent step in Flow Builder works again. In v0.9.16 this failed with “Extra inputs are not permitted”. If the application rejects a change, the message now names the setting involved.',
      ],
    },
    {
      heading: 'Paper searches',
      bullets: [
        'A paper search that stalls is now retried once automatically, so a brief delay is less likely to leave a flow step without search results.',
      ],
    },
  ],
};

export default entry;

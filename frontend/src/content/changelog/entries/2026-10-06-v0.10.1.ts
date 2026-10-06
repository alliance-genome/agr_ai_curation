import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-06-v0.10.1',
  version: '0.10.1',
  date: 'October 6, 2026',
  title: 'Use Your Saved Flows in the AI Curation Benchmark',
  releaseUrl: 'https://github.com/alliance-genome/agr_ai_curation/releases/tag/v0.10.1',
  sections: [
    {
      heading: 'Import Your Flows Into the Benchmark',
      bullets: [
        'In the AI Curation Benchmark, Flow fields now has an Import from AI Curation panel listing the saved flows you can run in AI Curation: your own and those shared with your project.',
        'Importing makes your own private copy in the benchmark, including the agent versions, prompts, and output fields the flow uses. Your chats, documents, and run history are not copied, and nothing changes in AI Curation.',
        'When you change a flow in AI Curation, the panel shows that a newer version is available. Choose Update to bring it in; the benchmark keeps the same flow, so its field lineup stays in place.',
        'A flow that cannot be imported says why, for example when one of its steps uses a model that is no longer available.',
      ],
    },
  ],
};

export default entry;

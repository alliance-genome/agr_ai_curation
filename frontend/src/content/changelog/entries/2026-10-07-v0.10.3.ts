import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-10-07-v0.10.3',
  version: '0.10.3',
  date: 'October 7, 2026',
  title: 'Help with Your Chat, Plus Reliability Fixes',
  releaseUrl: 'https://agr-jira.atlassian.net/projects/KANBAN/versions/10919',
  sections: [
    {
      heading: 'Find Help in Agent Studio',
      text: 'When you ask main chat about an extraction problem or changing your prompts, agents, or flows, you may see a reminder showing how to open the conversation in Agent Studio. Your chat continues normally. Choose “Dismiss for this chat” to hide further reminders for that conversation in the same browser.',
    },
    {
      heading: 'More Reliable Processing and Benchmarks',
      bullets: [
        'Document classification now retries temporary provider failures when it is safe to do so.',
        'Benchmark runs that reach their model-call limit stop with a clear explanation, while retaining usage from calls already underway.',
        'If your sign-in is verified but benchmark access has not been enabled, the benchmark service now explains how to request access.',
      ],
    },
    {
      heading: 'Your Saved Work',
      text: 'These changes apply to future work; they do not rerun extractions or change saved prompts, agents, flows, or results.',
    },
  ],
};

export default entry;

import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-04-11-v0.5.1',
  version: '0.5.1',
  date: 'April 11, 2026',
  title: 'Release Candidate Hardening',
  sections: [
    {
      heading: 'Release Safety',
      text: 'Supersedes the earlier 0.5.0 candidate after replacing skip-based test shortcuts with real runtime and test fixes.',
      bullets: [
        'Restored full backend unit coverage in the supported dev and isolated test environments without adding new skips.',
        'Rebuilt the dev test images so required runtime dependencies are present during collection and execution.',
      ],
    },
    {
      heading: 'Runtime & Registry Fixes',
      bullets: [
        'Fixed layered agent bundle loading so runtime overrides merge cleanly with package-owned bundles instead of dropping shipped agents.',
        'Added the repo and workspace mounts needed for package-aware runtime tests in dev.',
        'Updated backend runtime path handling so public runtime helpers import consistently in containerized environments.',
      ],
    },
    {
      heading: 'Test Hardening',
      bullets: [
        'Stabilized Agent Studio, curation workspace, flow executor, and supervisor tests against module reload and class identity issues.',
        'Aligned evidence SSE and logout integration expectations with the current backend contract.',
        'Verified green backend unit, contract, integration, frontend test, and frontend build checks on the dev server.',
      ],
    },
  ],
};

export default entry;

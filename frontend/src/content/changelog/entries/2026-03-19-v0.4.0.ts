import type { ChangelogEntry } from '../types';

const entry: ChangelogEntry = {
  id: '2026-03-19-v0.4.0',
  version: '0.4.0',
  date: 'March 19, 2026',
  title: 'Standalone Docker Installer & Modular Packages',
  sections: [
    {
      heading: 'Docker-Based Standalone Installer',
      text: 'A new guided installer makes it straightforward to deploy your own instance of the AI Curation platform using Docker.',
      bullets: [
        'Six interactive stages: preflight checks, environment setup, authentication, group configuration, PDF extraction service, and stack startup.',
        'Resume from any stage if something goes wrong.',
        'Pin to a specific release version for reproducible installs.',
        'Auto-detects CPU, RAM, and GPU availability for optimal PDF extraction configuration.',
        'Progress breadcrumbs and clear guidance at each step.',
      ],
    },
    {
      heading: 'Modular Package Architecture',
      text: 'The backend is now organized into installable packages, making it easier to customize and extend.',
      bullets: [
        'Alliance Core package: minimal runtime with supervisor chat, task input, and base tools.',
        'Alliance Defaults package: full AGR specialist agents and domain tool catalog.',
        'Choose core-only or core + alliance during install, and add the alliance package later without reinstalling.',
        'Each package runs in its own isolated Python environment.',
      ],
    },
    {
      heading: 'Published Docker Images',
      bullets: [
        'Backend, frontend, and trace review images are published to AWS ECR Public with each release.',
        'Release assets include bundled package tarballs and a pinned environment template for reproducible deployments.',
      ],
    },
    {
      heading: 'Core-Only Install Improvements',
      bullets: [
        'The platform starts cleanly with only the core package installed.',
        'Connections health no longer shows warnings for optional services that are not configured.',
        'Chat UI suppresses alerts for services that are intentionally absent in standalone installs.',
      ],
    },
    {
      heading: 'PDF Extraction Service',
      bullets: [
        'Health checks now correctly recognize healthy extraction services on standalone installs.',
        'Errors only appear when the service is actually degraded, not when optional monitoring endpoints are missing.',
        'Installer configures Docker-compatible networking automatically.',
      ],
    },
    {
      heading: 'Frontend Fixes',
      bullets: [
        'Fixed a blank white page caused by a production build issue.',
        'Fixed an authentication redirect loop caused by missing API proxy rules.',
      ],
    },
    {
      heading: 'Other Changes',
      bullets: [
        'Renamed MOD terminology to Group across the entire platform.',
        'PDF highlight tracking improvements.',
        'Flow results are now available in chat context for follow-up questions.',
        'Installer and build regression suites now run in CI.',
      ],
    },
  ],
};

export default entry;

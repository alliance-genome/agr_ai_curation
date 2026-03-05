import type { ChangelogEntry } from './types';

const entryModules = import.meta.glob<{ default: ChangelogEntry }>('./entries/*.ts', { eager: true });

export const CHANGELOG_ENTRIES: ChangelogEntry[] = Object.values(entryModules)
  .map((module) => module.default)
  .sort((a, b) => b.id.localeCompare(a.id));

export const LATEST_CHANGELOG_ENTRY: ChangelogEntry | undefined = CHANGELOG_ENTRIES[0];

export type { ChangelogEntry, ChangelogSection } from './types';

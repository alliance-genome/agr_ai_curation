import type { ChangelogEntry } from './types';

const entryModules = import.meta.glob<{ default: ChangelogEntry }>('./entries/*.ts', { eager: true });
const POPUP_CHANGELOG_ENTRY_ID = '2026-04-11-v0.5.0';

export const CHANGELOG_ENTRIES: ChangelogEntry[] = Object.values(entryModules)
  .map((module) => module.default)
  .sort((a, b) => b.id.localeCompare(a.id));

export const LATEST_CHANGELOG_ENTRY: ChangelogEntry | undefined = CHANGELOG_ENTRIES[0];
export const POPUP_CHANGELOG_ENTRY: ChangelogEntry | undefined =
  CHANGELOG_ENTRIES.find((entry) => entry.id === POPUP_CHANGELOG_ENTRY_ID) ?? LATEST_CHANGELOG_ENTRY;

export type { ChangelogEntry, ChangelogSection } from './types';

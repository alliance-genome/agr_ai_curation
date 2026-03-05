export interface ChangelogSection {
  heading: string;
  bullets?: string[];
  text?: string;
}

export interface ChangelogEntry {
  id: string;
  version: string;
  date: string;
  title: string;
  sections: ChangelogSection[];
}

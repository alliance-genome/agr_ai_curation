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
  // Optional Jira/KANBAN release link so curators can open the release and see
  // its tickets. Rendered as a "View release tickets" link when present.
  releaseUrl?: string;
  sections: ChangelogSection[];
}

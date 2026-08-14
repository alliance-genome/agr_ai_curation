import type { DocumentSourceProvenance } from '../services/weaviate';

export const documentSourceProviderLabel = (
  source?: DocumentSourceProvenance | null
): string => {
  if (!source?.provider) {
    return 'Local PDF';
  }
  return source.providerMetadata?.displayLabel || source.provider;
};

const externalIdentifierLabel = (
  externalIds: Record<string, string | string[]> | null | undefined,
  requestedKey?: string
): string | null => {
  if (!externalIds) {
    return null;
  }

  const entry = requestedKey
    ? Object.entries(externalIds).find(
        ([candidate]) => candidate.toLowerCase() === requestedKey.toLowerCase()
      )
    : Object.entries(externalIds)[0];
  if (!entry) {
    return null;
  }

  const [label, rawValue] = entry;
  const value = Array.isArray(rawValue) ? rawValue[0] : rawValue;
  return value ? `${label.toUpperCase()}: ${value}` : null;
};

const configuredReferenceLabel = (
  source: DocumentSourceProvenance,
  selector: string
): string | null => {
  if (selector.startsWith('external_ids.')) {
    return externalIdentifierLabel(source.externalIds, selector.slice('external_ids.'.length));
  }

  const fields: Record<string, string | null | undefined> = {
    reference_curie: source.referenceCurie,
    reference_id: source.referenceId,
    source_md5: source.sourceMd5,
  };
  return fields[selector] || null;
};

export const documentSourceReferenceLabel = (
  source?: DocumentSourceProvenance | null
): string => {
  if (!source) {
    return 'Uploaded PDF';
  }

  const priority = source.providerMetadata?.referenceLabelPriority;
  if (!priority?.length) {
    return 'Provider presentation metadata missing';
  }

  for (const selector of priority) {
    const label = configuredReferenceLabel(source, selector);
    if (label) {
      return label;
    }
  }

  return 'Provider reference unavailable';
};

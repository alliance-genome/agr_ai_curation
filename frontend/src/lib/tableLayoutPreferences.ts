import {
  safeGetJson,
  safeRemoveItem,
  safeSetJson,
} from './browserStorage';

const TABLE_LAYOUT_PREFERENCE_VERSION = 1;
const TABLE_LAYOUT_PREFERENCE_PREFIX = 'table-layout';

export interface TableLayoutPreferenceScope {
  tableId: string;
  userId: string;
}

export interface TableLayoutPreference {
  version: typeof TABLE_LAYOUT_PREFERENCE_VERSION;
  columnOrder: string[];
  columnVisibility: Record<string, boolean>;
}

export const buildTableLayoutPreferenceKey = ({
  tableId,
  userId,
}: TableLayoutPreferenceScope): string => (
  `${TABLE_LAYOUT_PREFERENCE_PREFIX}:v${TABLE_LAYOUT_PREFERENCE_VERSION}`
  + `:${encodeURIComponent(userId)}:${encodeURIComponent(tableId)}`
);

const currentColumnsOnly = (
  values: unknown,
  currentColumnFields: readonly string[],
): string[] => {
  if (!Array.isArray(values)) {
    return [];
  }

  const currentFields = new Set(currentColumnFields);
  return values.filter(
    (value, index): value is string => (
      typeof value === 'string'
      && currentFields.has(value)
      && values.indexOf(value) === index
    ),
  );
};

export const defaultTableLayoutPreference = (
  currentColumnFields: readonly string[],
): TableLayoutPreference => ({
  version: TABLE_LAYOUT_PREFERENCE_VERSION,
  columnOrder: [...currentColumnFields],
  columnVisibility: {},
});

export const sanitizeTableLayoutPreference = (
  stored: unknown,
  currentColumnFields: readonly string[],
): TableLayoutPreference => {
  const defaults = defaultTableLayoutPreference(currentColumnFields);
  if (
    !stored
    || typeof stored !== 'object'
    || !('version' in stored)
    || stored.version !== TABLE_LAYOUT_PREFERENCE_VERSION
  ) {
    return defaults;
  }

  const candidate = stored as Partial<TableLayoutPreference>;
  const storedOrder = currentColumnsOnly(candidate.columnOrder, currentColumnFields);
  const storedFields = new Set(storedOrder);
  const columnOrder = [
    ...storedOrder,
    ...currentColumnFields.filter((field) => !storedFields.has(field)),
  ];
  const currentFields = new Set(currentColumnFields);
  const columnVisibility = candidate.columnVisibility
    && typeof candidate.columnVisibility === 'object'
    && !Array.isArray(candidate.columnVisibility)
    ? Object.fromEntries(
      Object.entries(candidate.columnVisibility).filter(
        ([field, visible]) => currentFields.has(field) && typeof visible === 'boolean',
      ),
    )
    : {};

  return {
    version: TABLE_LAYOUT_PREFERENCE_VERSION,
    columnOrder,
    columnVisibility,
  };
};

export const loadTableLayoutPreference = (
  scope: TableLayoutPreferenceScope | undefined,
  currentColumnFields: readonly string[],
): TableLayoutPreference => {
  if (!scope) {
    return defaultTableLayoutPreference(currentColumnFields);
  }

  const key = buildTableLayoutPreferenceKey(scope);
  const result = safeGetJson<unknown>(() => window.localStorage, key, {
    owner: 'preferences',
    quiet: true,
  });

  return sanitizeTableLayoutPreference(result.ok ? result.value : null, currentColumnFields);
};

export const saveTableLayoutPreference = (
  scope: TableLayoutPreferenceScope | undefined,
  preference: TableLayoutPreference,
): void => {
  if (!scope) {
    return;
  }

  safeSetJson(
    () => window.localStorage,
    buildTableLayoutPreferenceKey(scope),
    preference,
    { owner: 'preferences' },
  );
};

export const removeTableLayoutPreference = (
  scope: TableLayoutPreferenceScope | undefined,
): void => {
  if (!scope) {
    return;
  }

  safeRemoveItem(
    () => window.localStorage,
    buildTableLayoutPreferenceKey(scope),
    { owner: 'preferences' },
  );
};

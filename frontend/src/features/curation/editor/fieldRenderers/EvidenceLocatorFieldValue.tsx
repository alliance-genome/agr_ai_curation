import { Typography } from '@mui/material'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export default function EvidenceLocatorFieldValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') {
    return null
  }

  if (!isRecord(value)) {
    return (
      <Typography variant="body2" sx={{
        color: "text.secondary"
      }}>
        {String(value)}
      </Typography>
    );
  }

  const parts = [
    typeof value.page === 'number' || typeof value.page === 'string' ? `p.${value.page}` : null,
    typeof value.section === 'string' ? value.section : null,
    typeof value.subsection === 'string' ? value.subsection : null,
    typeof value.figure === 'string' ? value.figure : null,
  ].filter((part): part is string => Boolean(part))

  return (
    <Typography variant="body2" sx={{
      color: "text.secondary"
    }}>
      {parts.length > 0 ? parts.join(' · ') : JSON.stringify(value)}
    </Typography>
  );
}

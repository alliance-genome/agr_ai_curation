#!/usr/bin/env bash
# Best-effort prefix refresh on container start.
# - Uses CURATION_DB_URL if set.
# - Runs extract_identifier_prefixes.py with standard queries.
# - Logs warnings on failure but never blocks startup.
set -euo pipefail
echo '[prefix-refresh] Starting prefix refresh script'
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${BASE_DIR}/scripts/extract_identifier_prefixes.py"
OUTFILE="${BASE_DIR}/backend/config/identifier_prefixes.json"
log() {
  echo "[prefix-refresh] $*"
}
DB_URL="${CURATION_DB_URL:-${DATABASE_URL:-}}"
REDACTED_DB_URL="$(echo "${DB_URL:-}" | sed 's#//[^:]*:[^@]*@#//***:***@#')"
if [[ -z "$DB_URL" ]]; then
  log "No CURATION_DB_URL/DATABASE_URL set; skipping prefix refresh."
  exit 0
fi
log "Attempting prefix refresh using ${REDACTED_DB_URL:-<missing>} -> ${OUTFILE}"
set +e
"${SCRIPT}" \
  --database-url "$DB_URL" \
  --outfile "$OUTFILE" \
  --query "SELECT DISTINCT split_part(referencedcurie, ':', 1) AS prefix FROM crossreference WHERE referencedcurie LIKE '%:%' AND referencedcurie IS NOT NULL;" \
  --query "SELECT DISTINCT split_part(curie, ':', 1) AS prefix FROM ontologyterm WHERE curie LIKE '%:%' AND curie IS NOT NULL;" \
  --query "SELECT DISTINCT split_part(primaryexternalid, ':', 1) AS prefix FROM biologicalentity WHERE primaryexternalid LIKE '%:%' AND primaryexternalid IS NOT NULL;"
rc=$?
set -e
if [[ $rc -ne 0 ]]; then
  log "WARNING: Prefix refresh failed (rc=$rc); leaving existing prefixes in place."
else
  log "Prefix refresh completed."
fi
exit 0

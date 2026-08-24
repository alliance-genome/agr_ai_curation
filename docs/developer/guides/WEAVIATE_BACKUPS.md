# Weaviate Native Backups

AI Curation enables Weaviate's `backup-filesystem` module in both development
and standalone production Compose. Backups are written to the host directory
configured by `WEAVIATE_BACKUP_HOST_DIR`, separately from the live
`WEAVIATE_DATA_HOST_DIR`.

## Development Compose: create and inspect a backup

The development Compose stack publishes Weaviate on the host and can allow
anonymous access. Use a unique lowercase backup ID, then poll the same backup
until the asynchronous request reaches `SUCCESS` or `FAILED`.

```bash
backup_id="manual-$(date -u +%Y%m%d-%H%M%S)"
curl -fsS -X POST -H "Content-Type: application/json" \
  -d "{\"id\":\"${backup_id}\"}" \
  http://localhost:8080/v1/backups/filesystem

while :; do
  response="$(curl -fsS \
    "http://localhost:8080/v1/backups/filesystem/${backup_id}")" || exit
  printf '%s\n' "$response"
  if grep -Eq '"status"[[:space:]]*:[[:space:]]*"(SUCCESS|FAILED)"' \
      <<<"$response"; then
    break
  fi
  sleep 2
done
```

## Standalone production: create and inspect a backup

Production does not publish Weaviate's data ports and requires API-key
authentication. From the repository directory on the standalone host, run the
request inside the existing `backend` container. The script reads
`WEAVIATE_API_KEY` from that container's environment and never places the key
on the host command line or prints it.

```bash
backup_id="manual-$(date -u +%Y%m%d-%H%M%S)"
docker compose --env-file ~/.agr_ai_curation/.env \
  -f docker-compose.production.yml exec -T \
  -e BACKUP_ID="$backup_id" backend python - <<'PY'
import json
import os
import time
import urllib.request

backup_id = os.environ["BACKUP_ID"]
base_url = "http://weaviate:8080/v1/backups/filesystem"
authorization = {"Authorization": f"Bearer {os.environ['WEAVIATE_API_KEY']}"}

create_request = urllib.request.Request(
    base_url,
    data=json.dumps({"id": backup_id}).encode(),
    headers={**authorization, "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(create_request) as response:
    print(json.dumps(json.load(response)))

status_url = f"{base_url}/{backup_id}"
while True:
    status_request = urllib.request.Request(status_url, headers=authorization)
    with urllib.request.urlopen(status_request) as response:
        result = json.load(response)
    print(json.dumps(result))
    status = str(result.get("status", "")).upper()
    if status in {"SUCCESS", "FAILED"}:
        raise SystemExit(0 if status == "SUCCESS" else 1)
    time.sleep(2)
PY
```

The API creates a consistent online snapshot, including collection schemas,
tenant metadata, objects, and vector indexes. Record the backup ID, status,
timestamps, Weaviate version, collection list, and host-side size.

## Restore validation requirements

Never test a restore against the live data directory. Use a separate Weaviate
container or host with an empty persistence directory and no route to the live
cluster. A valid target must satisfy all of these conditions:

- the target has the same node count and `CLUSTER_HOSTNAME` values as the
  source backup;
- every module referenced by the saved collection schemas is enabled, in
  addition to `backup-filesystem`;
- the target contains none of the collections being restored;
- the validation copy of the backup directory is writable because Weaviate
  writes `restore_config.json` beside the backup during restoration.

For a same-host rehearsal, copy the selected backup to a disposable directory
and mount that copy read/write. Keep the authoritative backup untouched. After
the restore reports `SUCCESS`, compare normalized schemas, tenant sets,
per-tenant object counts, object-ID digests, and at least one vector-neighborhood
query before deleting only the isolated target.

## Durability and retention

The filesystem backend is appropriate for local development and for a
single-node deployment rollback snapshot. A backup on the same EC2/EBS storage
does not protect against instance or volume loss. Copy successful backups to a
separate durable system with a documented retention policy, or configure
Weaviate's `backup-s3` module for disaster recovery. Do not place backups under
the live data directory, and do not recursively include native backups in a
filesystem archive of that directory.

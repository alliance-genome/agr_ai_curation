# Weaviate Native Backups

AI Curation enables Weaviate's `backup-filesystem` module in both development
and standalone production Compose. Backups are written to the host directory
configured by `WEAVIATE_BACKUP_HOST_DIR`, separately from the live
`WEAVIATE_DATA_HOST_DIR`.

## Create and inspect a backup

Use a unique lowercase backup ID. The request is asynchronous, so poll until
the terminal status is `SUCCESS` or `FAILED`.

```bash
backup_id="manual-$(date -u +%Y%m%d-%H%M%S)"
curl -fsS -X POST -H "Content-Type: application/json" \
  -d "{\"id\":\"${backup_id}\"}" \
  http://localhost:8080/v1/backups/filesystem
curl -fsS \
  "http://localhost:8080/v1/backups/filesystem/${backup_id}"
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

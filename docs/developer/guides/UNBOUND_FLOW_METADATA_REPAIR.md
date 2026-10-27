# Generated metadata execution-group repair

Release migration `f4a8b0c2d3e5` removes execution groups that an older flow
producer incorrectly generated from descriptive, unbound validation attachments.
The runtime producer now excludes those attachments from execution groups. The
attachments themselves remain available for display and retain their saved flags.

Use the same Compose files, project and private configuration as the target deployment
when running these commands. The one-off script is mounted because it is not
included in the production image.

Before upgrading, drain flow and review workers and take the normal database
backup. From the candidate backend, retain private JSON reports from:

```bash
docker compose run --rm --no-deps -T --entrypoint python \
  -v "$PWD/scripts/one_off:/app/scripts/one_off:ro" \
  backend /app/scripts/one_off/inspect_unbound_metadata_groups.py
docker compose run --rm --no-deps -T --entrypoint python \
  -v "$PWD/scripts/one_off:/app/scripts/one_off:ro" \
  backend /app/scripts/one_off/inspect_unbound_metadata_groups.py --rehearse
```

The first command is read-only. The second performs the actual repair inside a
transaction and rolls it back. Neither commits. The report covers active and
inactive flows and includes before/after definition hashes, removed groups and
unmatched groups. It may contain private configuration; do not publish it.

`alembic upgrade head` applies the repair transactionally. It requires the normal
flow-update audit trigger and takes a table lock blocking concurrent saves, which retains complete old/new records. Only changed
flows receive a new `updated_at`. Group matching requires exact old-producer
output and a unique matching attachment in the same node. Real validator
selections, instructions, edges, agent revision pins and attachments are retained.
Unknown malformed groups remain unchanged and must be investigated; they are not
silently accepted by the dispatcher. Repeat the inventory after upgrading: no
matched groups should remain. Reapplication makes no additional updates.

Reports include definition hashes and the actual before/after saved-flow revisions
used by benchmark pins and flow import/export. Both change when groups are removed.
Exported `source_version` changes; repaired benchmark import copies no longer
match their recorded revision and may be imported as a new version. Inventory
`benchmark_flow_imports` as well as frozen jobs before choosing fresh captures. Do not resume old flow
claims or reuse a stale expected revision. Create new runs and fresh benchmark
captures after the repair. Historical benchmark captures remain immutable; this
migration does not rewrite them or make their old execution configuration valid.
Inventory required benchmark consumers separately before release signoff.

Downgrade is intentionally unsupported. Use the retained audit records and backup
for a separately reviewed recovery rather than restoring invalid groups during
normal operation.

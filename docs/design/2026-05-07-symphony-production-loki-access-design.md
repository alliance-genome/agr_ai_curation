# Symphony Production Loki Access Design

Date: 2026-05-07
Status: proposed design; GPT-5.5 high review incorporated
Audience: AI Curation / Symphony implementation agents

## Summary

Symphony agents need flexible, read-only access to production AI Curation logs while working Linear tickets. Production now ships Docker logs into local Loki through Promtail, and the backend `/api/logs` endpoint can query Loki for simple service-log reads. For debugging curator feedback and production bugs, agents also need the more exploratory workflow we use manually: inspect labels, list services, query specific time windows, search trace IDs/session IDs/feedback IDs, and follow a trail across `backend`, `trace_review_backend`, `langfuse`, `weaviate`, and `promtail`.

This design proposes a host-owned production Loki tunnel/proxy managed on Chris's workstation and surfaced in the Symphony Elixir UI as a small status/repair control. Agents inside the `symphony-main` Incus VM get broad read-only Loki query access, while production credentials and tunnel lifecycle remain on the host.

This is intentionally a personal/local implementation for Chris's workstation and Symphony VM. It does not need enterprise-level multi-user controls. It does need to be boring, constrained, recoverable, and clear enough for naive agents to use safely.

## Current Context

Production facts as of 2026-05-07:

- Production EC2 instance: `ai-curation-new` / `i-080ce45010da7c4ef`
- Production private IP: `172.31.29.141`
- Production app directory: `/home/ubuntu/agr_ai_curation`
- Production Loki is running and responds on the production host.
- Production Promtail is running and ingesting Docker logs into Loki.
- Promtail discovery is now Compose-project-name agnostic.
- Promtail positions are now persisted in a Docker named volume.
- The backend `/api/logs/backend?lines=100&since=5` route returns nonempty logs.
- Symphony runs inside the Incus VM named `symphony-main`.
- Symphony agents should not receive broad production shell, DB, or VPN access for log investigation.

Relevant repo files:

- `promtail-config.yml`
- `docker-compose.yml`
- `docker-compose.production.yml`
- `backend/src/api/logs.py`
- `backend/src/lib/loki_client.py`
- `scripts/utilities/symphony_local_db_tunnel_start.sh`
- `scripts/utilities/symphony_curation_db_psql.sh`
- `.symphony/WORKFLOW.md`
- `.symphony/elixir/`

## Problem

Curator feedback and production bug reports often need answers like:

- What did the backend log around this feedback ID?
- Did a session produce tool calls, trace snapshot failures, or exceptions?
- Did `record_evidence`, PDF loading, TraceReview, Langfuse, or feedback snapshot code run?
- Was an error isolated or repeated?
- Which services have relevant logs in the same time window?
- Did Promtail/Loki ingest logs correctly?

The current `/api/logs/{service}` route is useful, but it is intentionally narrow. It is less convenient for flexible LogQL exploration across labels, services, strings, and exact time ranges.

We want Symphony agents to have enough read-only log access to investigate effectively without giving them production mutation power.

## Non-Goals

Do not build these as part of this design:

- Production database access.
- Production SSH shell access for agents.
- VPN access inside the Incus VM.
- A general production admin dashboard.
- A generic host command runner from the Symphony UI.
- A full raw-log browser in the Symphony UI.
- Automatic posting of raw logs into Linear or Jira.
- Loki write access.
- Docker, app, database, filesystem, or service-control access on production.

## Recommended Shape

Use a host-owned tunnel/proxy:

```text
Chris workstation host
  systemd service or user service
  fixed tunnel/proxy command
        |
        v
  VM-reachable local endpoint, e.g. http://<host-incus-ip>:43100
        |
        v
  production Loki at http://127.0.0.1:3100 on prod EC2

symphony-main Incus VM
  Codex/Symphony issue workspaces
        |
        v
  curl / helper scripts against http://<host-incus-ip>:43100
```

Important boundary:

- Production credentials stay on the host.
- The VM receives an HTTP Loki query endpoint.
- The VM does not receive the production SSH PEM.
- The VM does not receive broad AWS/SSM production authority for this feature.
- The endpoint is for read-only Loki query APIs.

## Why Host-Owned

A VM-owned SSM tunnel would work, but it requires AWS credentials inside the VM. A host-owned tunnel is a better fit for Chris's local workflow:

- The host already has the production SSH/VPN/AWS unlock workflows.
- The Incus VM can remain less privileged.
- Symphony agents can use a stable HTTP endpoint without managing credentials.
- The Symphony UI can show status and request repair through a tiny fixed host-side control surface.
- Repair can be password-less because it only restarts a fixed read-only log tunnel service.

## Security Model

Agents may query Loki freely through read-only endpoints.

Allowed Loki capabilities:

- List labels.
- List service label values.
- Query series.
- Run instant queries.
- Run range queries.
- Use arbitrary LogQL within those read endpoints.

Agents must not be able to:

- SSH to production.
- Run commands on production.
- Restart production services.
- Connect to production PostgreSQL, Weaviate, Redis, MinIO, Docker, or the filesystem.
- Push logs into Loki.
- Change Loki/Promtail config.

The primary risks are:

- Accidentally huge Loki queries.
- Sensitive log snippets pasted into tickets.
- Binding the tunnel too broadly.
- A Symphony UI repair button that accidentally becomes a generic host command runner.

Guardrails:

- Bind the VM-facing endpoint only to a host address reachable by `symphony-main`, not public/LAN-wide `0.0.0.0`.
- Use a read-only proxy for the VM-facing endpoint. A raw Loki port forward is not technically read-only because Loki exposes write endpoints such as `POST /loki/api/v1/push`.
- Provide bounded helper defaults even though agents may still use flexible LogQL through the read-only proxy.
- Log status/repair actions in Symphony logs.
- Tell agents to summarize logs and include only short, relevant snippets in Linear/Jira.

## Transport Options

### Option A: SSH Local Port Forward

The host runs:

```bash
ssh -i ~/pem_certs/AGR-ssl3.pem \
  -N \
  -L 127.0.0.1:43101:127.0.0.1:3100 \
  ubuntu@172.31.29.141
```

Pros:

- Simple.
- Uses current production SSH path.
- Easy to inspect with `ss`, `systemctl`, and `curl`.

Cons:

- Raw tunnel does not filter HTTP method/path.
- Requires the production SSH PEM to be unlocked.
- Must be very careful about bind address.

Do not expose this raw tunnel directly to the VM as the normal agent endpoint. Use it as the private upstream behind the read-only proxy:

```text
127.0.0.1:43101 -> prod 127.0.0.1:3100
```

### Option B: Host SSM Port Forward

The host runs:

```bash
aws ssm start-session \
  --profile ctabone \
  --region us-east-1 \
  --target i-080ce45010da7c4ef \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3100"],"localPortNumber":["43100"]}'
```

Pros:

- No SSH network path required.
- IAM/session controlled.
- No PEM.

Cons:

- Host AWS credentials must be unlocked.
- Binding for VM access may still need a second local proxy.
- SSM process lifecycle is a little more awkward than SSH.

This is a future fallback. Do not include `--transport auto` in v1 unless the implementation explicitly handles:

```text
localhost SSM tunnel -> host read-only proxy -> VM-facing endpoint
```

### Option C: Tunnel Plus Read-Only HTTP Proxy

The host runs a private raw tunnel on localhost:

```text
127.0.0.1:43101 -> prod 127.0.0.1:3100
```

Then a tiny local proxy exposes:

```text
<host-incus-ip>:43100 -> validate request -> 127.0.0.1:43101
```

The proxy only allows read Loki endpoints.

Pros:

- Best safety shape.
- Raw Loki is not directly exposed to the VM-facing address.
- Easy to reject POST and unknown paths.

Cons:

- More code.
- More tests.
- More process supervision.

This is the recommended v1. It is still small enough for Chris's personal setup, but it makes the access truly read-only instead of merely policy-read-only.

## Recommended First Implementation

Build the feature in two small waves, but make the read-only proxy part of wave 1.

Wave 1:

- Host-owned SSH tunnel bound to host localhost only.
- Host-owned read-only Loki proxy bound only to the Incus host address.
- VM-readable endpoint config containing only `LOKI_URL`.
- CLI helpers for host lifecycle and VM-side status/query.
- Documentation and workflow instructions.
- No Symphony UI yet, unless status-only is trivial.

Wave 2:

- Add Symphony UI status/repair/stop controls.
- Add host control shim if needed for UI to restart the host-owned service.
- Add SSM transport fallback if SSH proves annoying.

This keeps the first implementation small and useful while avoiding the misleading claim that a raw Loki tunnel is read-only.

## Host/VM Contract

This boundary must be explicit because host-owned tunnel state is not directly visible inside the VM.

### Host Responsibilities

The host owns:

- Production SSH/SSM credentials.
- Raw tunnel process.
- Read-only proxy process.
- systemd service state.
- tunnel/proxy PID files and logs.
- repair/start/stop actions.

Host-side lifecycle scripts may inspect host PIDs and host state.

### VM Responsibilities

The VM owns:

- Reading a non-secret endpoint config file.
- Probing `GET /ready` through the configured endpoint.
- Running Loki read queries through the configured endpoint.
- Reporting clear errors when the endpoint is offline.

VM-side helpers must not assume they can read host PID files, host `${XDG_RUNTIME_DIR}`, or host systemd state.

### Endpoint Config

Create a non-secret endpoint file that can be copied or generated into both the host repo checkout and the VM repo checkout:

```text
.symphony/prod_loki_endpoint.env
```

Contents:

```bash
LOKI_URL=http://<host-incus-ip>:43100
LOKI_TUNNEL_OWNER=host
LOKI_TUNNEL_MODE=readonly-proxy
```

This file contains no credentials. It is safe for agents to source or print. It should not contain PIDs, SSH commands, AWS profile names, secrets, or production config values beyond the endpoint URL.

VM-side status behavior:

1. Load `LOKI_URL` from `.symphony/prod_loki_endpoint.env`.
2. Probe `GET $LOKI_URL/ready`.
3. Optionally probe labels with `GET $LOKI_URL/loki/api/v1/labels`.
4. If probing fails, print a repair instruction such as: `Production Loki tunnel is offline; use Symphony UI Repair or run the host-side tunnel repair helper on Chris's workstation.`

## Determining The Host Incus Address

A naive implementation must not guess the bind address.

Use a deterministic discovery and verification sequence.

On the host, inspect the VM network:

```bash
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" list symphony-main
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec symphony-main -- ip route
```

Inside the VM, the default gateway for the Incus network is usually the host-side address reachable from the VM:

```bash
ip route | awk '/^default/ {print $3; exit}'
```

From the host, bind the read-only proxy to that host-side Incus address and port `43100`.

Required verification:

```bash
ss -ltnp | grep ':43100'
```

The listener must look like:

```text
<host-incus-ip>:43100
```

It must not look like:

```text
0.0.0.0:43100
127.0.0.1:43100       # not reachable from VM unless another proxy exists
192.168.x.x:43100     # LAN-facing, avoid for v1
```

From the VM:

```bash
curl -fsS http://<host-incus-ip>:43100/ready
curl -fsS http://<host-incus-ip>:43100/loki/api/v1/label/service/values | jq .
```

## Files To Add

Tracked host lifecycle helpers:

```text
scripts/lib/prod_loki_tunnel_common.sh
scripts/utilities/symphony_prod_loki_readonly_proxy.py
scripts/utilities/symphony_prod_loki_host_start.sh
scripts/utilities/symphony_prod_loki_host_status.sh
scripts/utilities/symphony_prod_loki_host_stop.sh
scripts/utilities/symphony_prod_loki_host_repair.sh
```

Tracked VM/agent helpers:

```text
scripts/utilities/symphony_prod_loki_status.sh
scripts/utilities/symphony_prod_loki_query.sh
```

The host lifecycle helpers manage processes. The VM/agent helpers only read `.symphony/prod_loki_endpoint.env` and make HTTP requests to Loki through the read-only proxy.

Tests:

```text
scripts/tests/test_prod_loki_tunnel_common.sh
scripts/tests/test_symphony_prod_loki_readonly_proxy.py
scripts/tests/test_symphony_prod_loki_query.sh
```

Docs/instructions:

```text
scripts/README.md
.symphony/WORKFLOW.md
```

Optional UI/runtime:

```text
.symphony/elixir/
```

## Helper Design

### `scripts/lib/prod_loki_tunnel_common.sh`

Purpose: shared functions for tunnel lifecycle and query helpers.

Responsibilities:

- Resolve repo root.
- Resolve state directory.
- Resolve or validate bind IP.
- Validate bind port.
- Check host prerequisites: `ssh`, `curl`, `jq`, `ss`; optionally `aws` and `session-manager-plugin` in later SSM work.
- Detect running PIDs from state.
- Write and read tunnel state.
- Print non-secret shell environment values.
- Build endpoint URL.
- Write `.symphony/prod_loki_endpoint.env`.

Suggested defaults:

```bash
SYMPHONY_PROD_LOKI_PORT="${SYMPHONY_PROD_LOKI_PORT:-43100}"
SYMPHONY_PROD_LOKI_REMOTE_HOST="${SYMPHONY_PROD_LOKI_REMOTE_HOST:-172.31.29.141}"
SYMPHONY_PROD_LOKI_REMOTE_PORT="${SYMPHONY_PROD_LOKI_REMOTE_PORT:-3100}"
SYMPHONY_PROD_LOKI_INSTANCE_ID="${SYMPHONY_PROD_LOKI_INSTANCE_ID:-i-080ce45010da7c4ef}"
```

Suggested state directory:

```bash
${XDG_RUNTIME_DIR:-/tmp}/agr_ai_curation_symphony_prod_loki_tunnel
```

Suggested state fields:

```bash
BIND_IP=...
BIND_PORT=43100
LOKI_URL=http://<bind-ip>:43100
TRANSPORT=ssh
TUNNEL_PID=...
PROXY_PID=...
REMOTE_HOST=172.31.29.141
REMOTE_PORT=3100
STARTED_AT=...
LOG_FILE=...
```

Do not write secrets to state.

### `symphony_prod_loki_host_start.sh`

Purpose: start or reuse the host-owned raw SSH tunnel plus read-only proxy.

Arguments:

```text
--transport ssh
--bind-ip <ip>
--port <port>
--remote-host <ip>
--foreground
--print-env
```

Behavior:

1. If the tunnel and proxy are already healthy, print status and exit 0.
2. Validate prerequisites.
3. Validate bind IP is not `0.0.0.0` unless explicitly allowed.
4. Start raw SSH tunnel on host localhost, for example `127.0.0.1:43101 -> prod 127.0.0.1:3100`.
5. Start read-only proxy on the VM-facing bind IP, for example `<host-incus-ip>:43100 -> 127.0.0.1:43101`.
6. Wait for listener.
7. Probe `GET /ready` through the VM-facing proxy.
8. Write host state.
9. Write `.symphony/prod_loki_endpoint.env` with non-secret `LOKI_URL`.
10. Print `LOKI_URL` when requested.

For `--foreground`, do not daemonize. This mode is useful for systemd.

For detached mode, write a PID file and log file.

Recommended SSH options:

```bash
ssh \
  -i ~/pem_certs/AGR-ssl3.pem \
  -N \
  -o BatchMode=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:43101:127.0.0.1:3100 \
  ubuntu@172.31.29.141
```

If `~/pem_certs/AGR-ssl3.pem` is missing or locked, the helper should say:

```text
Production SSH key appears locked or unavailable. Ask Chris to run unlock-ssl, then retry.
```

If the PEM exists but SSH still fails with `Permission denied (publickey)`, follow the host auth guidance and ask Chris to run `unlock-ssh`, then retry once.

Do not suggest `ssh-keygen`, `aws configure`, or rewriting credentials.

### `symphony_prod_loki_host_status.sh`

Purpose: report host-side lifecycle health for Chris and the optional UI control shim.

Arguments:

```text
--json
--shell-env
--quiet
```

Text output:

```text
status=online
loki_url=http://<host-incus-ip>:43100
transport=ssh
bind_ip=<host-incus-ip>
bind_port=43100
raw_tunnel_status=running
proxy_status=running
ready_status=ready
```

JSON output:

```json
{
  "status": "online",
  "loki_url": "http://10.x.x.1:43100",
  "transport": "ssh",
  "bind_ip": "10.x.x.1",
  "bind_port": 43100,
  "raw_tunnel_status": "running",
  "proxy_status": "running",
  "ready_status": "ready",
  "checked_at": "2026-05-07T00:00:00Z"
}
```

Exit codes:

- 0: online
- 1: degraded/offline
- 2: invalid setup

### `symphony_prod_loki_host_stop.sh`

Purpose: stop the host-owned raw tunnel and proxy safely.

Behavior:

- Read recorded PIDs from state.
- Confirm each PID command still looks like the expected raw tunnel or proxy command.
- Kill only recorded matching PIDs.
- Remove state and logs unless `--keep-state` is passed.
- Do not kill arbitrary `ssh`, `aws`, or `socat` processes.

### `symphony_prod_loki_status.sh`

Purpose: VM/agent-safe status helper.

Behavior:

- Load `.symphony/prod_loki_endpoint.env`.
- Probe `GET $LOKI_URL/ready`.
- Probe `GET $LOKI_URL/loki/api/v1/labels` when `jq` is available.
- Print online/offline status.
- Never inspect host PIDs or host systemd.
- If offline, print a repair instruction pointing to the Symphony UI or host repair helper.

### `symphony_prod_loki_query.sh`

Purpose: convenience helper for common agent log searches.

Arguments:

```text
--labels
--services
--service backend
--since 30m
--until 2026-05-07T00:00:00Z
--contains "TraceContextError"
--trace-id <trace_id>
--session-id <session_id>
--feedback-id <feedback_id>
--level ERROR
--limit 200
--raw-logql '{service="backend"} |= "TraceContextError"'
--json
```

Defaults:

- `--since 1h`
- `--limit 200`
- chronological output for log lines

Limits:

- Default cap helper-generated queries at 5000 lines.
- Raw LogQL/curl through the read-only proxy can still be allowed, but the helper should keep safe defaults.

Examples:

```bash
bash scripts/utilities/symphony_prod_loki_query.sh --services

bash scripts/utilities/symphony_prod_loki_query.sh \
  --service backend \
  --since 45m \
  --contains TraceContextError \
  --limit 200

bash scripts/utilities/symphony_prod_loki_query.sh \
  --service backend \
  --since 2h \
  --trace-id e36dcec59a984db7bb7f31f01d952314
```

Raw curl should remain possible:

```bash
source .symphony/prod_loki_endpoint.env

curl -fsS --get "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={service="backend"} |= "TraceContextError"' \
  --data-urlencode 'limit=100'
```

`--shell-env` must print only non-secret values such as `LOKI_URL`.

## Symphony UI Design

Add a small panel in the Symphony Elixir UI.

Panel title:

```text
Production Log Tunnel
```

Fields:

```text
Status: Online / Degraded / Offline / Unknown
Endpoint: http://<host-incus-ip>:43100
Transport: ssh / ssm / proxy
Last checked: timestamp
Last repair: timestamp
```

Actions:

```text
Check
Repair
Stop
```

Recommended UI behavior:

- `Check` always visible.
- `Repair` visible for offline/degraded states and optionally always available.
- `Stop` hidden behind an advanced/details area or confirmation.
- No raw log browser in v1.
- No arbitrary command input.

## UI Backend / Host Control

Because Symphony Elixir runs inside the VM and the tunnel is host-owned, the UI cannot simply run host scripts unless we build a bridge.

Two implementation choices:

### Choice 1: CLI Only First

Do not add UI repair in wave 1. Agents and Chris use host scripts or VM query helpers.

Pros:

- Fast.
- Less moving machinery.
- Good enough to validate the concept.

Cons:

- No button yet.

### Choice 2: Host Control Shim

Run a tiny host-side control service reachable only from the VM. It exposes fixed actions:

```text
GET  /prod-loki-tunnel/status
POST /prod-loki-tunnel/repair
POST /prod-loki-tunnel/stop
```

The shim can only call fixed scripts or fixed `systemctl` commands:

```bash
systemctl --user status symphony-prod-loki-tunnel.service
systemctl --user restart symphony-prod-loki-tunnel.service
systemctl --user stop symphony-prod-loki-tunnel.service
```

It must not accept arbitrary shell arguments.

This is safe to make password-less for Chris's local Symphony UI because the only operation is status/repair/stop for a read-only log tunnel.

## Systemd Service

Use a dedicated host service:

```text
symphony-prod-loki-tunnel.service
```

User service is preferred because the port is high and no root privilege should be needed.

Example concept:

```ini
[Unit]
Description=Symphony production Loki read-only tunnel
After=network-online.target

[Service]
Type=simple
ExecStart=/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/symphony_prod_loki_host_start.sh --foreground --transport ssh
ExecStop=/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/scripts/utilities/symphony_prod_loki_host_stop.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

If the start script daemonizes, do not use this exact unit. Prefer adding `--foreground` so systemd owns the long-running process.

Service implementation notes:

- Use `BatchMode=yes` so the service fails quickly when the SSH key is locked rather than hanging.
- Use `ExitOnForwardFailure=yes` so systemd sees startup failures.
- Use `ServerAliveInterval` and `ServerAliveCountMax` so dead tunnels are noticed.
- If the AGR PEM is missing, the status output should tell Chris to run `unlock-ssl`.
- If the PEM exists but SSH fails with `Permission denied (publickey)`, the status output should tell Chris to run `unlock-ssh`.

## Read-Only Proxy Policy

The VM-facing endpoint must be the proxy, not the raw tunnel.

Allow:

```text
GET /ready
HEAD /ready
GET /loki/api/v1/status/buildinfo
GET /loki/api/v1/labels
GET /loki/api/v1/label/<label>/values
GET /loki/api/v1/series
GET /loki/api/v1/query
GET /loki/api/v1/query_range
```

Reject:

```text
POST /loki/api/v1/push
POST, PUT, PATCH, DELETE for any path
any path not listed above
```

Optional proxy defaults:

- If `query_range` lacks `start`, inject now minus 1 hour or reject with a clear message.
- If `limit` is missing, inject `limit=1000`.
- If `limit` is duplicated, empty, non-positive, or exceeds 10000, reject.
- If `since` is present, treat it as the bounded window only when it is non-empty, parseable, and under the max window.
- Set a finite upstream timeout, for example 30 seconds.
- Log method, path, caller IP, and query length, but do not log full returned log payloads.

Do not overconstrain LogQL itself. The whole point is flexible investigation.

## Read-Only Proxy Implementation Sketch

Keep the proxy intentionally small. A single Python script is enough:

```text
scripts/utilities/symphony_prod_loki_readonly_proxy.py
```

Suggested arguments:

```text
--bind-ip <host-incus-ip>
--bind-port 43100
--upstream http://127.0.0.1:43101
--timeout-seconds 30
--default-limit 1000
--max-limit 10000
--default-window 1h
--max-window 24h
```

Use the Python standard library if practical. If using `requests` or another dependency, make sure it is already present in the local environment or document exactly how the helper checks for it.

Request handling rules:

1. Accept `GET` for allowed read endpoints.
2. Accept `HEAD` only for `/ready`.
3. Reject every other method with HTTP 405.
4. Normalize the path before matching.
5. Allow only the paths listed in `Read-Only Proxy Policy`.
6. Forward query parameters after applying default/cap behavior.
7. Stream or return the upstream response without storing log payloads on disk.
8. On upstream timeout, return HTTP 504 with a short non-secret message.
9. On rejected requests, return JSON with a clear error such as `{"error": "read-only Loki proxy rejected POST /loki/api/v1/push"}`.

Health behavior:

- `GET /ready` should proxy to upstream `/ready`.
- If upstream `/ready` fails, return non-200 so status helpers can report degraded/offline.

Logging behavior:

- Log one line per request to stdout/stderr for systemd capture.
- Include timestamp, method, path, status code, duration, caller IP, and query length.
- Do not log full query results.
- Avoid logging full LogQL query strings by default. A query length is enough for routine troubleshooting.

## Agent Workflow Instructions

Add a section to `.symphony/WORKFLOW.md` near the existing read-only curation DB guidance:

```markdown
For curator feedback or production bug work, production logs may be available through the read-only Loki tunnel.

Check status:

bash scripts/utilities/symphony_prod_loki_status.sh

List services:

bash scripts/utilities/symphony_prod_loki_query.sh --services

Search focused logs:

bash scripts/utilities/symphony_prod_loki_query.sh --service backend --since 1h --contains '<feedback-id-or-trace-id>' --limit 200

Use raw LogQL queries through the read-only endpoint when needed, but keep time windows focused. Do not paste full logs into Linear/Jira. Summarize findings and include only short relevant snippets. The Loki endpoint is read-only; do not attempt production SSH, VPN, DB, or service restarts unless Chris explicitly asks.
```

## Testing Plan

### Unit/Shell Tests

Test common helpers:

- State directory creation.
- Bind address validation rejects `0.0.0.0` by default.
- Bind address validation rejects LAN-facing addresses by default for VM mode.
- Port validation.
- Status parsing for online/degraded/offline.
- Stop only kills recorded PIDs with expected command signatures.
- Query helper builds correct LogQL for service, level, contains, trace ID, session ID, feedback ID, and raw LogQL.
- VM-side status works from `.symphony/prod_loki_endpoint.env` without reading host state.
- Proxy rejects `POST /loki/api/v1/push`.
- Proxy rejects unlisted paths.
- Proxy clamps or rejects excessive limits.

### Fake Loki Integration Test

Use a tiny local HTTP server or fixture to simulate:

- `/ready`
- `/loki/api/v1/labels`
- `/loki/api/v1/label/service/values`
- `/loki/api/v1/query_range`

Verify:

- `--services` prints services.
- `--contains` safely URL-encodes strings.
- `--limit` defaults and caps.
- `--json` emits valid JSON.

### Manual Host Test

On host:

```bash
bash scripts/utilities/symphony_prod_loki_host_start.sh --transport ssh
bash scripts/utilities/symphony_prod_loki_host_status.sh
bash scripts/utilities/symphony_prod_loki_query.sh --services
bash scripts/utilities/symphony_prod_loki_query.sh --service backend --since 10m --limit 20
```

From VM:

```bash
incus exec symphony-main -- sudo --login --user ctabone bash -lc '
  curl -fsS http://<host-incus-ip>:43100/ready
  curl -fsS http://<host-incus-ip>:43100/loki/api/v1/label/service/values | jq .
'
```

### UI Manual Test

If UI is implemented:

1. Stop tunnel.
2. Symphony UI shows `Offline`.
3. Click `Repair`.
4. UI transitions to `Online`.
5. Stop service externally.
6. UI transitions to `Offline` or `Degraded` on next check.
7. Click `Repair` again.
8. Confirm audit line appears in Symphony logs.

## Rollout Plan

1. Implement CLI helpers and tests.
2. Commit and push to `main`.
3. Sync VM checkout:

```bash
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec symphony-main -- sudo --login --user ctabone bash -lc \
  'cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation && git pull origin main'
```

4. If `.symphony/WORKFLOW.md` changes, push the runtime file into the VM source tree as described in `AGENTS.md`.
5. Install host systemd service if using always-on mode.
6. Start tunnel and verify from host.
7. Verify from VM.
8. Add UI status/repair only after the CLI helpers are stable.
9. Restart Symphony if UI/runtime code changed.

## Acceptance Criteria

From a Symphony issue workspace, a naive agent can run:

```bash
bash scripts/utilities/symphony_prod_loki_status.sh
bash scripts/utilities/symphony_prod_loki_query.sh --services
bash scripts/utilities/symphony_prod_loki_query.sh --service backend --since 30m --contains TraceContextError --limit 200
```

Expected:

- Status is online, or the output gives a clear repair instruction.
- Services include `backend`, `frontend`, `trace_review_backend`, `langfuse`, `promtail`, and other production services.
- Query returns relevant backend logs or a clear empty result.
- No production credentials are printed.
- The agent does not get production shell, DB, VPN, Docker, or service control.

If UI is implemented:

- Symphony shows Production Log Tunnel status.
- Repair restarts only the fixed tunnel service/shim.
- Failed repair reports useful non-secret error text.
- Repair action is logged.

## Open Questions

1. What is the stable host address reachable from `symphony-main` on this workstation?
2. Should the UI repair button be in v1 or wave 2?
3. Should the tunnel/proxy be always-on with systemd, or lazy-started by helper?
4. Should helper default window be 30 minutes or 1 hour?
5. Should helper hide `*-test` services by default?
6. Should SSM be added later as a fallback to SSH?

## Recommended Answers

For the first implementation:

- Use SSH tunnel from the host if the production PEM/VPN path is available.
- Do not include SSM in v1.
- Bind only to the host Incus bridge address.
- Put a read-only proxy in front of the raw Loki tunnel in v1.
- Write `.symphony/prod_loki_endpoint.env` and have VM helpers use only that URL.
- Implement CLI helpers first.
- Add UI status/repair once the helper behavior is proven.
- Use `--since 1h` and `--limit 200` defaults.
- Show all services; let agents choose intentionally.

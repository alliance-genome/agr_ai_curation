# Connection health observations

The public `GET /api/admin/health/connections` endpoint shares a demand-triggered
sweep among dashboard viewers within one application worker. The public
`GET /api/admin/health/connections/{service_id}` endpoint returns one service's
entry from the same observation and never probes on its own.
`GET /api/admin/health/llm-providers` and `GET /api/admin/health/packages`
require login. There is no
cross-worker coordination or background polling scheduler. Frontend polling
defaults are unchanged.

`CONNECTIONS_HEALTH_SNAPSHOT_TTL_SECONDS` defaults to 30 seconds, matching the
dashboard's existing React Query stale interval. The monotonic expiry and the
UTC `observed_at` response field both refer to **sweep start**, not response time.
Cache hits retain that timestamp. A sweep that consumes the whole freshness
window may return its completed observation to its waiting callers but is
already expired for the next request. Its age remains explicit. Each probe's
execution is bounded by that service's configured `timeout_seconds` (default
`CONNECTION_HEALTH_TIMEOUT_SECONDS=10`); asynchronous resource cleanup may take
up to one additional service timeout. Raising a service override therefore also
raises that probe's possible observation age and cleanup duration.

Only complete sweeps are published. Explicit unhealthy and intentionally
unconfigured service results may be cached; the existing healthy/degraded/
unhealthy status and counts retain their meanings and remain HTTP 200.
Reaching a service's total probe deadline or a nonzero isolated-probe exit
produces an explicit unhealthy result for that service after resource cleanup;
other service observations are retained. Isolated-process failures are reported
through the runtime observability facade with a fixed, secret-free message.
An aborted sweep returns a sanitized HTTP 503 and cannot reuse an expired healthy
snapshot. The initialization guard remains HTTP 503 and unknown
service ids remain HTTP 404. Unexpected sweep failures are reported once through
the runtime observability facade without raw exception content.

Snapshots contain copied, sanitized fields from private connection definitions.
Successful configuration loads
and resets advance a generation token, including loads of an empty configuration.
Failed reloads preserve the previous complete definitions. Old-generation work
can finish within its deadline, but cannot publish into the new generation or
mutate replacement definitions. Reset needs no running event loop.

The app lifespan owns the monitor and all pending tasks. Cancelling any or all
HTTP waiters does not cancel shared work. Tasks retain strong ownership and their
exceptions are retrieved even with no remaining waiters. Shutdown cancels and
awaits all pending work and drops the snapshot. A new lifespan owns a new monitor;
monitors cannot be reused on another event loop.

Aggregate PostgreSQL and credential-readiness probes use short-lived Python child
processes because credential providers and the supported psycopg2 recovery path
can block synchronously. Definitions travel over stdin, never command arguments
or temporary files. The child invokes the existing canonical probe/resolver.
Timeout, cancellation and completion kill its process group (including external
credential commands) and reap the child. This POSIX process ownership matches the
Docker runtime; it does not claim that cancelling an executor thread stops work.
HTTP and Redis probes remain asynchronous, with bounded client close; asyncpg
query and close are also bounded and an unsuccessful close terminates the
connection. Startup required-service checks, readiness, LLM-provider and
package routes do not acquire aggregate caching semantics.

Tests use barriers and a fake clock: a burst of 20 aggregate callers starts one
sweep; hits before expiry start none; a burst at expiry starts one new sweep.
This demonstrates probe-count reduction for overlapping callers in one worker,
not a measured production latency or cluster-wide reduction. Independent monitors
that require fresh diagnostics should use the readiness route.

Callers of the single-service route wait like callers of the summary route.
When the worker's observation is missing or expired, the request waits for a
sweep of **every** configured service, not just the one asked for, so it can
take as long as the slowest configured probe (`timeout_seconds` in
`config/connections.yaml`; weaviate's is 30 seconds), plus cleanup. Before this
route used the cache it waited only for the named service's own probe. A caller
with a short request timeout (for example `curl -m 5`) can time out on a cold
or expired observation; the sweep keeps running and a retry after it finishes
gets the cached answer. A caller that retries only once or twice in a few
seconds may therefore get no answer at all. Once answered, the result can be up
to `CONNECTIONS_HEALTH_SNAPSHOT_TTL_SECONDS` (30 seconds by default) old.

Known edge case: expiry is counted from sweep **start**, which is what keeps
every published observation's age within the TTL. A sweep that takes as long
as the TTL (with the defaults, a probe that hangs for weaviate's full 30-second
timeout) is already expired when it is published. Its own waiters get it, but
the next request starts a new sweep, so callers with short timeouts keep
timing out until the slow service answers or fails quickly again. With the
defaults (TTL 30 seconds, weaviate probe timeout 30 seconds) this happens
whenever weaviate hangs instead of refusing connections. Setting the TTL above
the slowest probe timeout avoids it.

# Synthetic benchmark replacement canary

Run from a fresh public checkout with Docker Compose available:

```bash
bash scripts/testing/benchmark-replacement-canary.sh
```

The command uses the existing `backend-persistence-tests` image/build and
PostgreSQL, Weaviate and Redis services. It creates a uniquely named Compose
project, migrates its fresh database to head and removes only that project's
containers, network and volumes on exit, including failure. It never points at
an existing application stack. Like the ordinary Docker test helper, it defaults
to rootless Docker; select the system daemon explicitly with
`AI_CURATION_TEST_DOCKER_MODE=rootful` when appropriate.

The small Compose overlay makes the test network internal. Provider credentials
are not forwarded. JWT signing keys and tokens are generated in memory; source
text is synthetic and no private resolver, portal, Cognito or paid model is
contacted. This is an offline integration guard, not deployed release evidence
or a biological extraction-quality evaluation.

`BENCHMARK_CANARY_SERVER_TIMEOUT_SECONDS` is a test-only setting (default `30`)
for loopback API startup and shutdown waits. Increase it for a slow test host;
it does not change an application or provider timeout. The wrapper forwards this
setting to the test container.

If the Docker daemon has exhausted its automatic address pools, supply an
ordinary Compose override after verifying its explicit subnet does not overlap
existing Docker, host or VPN routes:

```bash
bash scripts/testing/benchmark-replacement-canary.sh -f /path/to/local-network.yml
```

Do not delete existing networks to make room. No machine-specific subnet is
part of the checked-in defaults. A failed cleanup must be resolved against the
exact project name printed by Compose, not a global Docker prune.

## Boundaries exercised

| Real path | Synthetic boundary |
|---|---|
| RS256 key selection/signature, issuer/audience/expiry, capability and initiating-human checks | OIDC discovery/JWKS delivery and Cognito administrative membership responses |
| Active SQL curator, frozen input upload/read/resolver and owned document preparation journal/files/Weaviate | Hierarchy, abstract and figure model outputs; vectorization disabled in the existing persistence fixture |
| Public CLI HTTP commands, authoritative preview/admission, idempotency and durable worker leases | Reviewed test catalog and deterministic external agent/flow execution |
| Agent/flow runtime adapters, independent role routes, durable invocation attempts and exact result bytes | Model response content and usage events; unknown cost stays unknown |
| Durable events/410 recovery, explicit failed-cell rerun, cancellation, owner-bound deletion, uncertain-running recovery | Controlled failure and expired lease fixture |
| Persisted flow extraction, deterministic curation prep, owned snapshot export and durable handoff replay | Review-session association fixture and configured fake HTTP sink/token endpoint |

The flow executor boundary supplies persisted extraction references, not prose
masquerading as a result. The real adapter reloads them with owner/document/run
checks. The canary does not claim the synthetic flow tests every internal
supervisor/tool/validator scheduling branch; existing runtime unit tests cover
those branches. Likewise, seeded review-session association is not a browser
bootstrap or gold-review claim.

The lifecycle canary substitutes agent construction at its synthetic boundary.
The same wrapper also runs a second, cold-worker startup regression using a fresh
Python process, migrated database prompt and agent rows, and the actual agent
builder. It checks the missing-cache failure before startup and successful
construction through the worker entrypoint after startup. Its finite loop does
not claim a job or invoke a provider. This second test proves construction only,
not live model or Langfuse connectivity. The existing
`BENCHMARK_CANARY_SERVER_TIMEOUT_SECONDS` setting also bounds its child process.

Prepared jobs cannot be SQL-deleted safely because their external preparation
artifacts can outlive cancellation. The canary verifies that refusal and proves
successful/idempotent deletion on a separate unprepared cancelled job. Expired
running invocation recovery must record `interrupted_uncertain` without replay.
The test reads results and progress without creating new work, and handoff
uncertainty never triggers a second delivery automatically.

The final JSON line is an aggregate test receipt; pytest failure is authoritative.
The test is skipped in ordinary persistence runs and must be run explicitly via
the wrapper. Existing focused authentication, lifecycle, worker, source-transfer,
result-artifact and handoff tests remain the detailed contract coverage.

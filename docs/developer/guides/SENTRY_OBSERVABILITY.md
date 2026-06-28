# Sentry Observability Guide

This guide documents the backend Sentry operating model for AI Curation. It is
for repository contributors adding runtime error reporting, not for storing
secrets or running the self-hosted Sentry stack.

Sentry is the focal point for application error events. CloudWatch and container
logs remain the line-by-line operational record. Langfuse remains the LLM trace
and model-call observability system.

## Configuration

Sentry is disabled unless `SENTRY_DSN` is set.

Configure these values in ignored `.env` files or AWS Secrets Manager, never in
git:

```bash
SENTRY_DSN=
SENTRY_ENVIRONMENT=local
SENTRY_RELEASE=
SENTRY_ALLOW_INSECURE_DSN=false
SENTRY_SYNTHETIC_TEST_ENDPOINTS_ENABLED=false
SENTRY_TRACES_SAMPLE_RATE=
SENTRY_PROFILES_SAMPLE_RATE=
RUNTIME_OBSERVABILITY_TAG_VALUE_MAX_CHARS=200
RUNTIME_OBSERVABILITY_CONTEXT_VALUE_MAX_CHARS=500
```

Use `SENTRY_RELEASE` for every deployed candidate so events can be tied back to
the exact commit. Leave tracing and profiling blank unless transport and
redaction have been explicitly validated for that environment.

Use separate Sentry projects/DSNs for dev and production. `SENTRY_ENVIRONMENT`
is still required, but it is a label/filter, not the isolation boundary.
Current backend routing uses:

- dev: `ai-curation-backend-dev`;
- production: `ai-curation-backend-prod`.

The runtime secret `ai-curation/sentry/runtime` stores the project metadata.
Application hosts should use the private VPC DSN field for their environment;
do not point dev at the production DSN or production at the dev DSN.

If `SENTRY_ENVIRONMENT` is blank, backend setup falls back to `APP_ENV`, then
`ENVIRONMENT`, then `local`. If `SENTRY_RELEASE` is blank, setup falls back to
`GIT_SHA`.

`http://` DSNs are rejected unless `SENTRY_ALLOW_INSECURE_DSN=true`. Use that
only for tightly scoped dev/VPC smoke testing while TLS or a private transport
path is being finished.

`RUNTIME_OBSERVABILITY_TAG_VALUE_MAX_CHARS` and
`RUNTIME_OBSERVABILITY_CONTEXT_VALUE_MAX_CHARS` apply to
`report_runtime_exception()`. Background-task and tool-failure reporting use
their own conservative caps.

## Initialization

Backend initialization lives in `backend/src/lib/observability/sentry.py` and is
called during app startup. The SDK setup:

- uses `before_send` and `before_send_transaction` to redact event payloads;
- sets `send_default_pii=False`;
- drops local stack-frame variables with `include_local_variables=False`;
- keeps Sentry logging integration as breadcrumbs only;
- disables Starlette/FastAPI handled-status auto-capture so explicit handled
  5xx reports do not duplicate framework events.

Unhandled backend exceptions are handled by the Sentry framework integrations.
Handled failures must report explicitly through one of the facades below.

## What Sentry May Contain

Allowed low-risk context:

- component and operation names;
- HTTP status code and log level;
- logger name;
- bounded counts, booleans, and stage names;
- operational trace IDs when they are needed for debugging;
- hashed identifiers for documents, sessions, runs, jobs, turns, flows, and
  batches.

Never send raw curator or document content:

- prompts, messages, transcripts, raw text, chunks, abstracts, PDF content, or
  verified quotes;
- API keys, DSNs, cookies, auth headers, tokens, passwords, private keys, or
  credentials;
- raw SQLAlchemy exceptions when their statement or params may contain prompt,
  custom-agent, flow-definition, feedback, or document text.

Global redaction filters sensitive keys, content-like keys, common secret
patterns, request query strings, cookies, request bodies, exception values,
breadcrumbs, arbitrary extra data, and stack-frame locals.

## Reporting Facades

Use `raise_sanitized_http_exception()` for caught endpoint failures that become
client-safe HTTP errors:

```python
raise_sanitized_http_exception(
    logger,
    status_code=500,
    detail="Failed to update resource",
    log_message="Unexpected database error updating resource",
    exc=sanitized_exc,
)
```

Rules:

- report only server-side failures; 4xx validation/auth conflicts generally
  should not report;
- keep public `detail` stable and client-safe;
- pass a sanitized wrapper exception if the original exception may include
  curator text, SQL params, prompts, flow definitions, or feedback;
- ensure sanitized wrappers do not retain raw exception chains when the original
  exception is sensitive.

Use `report_runtime_exception()` for caught non-HTTP runtime failures:

```python
report_runtime_exception(
    exc,
    component="flow_executor",
    operation="extraction_persistence_failed",
    context={"chunk_count": chunk_count, "flow_run_id": flow_run_id},
)
```

Identifier-like runtime context is hashed by the global Sentry hook when it uses
recognized keys such as `batch_id`, `document_id`, `flow_id`, `flow_run_id`,
`job_id`, `run_id`, `session_id`, `trace_id`, or `turn_id`.

Use `add_observed_background_task()` or `report_background_task_exception()` for
FastAPI background tasks. Background-task identifier tags are hashed before
capture.

Use the tool-failure notifier only for tool/specialist failure alerts. Do not
use it as a generic application-error facade.

## Sensitive Database Wrappers

Some exception types should not be logged or captured directly. SQLAlchemy
`IntegrityError` and similar DB exceptions can include statement params. If
those params might contain curator text, prompts, custom-agent definitions,
feedback, or flow definitions, create a small sanitized wrapper.

Pattern:

```python
class _ResourceDatabaseError(RuntimeError):
    """Sanitized database failure safe for logs and Sentry."""


def _sanitized_resource_db_error(orig_type_name: str) -> _ResourceDatabaseError:
    try:
        raise _ResourceDatabaseError(f"Resource save failed ({orig_type_name})") from None
    except _ResourceDatabaseError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized
```

Call this from the raw exception handler after rollback, passing only
`type(exc.orig).__name__` or another safe derived value. Tests should assert:

- rollback happened;
- public HTTP detail is unchanged;
- reported exception is the sanitized wrapper;
- `__traceback__` exists;
- `__context__` and `__cause__` are `None`;
- fake sensitive text is absent from logs and reported exception text.

## Dev Smoke Testing

Synthetic HTTP smoke endpoints are guarded by both `DEV_MODE=true` and
`SENTRY_SYNTHETIC_TEST_ENDPOINTS_ENABLED=true`:

```bash
curl -X POST http://127.0.0.1:8000/api/observability/sentry/synthetic-unhandled
curl -X POST http://127.0.0.1:8000/api/observability/sentry/synthetic-caught-alert
```

Leave `SENTRY_SYNTHETIC_TEST_ENDPOINTS_ENABLED=false` outside a narrow dev
smoke. After a smoke, verify those routes return 404 when disabled.

For path-specific smoke tests, prefer an in-container Python script that imports
the deployed module, monkeypatches only the failing dependency in process,
initializes Sentry, calls the real handler, flushes Sentry, and queries Sentry by
a unique `logger_name`. This avoids adding permanent test-only routes.

Every dev smoke should record:

- deployed commit and `SENTRY_RELEASE`;
- health check result after deploy;
- synthetic smoke ID;
- Sentry group and event ID;
- exact public HTTP response;
- redaction checks proving fake secrets/content are absent;
- post-smoke health and fake-route 404 checks.

## CloudWatch, Logs, Langfuse, And Sentry

CloudWatch and Docker logs are verbose operational logs. They may include local
diagnostic detail that is too noisy for Sentry, but they must never include
secrets. Curator or document content in logs requires an explicitly reviewed
exception and should not be introduced incidentally while adding Sentry
instrumentation.

Sentry is for actionable application error events:

- unhandled backend exceptions;
- explicit handled 5xx failures;
- caught runtime/background failures that would otherwise disappear in logs;
- enough bounded context to locate the affected component and release.

Langfuse is for LLM traces, model calls, token/cost analysis, and TraceReview.
Do not duplicate full prompt/message/transcript payloads into Sentry.

The current CloudWatch-to-Sentry bridge is intentionally deferred. Until that
ticket is implemented, do not try to convert every log line into a Sentry event.
Add explicit Sentry captures at meaningful application failure boundaries.

## Frontend Capture

Frontend/browser Sentry capture is optional and currently deferred unless a
dedicated frontend DSN and redaction plan are added. Do not enable browser
session replay or capture curator-entered form/document text without a separate
review.

## Review Checklist

Before merging a new Sentry-reporting path:

- no secret or content-like values are used as tags, exception messages, extras,
  or custom context;
- sensitive raw exceptions are wrapped and exception chains are severed;
- public HTTP details remain stable;
- 4xx paths are not reported unless there is a deliberate security reason;
- framework auto-capture and explicit helper capture do not duplicate the same
  handled failure;
- tests assert capture behavior and redaction using fake sensitive strings;
- `.env.example` documents any new operational limit or feature flag;
- dev smoke evidence includes release, event ID, and redaction checks for
  runtime-affecting changes.

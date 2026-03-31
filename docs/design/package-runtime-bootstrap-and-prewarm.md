# Package Runtime Bootstrap And Prewarm Plan

## Status

This note documents a real production-like failure mode seen in the Symphony main sandbox and the intended fix direction. It is written to survive a context reset and to be explicit enough that a fresh agent can continue the work without rediscovering the architecture.

## Why This Exists

The AI Curation backend now supports a modular runtime package system. Tool implementations do not all live directly in the generic backend anymore. Instead, packages can contribute:

- tool bindings
- prompts
- schemas
- other runtime assets

Each runtime package may have its own Python dependencies. To avoid dependency collisions, package tool execution happens in an isolated per-package virtual environment.

That design is good and should stay. The problem is not the modular package system itself. The problem is the first-use bootstrap behavior under concurrent tool calls.

## ELI5 Version

Think of the backend as a workshop with separate toolboxes.

- The generic backend is the workshop.
- Each runtime package is a toolbox.
- Each toolbox gets its own mini workbench so its tools do not get mixed up with other toolboxes.

When the app uses a packaged tool for the first time, it may need to:

1. build the mini workbench
2. install the package's dependencies
3. mark that workbench as ready

That startup step is called bootstrap.

The failure we hit is basically:

1. two people asked for tools from the same toolbox at almost the same time
2. both saw that the workbench was not ready yet
3. both tried to build it simultaneously
4. they got in each other's way and broke the setup

So the right fix is:

1. only let one person build a given toolbox workbench at a time
2. if possible, build the workbench before the first real user needs it

Those are two different improvements:

- serialization/locking: correctness fix
- prewarm/bootstrap on startup: latency fix

We want both.

## What We Observed

### Earlier failure mode

We first investigated an allele extraction failure that looked like an evidence-guard issue, then like an AGR timeout. That investigation found a real performance bug:

- warm `agr_curation_query(search_genes, gene_symbol="Actin")` across all providers could hit the package runner timeout
- the single-search `search_genes` and `search_alleles` paths were still doing one detail lookup per result
- the bulk variants already had batched detail lookup helpers

That issue was fixed by switching single-search paths to the same batched detail fetch strategy.

### New failure mode after refreshing the sandbox

After the sandbox picked up the AGR batching fix, the failure changed. That is important because it means the new problem is distinct.

In the main sandbox backend logs for:

- session id: `e46e3184-0dbc-4e3c-8fc5-2dc47057fb8a`
- trace id: `d43c5f6fb7692de8aec43ade7337c862`

the allele extractor failed before it even reached AGR lookup.

The relevant sequence was:

1. the specialist started
2. it called `search_document`
3. it called `search_document` again almost immediately
4. the first attempt failed with:
   - `Package tool 'search_document' execution failed: Failed to install_requirements for package 'agr.alliance'`
5. the retry then failed with:
   - `[Errno 39] Directory not empty: 'base_llm'`

That failure pattern strongly indicates a concurrent bootstrap race against the same package environment directory.

## Root Cause

`PackageEnvironmentManager.ensure_environment()` previously did this logic without a per-package lock:

1. inspect metadata + venv path
2. if ready, reuse
3. otherwise:
   - delete existing venv
   - create venv
   - install requirements
   - write metadata

That is fine if exactly one caller does it.

That is not safe if multiple requests hit the same package concurrently.

In the sandbox, packaged tools like `search_document` can now execute through the runtime package system. The allele extractor issued multiple `search_document` tool calls close together on a fresh runtime. Both calls reached bootstrap. Without serialization:

- one call can remove a venv while another is installing into it
- one call can see partial state
- one call can fail while another leaves a half-built environment behind
- retries can then trip over leftover directories and partially installed dependencies

The `Directory not empty: 'base_llm'` error is consistent with that exact sort of partial, concurrent filesystem mutation.

## Desired Behavior

### Correctness

For any single package id:

- at most one bootstrap operation may run at a time
- other callers must wait until that bootstrap completes
- once the first bootstrap finishes, waiting callers should re-check metadata and reuse the resulting environment

### Startup behavior

When configured, the backend should eagerly prepare package tool environments during startup so the first user query does not pay bootstrap cost.

This needs to work in both startup paths:

1. the production runtime entrypoint path
2. the FastAPI startup path used by the dev-compose/Symphony sandbox backend

If startup prewarm is only wired through the production entrypoint, the Symphony main sandbox still misses the benefit.

## Existing Architecture Notes

### Package runtime paths

Runtime package state lives under the runtime root, typically:

- `/runtime/packages`
- `/runtime/state/package_runner/<package_id>/`

Important package runner files:

- venv dir:
  - `.../venv`
- metadata:
  - `.../environment.json`

### Existing startup hook

There was already an opt-in environment variable:

- `AGR_BOOTSTRAP_PACKAGE_ENVS_ON_START`

and the production runtime entrypoint already had logic to bootstrap package environments when that variable is true.

However, the Symphony main sandbox backend path is driven by `docker-compose.yml` and `backend/main.py`, not only by the production runtime entrypoint.

So the prewarm logic needed to be reachable from the FastAPI lifespan startup too.

## Implementation Plan

### 1. Add per-package bootstrap serialization

In `backend/src/lib/packages/env_manager.py`:

- create a per-package lock file inside the package runner state directory
- acquire an exclusive lock before:
  - reading package metadata for reuse decision
  - deleting an existing venv
  - creating a new venv
  - installing requirements
  - writing metadata
- after acquiring the lock, re-check whether a valid environment now exists
- if another caller already finished bootstrap, immediately reuse that environment

On Linux, `fcntl.flock` is the right simple mechanism.

This is the correctness fix. It prevents the race even if no prewarm happens.

### 2. Expose a shared startup helper

In `backend/src/lib/runtime_entrypoint.py`:

- keep the existing opt-in env flag
- factor the actual package prewarm loop so it can be reused
- add a helper that:
  - checks the env flag
  - ensures runtime directories exist
  - validates runtime packages
  - bootstraps package tool environments
  - returns whether it actually ran

This lets both the production entrypoint and the FastAPI app reuse the same logic.

### 3. Call prewarm from FastAPI startup

In `backend/main.py`:

- call the shared startup helper during lifespan startup
- do it early enough that user traffic cannot arrive first
- fail fast if startup prewarm is enabled and package bootstrap cannot complete
- if the production/runtime entrypoint already performed prewarm, skip the FastAPI-side prewarm via a sentinel environment variable rather than doing the work twice

This is how the Symphony sandbox backend actually benefits from the prewarm.

### 4. Enable prewarm in compose configs

In the compose files used for normal backend startup:

- set `AGR_BOOTSTRAP_PACKAGE_ENVS_ON_START=true` by default
- keep it overrideable with an environment variable
- increase the backend healthcheck `start_period` so a cold first boot is not marked unhealthy while package environments are still being prepared

At minimum this should cover:

- `docker-compose.yml`
- `docker-compose.production.yml`

The goal is:

- sandbox/dev backend: prewarm on startup
- production backend: prewarm on startup
- tests: no mandatory prewarm unless explicitly enabled

### 5. Keep the earlier AGR performance fix

This package bootstrap work does not replace the earlier AGR batching fix.

Both are needed:

- AGR batching fixes the expensive query path
- bootstrap locking/prewarm fixes first-use package races

## Regression Tests To Keep

### Package bootstrap concurrency

Add a regression that proves:

- two concurrent `ensure_environment()` calls on the same package do not both run bootstrap
- only one call performs `create_venv` + `install_requirements`
- the second caller waits and then reuses the completed environment

This is the most important new test because it directly encodes the sandbox failure mode.

### Startup helper tests

Add unit tests that prove:

- startup prewarm helper is a no-op when disabled
- startup prewarm helper ensures runtime layout, validates packages, and bootstraps when enabled
- FastAPI lifespan calls the helper
- FastAPI lifespan fails fast if the helper raises

### Existing AGR tests

Keep the earlier targeted AGR tests that ensure:

- single `search_genes` uses batched detail lookup
- single `search_alleles` uses batched detail lookup
- bulk search behavior remains correct

## Validation Plan

### Local/backend tests

Run at least:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc \
  "cd /app/backend && python -m pytest \
    tests/unit/lib/packages/test_package_runner.py \
    tests/unit/lib/test_runtime_entrypoint.py \
    tests/unit/test_main_startup.py \
    tests/unit/lib/openai_agents/tools/test_agr_curation_provider_config.py \
    tests/unit/lib/openai_agents/tools/test_agr_curation_query_paths.py -q"
```

Also run syntax-only validation:

```bash
python3 -m py_compile backend/src/lib/packages/env_manager.py
python3 -m py_compile backend/src/lib/runtime_entrypoint.py
python3 -m py_compile backend/main.py
python3 -m py_compile backend/src/lib/openai_agents/tools/agr_curation.py
```

### Symphony sandbox validation

After commit/push and sandbox refresh:

1. ensure the main sandbox backend is on the new commit
2. watch backend logs during a first post-refresh query
3. verify there is no package bootstrap race
4. verify the allele extractor gets past `search_document`

The target query to retry is:

- `Please list all the actin alleles used in this publication.`

What we want to see:

- no `Failed to install_requirements`
- no `Directory not empty`
- no early `search_document` package failure
- no false backend unhealthy status during a cold startup that is still legitimately prewarming packages

## Why Prewarm Alone Is Not Enough

Even if startup prewarm is enabled, locking is still necessary.

Reasons:

- startup prewarm may be disabled in some environments
- a runtime directory could be wiped or partially cleaned later
- multiple app processes or requests can still converge on first-use behavior
- future packages may be added without startup coverage

So:

- prewarm reduces latency and avoids many first-user stalls
- locking guarantees safety when prewarm is absent, late, or incomplete

## Why Locking Alone Is Not Enough

Locking fixes correctness, but it does not make the first query fast.

If the first user query has to wait while a package environment is built and dependencies are installed, the system may feel slow even though it no longer fails.

So:

- locking is the correctness fix
- prewarm is the UX/performance fix

## Future Nice-To-Haves

These are optional after the core fix:

- add metrics/logging for:
  - package environment reuse
  - package bootstrap duration
  - which package ids are being prewarmed
- consider a startup summary log of all prewarmed packages
- consider a small admin/health endpoint surface for package runtime readiness
- consider configurable package tool subprocess timeout hardening if other tools remain close to timeout even after batching

## Summary

The correct long-term behavior is:

1. packaged tool environments are safe under concurrency
2. the backend can prewarm them before the first real query
3. both production and Symphony/dev startup paths share that behavior

That is the full intended change set.

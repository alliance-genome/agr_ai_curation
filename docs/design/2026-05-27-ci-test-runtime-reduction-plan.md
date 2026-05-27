# CI Test Runtime Reduction Plan

Date: 2026-05-27

## Context

Backend unit tests are now taking about 8 minutes in GitHub Actions. PR #424 is
a representative recent workload: a small final fix on top of a broad
Gene Expression validator/domain-pack stack still triggers the full backend unit
job because the workflow runs one serial `pytest tests/unit/` pass whenever
backend, config, package, or related shared paths change.

Current relevant behavior:

- `.github/workflows/test.yml` runs `Backend Unit Tests` as one job on
  `ubuntu-latest`.
- The unit job builds `backend/Dockerfile.unit-test` with Buildx GHA layer cache.
- The job then runs `backend/tests/unit/run_ci_unit_tests.sh`.
- The unit script runs one serial pytest process with coverage:
  `python -m pytest tests/unit/ -v --tb=short --strict-markers --cov=src ...`.
- The Agent PR Gate already runs scoped `ruff` and path validation, but it does
  not replace the full backend unit job.
- Frontend jobs already use npm caching through `actions/setup-node`.

Target outcome:

- Bring backend unit PR wall time below 5 minutes first, ideally toward 3-4
  minutes for ordinary PRs.
- Preserve confidence for broad domain-pack/backend changes.
- Keep coverage reporting available without making every shard independently
  enforce a whole-suite threshold.
- Avoid introducing flaky parallel behavior silently.
- Preserve existing required GitHub check names, especially `Backend Unit Tests`,
  because `Agent PR Gate` waits for those names explicitly.

## Survey Findings

The external ecosystem supports four useful approaches for this repo:

1. `pytest-xdist` can run tests across multiple processes with `-n auto`.
   It also supports distribution modes such as `loadscope`, `loadfile`,
   `loadgroup`, and `worksteal`; `loadscope` and `loadfile` are especially
   relevant if our fixtures have module/file-local assumptions.

2. `pytest-split` shards a suite into similarly sized groups based on stored
   test durations. It is designed for CI matrix fan-out, which fits GitHub
   Actions better than one runner trying to use more cores.

3. Coverage.py supports combining separate coverage data files after tests run
   under different conditions or in different processes. That lets us shard
   tests while preserving one final coverage report and threshold.

4. Affected-test tools such as `pytest-testmon` can select tests based on code
   executed by prior runs. That is attractive for local/Symphony feedback, but
   it is a higher-risk replacement for PR gates because it depends on a valid
   persisted dependency database and can expose hidden test dependencies.

Docker/GitHub caching is already partly in place through Buildx
`cache-from/cache-to: type=gha`. The remaining opportunity is less "add cache"
and more "avoid rebuilding similar images repeatedly" or "reuse/publish a test
image across jobs." This becomes an immediate concern once the backend unit job
is sharded, because the current build step lives inside the backend unit job. A
4-way matrix that repeats Buildx setup, image build, and `load: true` four times
may lose much of the pytest wall-time win.

References:

- pytest-xdist distribution modes:
  https://pytest-xdist.readthedocs.io/en/stable/distribution.html
- pytest-split duration-balanced CI sharding:
  https://pypi.org/project/pytest-split/
- Coverage.py combine:
  https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html
- Docker Build GitHub Actions cache:
  https://docs.docker.com/build/cache/backends/gha/
- pytest-testmon affected-test selection:
  https://www.testmon.org/
- GitHub Actions Python and cache docs:
  https://docs.github.com/en/actions/tutorials/build-and-test-code/python
  https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching

## Recommended Rollout

### Tactical First Pass: In-Place Xdist Before Sharding

Goal: get a meaningful speed win without changing required check names,
coverage artifact topology, or Agent PR Gate behavior.

Implementation:

- Keep the existing `Backend Unit Tests` job as a single GitHub check.
- Add conservative `pytest-xdist` support to the existing backend unit runner.
- Default local/script execution to serial unless a worker count is explicitly
  provided through an argument or environment variable.
- Set CI to use an explicit worker count with `--dist loadscope`. The first
  green trial used `2` workers. A public-repo standard-runner trial with `4`
  workers passed, but was slightly slower (`5m47s` job / `4m29s` pytest versus
  `5m27s` job / `4m26s` pytest), so keep CI at `2` workers unless later suite
  shape changes make the extra workers useful.
- Add `--durations=25`, JUnit output, and step summary timing in the same pass.

Why this precedes sharding:

- It avoids the matrix check-name problem entirely.
- `pytest-cov` can handle xdist coverage in the same process/job path, so we do
  not need coverage artifact combining yet.
- It avoids multiplying Docker image build/load time across matrix jobs.
- It is reversible by setting the worker count back to `0`.

Acceptance criteria:

- The existing unit-test image builds with `pytest-xdist` installed.
- A representative xdist run succeeds, or any failures reproduce serially and
  are confirmed unrelated to xdist.
- CI summary shows worker count, xdist distribution mode, pytest duration,
  slow-test report setting, and JUnit artifact path.
- The GitHub check remains named `Backend Unit Tests`.

Local follow-up profiling on 2026-05-27 found two immediate lessons:

- Keep `--dist loadscope` for now. A no-coverage full-suite comparison on the
  same Docker image passed in both modes, but `loadscope` was faster
  (`3301 passed, 10 skipped in 78.32s`) than item-level `load`
  (`3301 passed, 10 skipped in 87.15s`). The single slow
  `test_package_runner.py` file does improve under `--dist load`, but the full
  suite does not currently benefit enough to justify changing CI behavior.
- Repeated curation adapter registry rebuilds are a real test-local cost. A
  fresh `load_curation_adapter_registry()` build takes about `1.42-1.50s`,
  while a cached lookup is effectively zero. Tests that do not mutate the
  registry input should clear this cache once per module, not once per test.
  This reduced `test_curation_prep_invocation.py` plus
  `test_curation_prep_service.py` from about `13.7s` to `2.8s`, and
  `test_streaming_tools_helpers.py` from about `13.3s` to `3.7s`.
- Unit retry tests should mock retry sleep consistently. The feedback email
  notifier retry tests were paying real `1s + 2s` backoff waits in several
  cases; after mocking `time.sleep`, that file dropped from about `3.8s` to
  `0.8s` elapsed in the same Docker image.
- Package-runner tests should avoid recreating venvs unless the test mutates
  package inputs. The demo package tests intentionally keep per-test runtime
  roots because they edit fixture package files. The Alliance package binding
  tests do not mutate the package manifest or requirements, so they can share a
  module-scoped isolated venv while still varying fake runtime roots through
  per-test `monkeypatch` environment variables. That reduced
  `test_package_runner.py` from about `56s` to about `41s` without removing the
  subprocess isolation check, and brought the no-coverage full unit suite with
  `-n 2 --dist loadscope` down to about `63s` locally.

### Phase 0: Add Timing Visibility

Goal: know whether the 8-minute runtime is broad suite growth, coverage/report
overhead, a few slow files, repeated Docker image setup, or expensive fixtures.

Implementation:

- Add `--durations=25` to the backend unit CI pytest invocation.
- Emit the slowest-test section into the GitHub step summary or upload it as a
  small artifact.
- Add `--junitxml=...` so future tooling can track per-test durations.
- Capture coarse step timings for:
  - Buildx setup.
  - Unit image build/load.
  - Ignore-path validation.
  - Pytest collection and execution.
  - Coverage report generation.
- Produce or document a full machine-readable duration artifact suitable for
  `pytest-split`, not just the top 25 human-readable slow tests. The first
  implementation can do this by running the serial baseline with
  `pytest-split --store-durations` once `pytest-split` is introduced, or by
  retaining JUnit timing data and converting it in the sharding prototype.
- Keep the existing serial run and coverage threshold unchanged.

Acceptance criteria:

- Every backend unit CI run reports the slowest 25 tests.
- The CI log or summary separates Docker image time from pytest time.
- A path exists to seed complete duration data for sharding.
- No behavior change to pass/fail semantics.
- We can identify the top slow files after one or two PRs.

Why first:

- It is nearly zero risk.
- It tells us whether to shard by test item duration, manually split a few slow
  modules, or fix accidental integration work inside unit tests.
- It prevents us from scaling the wrong bottleneck. If Docker build/load is a
  large part of the 8 minutes, sharding without image reuse may disappoint.
- It can reveal domain-pack/config fixture costs. PR #424 added several large
  domain-pack and vocabulary-helper test areas; if those dominate runtime, a
  fixture-loading optimization may be better than only adding more runners.

Decision point after Phase 0:

- If pytest execution dominates and slow tests are broadly distributed, proceed
  to sharding.
- If Docker image build/load dominates, prioritize the image-reuse work before a
  large matrix rollout.
- If slow tests cluster around domain-pack/config setup, create a small
  optimization ticket for fixture caching, domain-pack load reuse, or file/module
  grouping before moving directly to 4 shards.

### Phase 1: Shard Backend Unit Tests Across GitHub Matrix Jobs

Goal: reduce wall time by horizontal scaling without requiring tests to be
safe inside one multi-process pytest worker set.

Preferred implementation:

- Add `pytest-split` to the backend test dependency set in a way that keeps
  local, Symphony, and CI behavior aligned. Prefer pinning through the backend
  dependency files used by `backend/Dockerfile.unit-test` instead of installing a
  floating version only in the Dockerfile.
- Store a committed duration file, for example
  `backend/tests/unit/.test_durations`.
- Add script support in `backend/tests/unit/run_ci_unit_tests.sh`:
  - `--shard-count N`
  - `--shard-index I`
  - `--store-durations`
  - `--coverage-mode final|shard|off`
- Use a GitHub Actions matrix such as `shard: [1, 2, 3, 4]`.
- Each shard runs the same ignore-path logic, then:
  `python -m pytest tests/unit/ ... --splits 4 --group ${shard}`.
- Use the `least_duration` splitting algorithm unless order preservation proves
  necessary.

GitHub check-name design:

- Do not let the matrix replace the required `Backend Unit Tests` check with
  matrix-specific names such as `Backend Unit Tests (1/4)`.
- Name shard jobs explicitly, for example `Backend Unit Tests Shard 1`,
  `Backend Unit Tests Shard 2`, and so on.
- Add a non-matrix aggregate job named exactly `Backend Unit Tests` that depends
  on all shard jobs and the coverage-combine job.
- Keep `.github/workflows/agent-pr-gate.yml` aligned with this aggregate name,
  because it currently waits for exact check names including `Backend Unit Tests`.
- Confirm branch protection and any external dashboards still watch the stable
  aggregate check name.

Coverage design:

- In shard jobs, use coverage parallel data files with a unique `COVERAGE_FILE`
  such as `.coverage.unit-shard-${shard}`.
- Upload uniquely named artifacts per shard, for example
  `backend-unit-coverage-shard-${shard}`.
- Add a small coverage-combine job that downloads all shard coverage artifacts,
  runs `coverage combine`, then emits term/xml/html reports and enforces the
  current `--cov-fail-under=50` threshold.
- Add a `.coveragerc` or equivalent pytest-cov configuration before combining
  coverage from Docker and GitHub workspace paths. At minimum, map container
  paths such as `/app/backend/src` to workspace paths such as `backend/src`.
- Run coverage combine either inside the same unit-test image or in a Python
  environment with a matching `coverage` version.
- If coverage combine proves too fussy initially, start with sharded tests
  without coverage on PRs and keep the existing full coverage run on `main` or
  nightly. That should be treated as a temporary migration step, not the final
  design.

Image build/load design:

- Measure whether every shard repeats the `backend/Dockerfile.unit-test` build
  and `load: true` cost.
- For the 2-shard prototype, explicitly record total build/load time versus
  pytest time before deciding whether to scale to 4 shards.
- If repeated image setup is costly, add a build-once/pull-many path before
  defaulting to 4 shards. Options include publishing a PR-scoped image keyed by
  Dockerfile and lockfile hash, using GitHub Container Registry, or keeping one
  serial image-build job that downstream test jobs can consume.

Acceptance criteria:

- Backend unit PR wall time is materially below the current 8-minute baseline.
- The final combined coverage report is equivalent enough for the current
  low-threshold gate.
- The failure UI identifies which shard failed.
- Developers can reproduce a failed shard locally with one documented command,
  including the exact Docker or Docker Compose invocation and shard arguments.
- `Agent PR Gate` still observes a passing `Backend Unit Tests` check.
- Artifact names are unique and understandable per shard.

Risk controls:

- Keep the existing serial command available locally and for fallback.
- If shard imbalance appears after several weeks, refresh the duration file.
- Do not edit `.ci-ignore-paths` behavior as part of sharding.

### Phase 2: Trial `pytest-xdist` for Local and Optional CI Speedups

Goal: see whether vertical parallelism is safe enough to use, especially in
local/Symphony workflows.

Implementation trial:

- Add `pytest-xdist` to the unit image.
- Run experiments in Incus:
  - `python -m pytest tests/unit -n auto --dist loadscope`
  - `python -m pytest tests/unit -n 2 --dist loadfile`
  - `python -m pytest tests/unit -n auto --dist worksteal`
- Compare runtime and flake rate against the serial run.

Decision rule:

- If xdist is stable and significantly faster, use it inside each shard with a
  small worker count, or use it locally/Symphony only.
- If xdist exposes fixture/global-state coupling, keep CI on matrix sharding and
  use the failures as a test isolation cleanup backlog.

Risk controls:

- Prefer `loadscope` or `loadfile` before default `load` if fixture reuse or
  module state looks fragile.
- Start with `-n 2`, not `-n auto`, on GitHub runners.

### Phase 3: Add Affected-Test Fast Feedback, Not as the Main Gate

Goal: give agents and humans faster preflight checks without reducing the
required PR confidence.

Candidate options:

- `pytest-testmon`: dependency-aware affected-test selection based on prior
  coverage data.
- `pytest-picked`: Git changed-file based test selection.
- Repo-owned mapping: changed backend package path -> known focused test paths.

Recommendation:

- Start with a repo-owned focused-test helper for common domains, because we
  already have strong path manifests and agent workflows.
- Consider `pytest-testmon` later for local/Symphony loops only.
- Do not make affected-test selection the only PR gate until we have evidence it
  catches shared domain-pack and validator regressions reliably.

Example use:

- Agent PR Gate can run a changed-scope unit slice before the full unit matrix.
- Symphony agents can use the focused helper for quick feedback before pushing.

### Phase 4: Reduce Repeated Image-Build Overhead

Goal: avoid paying similar Docker build costs in multiple jobs. This phase may
move earlier if Phase 0 shows Docker image setup is a major part of the 8-minute
runtime or if the sharding prototype repeats image setup per shard.

Current state:

- `Agent PR Gate`, `Backend Unit Tests`, and `Backend Persistence Tests` can all
  build or load the lightweight unit test image.
- Buildx GHA caching helps, but each job still has independent setup and load
  overhead.

Options:

- Publish a reusable unit-test image keyed by lockfile/Dockerfile hash for PR
  jobs to pull.
- Split workflows so one image-build job produces an artifact or registry image
  consumed by dependent jobs.
- Keep Buildx caching only if measured image build/load time is small relative
  to pytest runtime.

Recommendation:

- Measure image build/load time separately before changing this. If pytest is
  the dominant cost, sharding gives a better return. If image setup is a large
  cost, treat image reuse as part of the sharding rollout rather than a later
  cleanup.

### Phase 5: Tune Coverage Cost

Goal: avoid spending PR time on report formats that are not used every run.

Options:

- Keep XML and terminal coverage on PRs, but generate HTML coverage only on
  `main` or nightly.
- Keep HTML generation only in the coverage-combine job after sharding.
- Lower verbosity from `-v` if log volume becomes a meaningful cost.

Recommendation:

- Do not remove coverage yet. First measure whether report generation is a
  meaningful part of the 8-minute runtime.
- If it is, keep XML for CI integrations and move HTML to main/nightly.

## Non-Recommended Changes

- Do not replace pytest with `green`; the repo already depends heavily on
  pytest fixtures, markers, pytest-cov, pytest-asyncio, and existing scripts.
- Do not adopt Keploy for unit-test speed. It may be useful for API replay or
  integration test generation later, but it does not directly solve this unit
  suite runtime problem.
- Do not make changed-file-only testing the sole PR gate for broad backend or
  domain-pack changes.
- Do not introduce xdist as a hard gate before proving fixture isolation.

## Proposed Ticket Breakdown

### Ticket 1: Backend Unit Timing Telemetry

Scope:

- Add slow-test duration output to backend unit CI.
- Add JUnit or another machine-readable timing artifact.
- Split CI summary timing between image build/load, validation, pytest, and
  coverage/report generation.
- Preserve current serial execution and coverage behavior.
- Document how to interpret the slow-test section.

Acceptance:

- Backend unit CI reports slowest tests for every PR.
- CI output makes clear whether the 8-minute cost is mostly Docker setup,
  pytest execution, coverage generation, or a small number of slow test modules.
- The sharding prototype has a reliable way to seed full duration data.
- No test selection or coverage semantics change.

### Ticket 2: Backend Unit Sharding Prototype

Scope:

- Add pinned `pytest-split` support in the backend test dependency path used by
  local/Symphony and CI unit runs.
- Add shard options to `run_ci_unit_tests.sh`.
- Add a 2-shard GitHub Actions matrix behind an env/config toggle or a draft
  workflow branch.
- Seed or commit a full test-duration file.
- Measure wall time, image build/load cost, shard balance, and failure behavior.
- Keep the existing required `Backend Unit Tests` check name stable through an
  aggregate job or an explicitly documented temporary prototype branch.

Acceptance:

- 2-shard run is reproducible locally.
- Shard failures are easy to identify.
- Runtime improves enough to justify 4-shard rollout.
- `Agent PR Gate` does not wait forever because of renamed/matrixed checks.

### Ticket 3: Coverage Combine for Sharded Unit Runs

Scope:

- Add `.coveragerc` or equivalent configuration for Docker/workspace path
  mapping.
- Produce uniquely named per-shard coverage data files with `COVERAGE_FILE`.
- Upload uniquely named shard artifacts.
- Combine coverage in a final job.
- Preserve XML/html artifacts and current threshold.
- Enforce coverage threshold only once, after combine.

Acceptance:

- Combined coverage report exists.
- Current coverage threshold is enforced once, after all shards complete.
- Coverage paths map correctly from container paths to workspace paths.

### Ticket 4: CI Orchestration and Agent Gate Compatibility

Scope:

- Preserve or intentionally migrate required check names used by
  `.github/workflows/agent-pr-gate.yml`.
- Add the non-matrix `Backend Unit Tests` aggregate job if backend unit shards
  are matrixed.
- Confirm branch protection, Agent PR Gate, and any dashboards observe the
  aggregate check rather than individual shard names.
- Document failed-shard reproduction with exact commands.

Acceptance:

- Agent PR Gate completes normally when backend unit tests are sharded.
- The final required check name remains stable.
- A failed shard points developers to a direct local reproduction command.

### Ticket 5: 4-Shard Rollout and Duration Refresh

Scope:

- Move from prototype to default PR backend unit matrix.
- Commit or refresh `.test_durations`.
- Add a documented refresh command.
- Decide whether image reuse is required before 4 shards based on prototype
  measurements.

Acceptance:

- Backend unit PR wall time is below 5 minutes on representative PRs.
- Shards are reasonably balanced.
- Main/develop push behavior remains at least as strict as PR behavior.

### Ticket 6: Optional Xdist Trial

Scope:

- Add `pytest-xdist` to local/Symphony tooling or the unit image.
- Run controlled Incus trials with `-n 2`, `loadscope`, `loadfile`, and
  `worksteal`.
- Record flake/isolation findings.

Acceptance:

- Clear decision: use xdist in CI, use only locally, or defer pending fixture
  cleanup.

## Open Questions

- Is the 8-minute number only the `Backend Unit Tests` pytest step, or the whole
  job including Docker build/load?
- Do we need PR HTML coverage artifacts, or are XML and terminal summaries
  enough?
- Should full serial unit coverage still run on `main` as a backstop during the
  sharding rollout?
- How many concurrent GitHub jobs are acceptable for this repository? Four
  shards should reduce wall time but consume more runner capacity.
- Does branch protection require exact job names, and does it currently require
  `Backend Unit Tests` specifically?
- Is it acceptable to publish PR-scoped unit-test images to GHCR if repeated
  Docker build/load costs are high?

## Recommended Next Step

Implement the tactical first pass first: timing visibility plus conservative
in-place xdist. Use the next few CI runs to decide whether that is enough. If it
is stable but still too slow, proceed to Ticket 1/Ticket 2 data gathering for
sharding, fixture optimization, or image reuse.

# Symphony Flow And Optimization

This document is a code-grounded walkthrough of the current Symphony runtime in `agr_ai_curation`.

It is based on the live implementation in `.symphony/WORKFLOW.md`, `.symphony/elixir/`, and the `scripts/utilities/symphony_*.sh` helper layer as of 2026-04-21.

Note: this guide supersedes some stale values in `.symphony/ARCHITECTURE.md`. For example, the current workflow sets `agent.max_turns: 60`, `max_concurrent_agents: 6`, and `codex.stall_timeout_ms: 600000`.

## 1. Executive Summary

Symphony is a Linear-driven state machine with three distinct control layers:

1. The Elixir orchestrator decides when an issue should run, when a run should stop, and when retries or cleanup should happen.
2. `WORKFLOW.md` defines the policy surface: active states, concurrency, hooks, Codex runtime settings, and the large prompt that tells the agent how each lane should behave.
3. The shell helper layer does most of the actual lane work: gathering context, updating the persistent workpad comment, checking PRs, preparing human review, and moving issues between Linear states.

The important design point is this:

- The orchestrator does not know the lane semantics in detail.
- The prompt plus helper scripts do.
- Most lane changes are agent-driven by calling `scripts/utilities/symphony_linear_issue_state.sh`.
- Some lane changes are human-driven in Linear.
- A few are orchestrator-driven safety moves, especially stall-to-`Blocked` and terminal cleanup.

## 2. Startup And Hot Reload

### Runtime bootstrap

`./.symphony/run.sh` prepares the environment before Elixir starts:

- exports repo/workspace/runtime paths such as `SYMPHONY_SOURCE_REPO`, `SYMPHONY_WORKSPACE_ROOT`, `SYMPHONY_LOCAL_SOURCE_ROOT`, and `SYMPHONY_REVIEW_HOST`
- decrypts the private app `.env` into `/dev/shm` when the local vault is available
- loads the repo-scoped GitHub PAT helper from `.symphony/github_pat_env.sh`
- materializes Linear auth if needed
- creates the workspace/log roots before boot

That means the Elixir app mostly assumes env and auth are already ready by the wrapper.

### Workflow hot reload

`WorkflowStore` keeps the last known good workflow in memory and polls `WORKFLOW.md` every second. It reloads on content change and keeps the old version if parsing fails.

Source:

- `.symphony/elixir/lib/symphony_elixir/workflow_store.ex`
- `.symphony/elixir/lib/symphony_elixir/workflow.ex`

Operational consequence:

- config changes in `WORKFLOW.md` are live-ish
- prompt changes are also live-ish
- broken YAML or template edits do not immediately brick the running server if an older valid copy is already loaded

## 3. The Real End-To-End Flow

### Step 1: poll timer fires

Every `polling.interval_ms` the orchestrator runs a cycle. The current workflow sets that to 10 seconds.

The orchestrator:

1. refreshes runtime config from `WORKFLOW.md`
2. reconciles running issues
3. validates the workflow/config
4. fetches candidate Linear issues in active states
5. sorts them by priority, then creation time
6. dispatches eligible issues until global and per-state slots are full

Source:

- `.symphony/elixir/lib/symphony_elixir/orchestrator.ex`
- `.symphony/WORKFLOW.md`

### Step 2: candidate issue gating

An issue is dispatchable only if all of these are true:

- it is in an active state from `WORKFLOW.md`
- it is not already claimed or running
- global slots are available
- per-state slots are available
- it is still routable to this worker
- if it is in `Todo`, all `blockedBy` issues are terminal

Important nuance:

- blocker gating only happens automatically for `Todo`
- once a ticket is manually moved into another active lane, the orchestrator will not reapply the same dependency gate

### Step 3: dispatch creates or reuses the issue workspace

`AgentRunner.run/3` calls `Workspace.create_for_issue/1`.

Workspace behavior:

- workspace path is derived from the issue identifier
- a fresh workspace runs the `after_create` hook from `WORKFLOW.md`
- that hook clones the repo and runs `scripts/utilities/symphony_ensure_workspace_runtime.sh`
- every run then executes the `before_run` hook, which guards that the workspace still matches the expected repo/ref/runtime state

If the runtime overlay is missing, the run is blocked before coding starts and the issue is moved to `Blocked`.

### Step 4: Codex session starts

`AppServer.start_session/2` launches `codex app-server` inside the workspace and opens a JSON-RPC session over stdio.

Current behavior:

- thread sandbox is `danger-full-access`
- turn sandbox policy is also `dangerFullAccess`
- approval policy is `never`, so command/apply-patch/file-change approvals are auto-approved
- the only dynamic tool exposed by Symphony itself is `linear_graphql`

In practice, most routine Linear work is still supposed to use the shell helpers, not the dynamic tool.

### Step 5: first-turn prompt is built

`PromptBuilder.build_prompt/2` starts from the `WORKFLOW.md` prompt body, narrows the detailed `State-specific operating mode` section to the current lane, then renders the result with Liquid variables from the current Linear issue.

The first-turn prompt currently includes:

- repository startup rules
- development doctrine
- testing rules
- execution/repository/blocked rules
- the full lane state machine
- the current lane's detailed operating-mode instructions
- issue identifier/title/state/url/labels/delivery mode
- the latest non-workpad comment
- the full issue description

For `workflow:no-pr` tickets, `AgentRunner` also appends extra delivery-mode guidance after rendering the workflow prompt.

### Step 6: the agent runs the lane

Symphony expects the agent to use the helper layer as the operational API.

The general lane pattern is:

1. read context
2. do the lane’s work
3. update the persistent workpad comment first
4. change Linear state second
5. stop the run

The prompt explicitly warns that after a successful state transition the session is expected to end immediately.

### Step 7: completion, retry, or stop

When a task exits normally, the orchestrator does not assume the ticket is done. It schedules a short continuation check:

- if the issue is still in the same active state, it may be redispatched
- if the state changed, the run ends and the next lane will be picked up fresh

When a task crashes:

- Symphony schedules exponential-backoff retries

When a task stalls:

- if no Codex activity is observed for `codex.stall_timeout_ms`, the run is terminated
- after `codex.stall_restart_limit` consecutive stall restarts, Symphony moves the issue to `Blocked` and writes a blocker comment

### Step 8: external state changes are authoritative

If a human or another automation moves the Linear issue while Symphony is running:

- terminal state: the orchestrator kills the worker and cleans the workspace
- other non-active state: the orchestrator kills the worker and preserves the workspace
- different active lane: the orchestrator kills the worker so the issue can be redispatched under fresh lane instructions

That is what prevents a single long-running session from silently spanning multiple lanes.

## 4. What Actually Changes Lanes

### Agent-driven transitions

These are the normal path.

The agent:

1. writes a specific section into the persistent workpad comment
2. calls `scripts/utilities/symphony_linear_issue_state.sh`
3. exits

This is how most lane changes happen:

- `Todo -> In Progress`
- `In Progress -> Needs Review`
- `Needs Review -> In Review`
- `In Review -> In Progress`
- `In Review -> Ready for PR`
- `In Review -> Human Review Prep`
- `Ready for PR -> In Progress`
- `Ready for PR -> Human Review Prep`
- `Ready for PR -> Blocked`
- `Human Review Prep -> Human Review`
- `Finalizing -> Done`
- `Finalizing -> In Progress`
- `Finalizing -> Blocked`

### Human-driven transitions

These enter through Linear, not through the orchestrator:

- human sends `Human Review -> In Progress`
- human sends `Human Review -> Finalizing`
- human cancels/closes/duplicates/blocks a ticket

The orchestrator only observes and reacts to those changes on the next poll/reconcile cycle.

### Orchestrator-driven transitions

These are safety paths, not the main workflow:

- repeated stalls can move the issue to `Blocked`
- missing workspace runtime files can move the issue to `Blocked`
- terminal cancellation states can trigger PR close + workspace cleanup

## 5. Lane-By-Lane Flow

| Lane | Primary helper(s) | Main job | Normal next state(s) |
|---|---|---|---|
| `Todo` | `symphony_issue_branch.sh`, `symphony_linear_workpad.sh`, `symphony_linear_issue_state.sh` | Intake only. Create/switch issue branch, write `Todo Handoff`, stop. | `In Progress` or `Blocked` |
| `In Progress` | `symphony_in_progress.sh`, `symphony_linear_workpad.sh`, `symphony_linear_issue_state.sh` | Implement, validate, write `Review Handoff`. | `Needs Review` or `Blocked` |
| `Needs Review` | `symphony_linear_workpad.sh`, `symphony_linear_issue_state.sh` | Claim-only reviewer handoff, write `Review Claim`. | `In Review` |
| `In Review` | `symphony_in_review.sh`, optionally `symphony_claude_review_loop.sh`, then `symphony_linear_workpad.sh`, `symphony_linear_issue_state.sh` | Review only. Record `Review Outcome`. | `In Progress`, `Ready for PR`, or `Human Review Prep` |
| `Ready for PR` | `symphony_ready_for_pr.sh`, `gh`, optionally `symphony_claude_review_loop.sh`, then workpad/state helpers | Create/find PR, watch checks, consume Claude review, record `PR Handoff`. | `In Progress`, `Human Review Prep`, or `Blocked` |
| `Human Review Prep` | `symphony_human_review_prep.sh`, optionally `symphony_claude_review_loop.sh`, then workpad/state helpers | Best-effort local review environment prep, record `Human Review Handoff`. | `Human Review` or `Blocked` |
| `Human Review` | no coding helper; human changes state in Linear | Wait state. The agent is effectively idle here. | human moves to `In Progress` or `Finalizing` |
| `Finalizing` | `symphony_finalize_issue.sh`, then workpad/state helpers | Merge/cleanup/reporting, record `Finalization Summary`. | `Done`, `In Progress`, or `Blocked` |

### The workpad sections are the baton between lanes

Required canonical sections:

- `Todo Handoff`
- `Review Handoff`
- `Review Claim`
- `Review Outcome`
- `PR Handoff`
- `Human Review Handoff`
- `Finalization Summary`

The workpad helper replaces a section with the same `## Heading` instead of appending duplicates. That keeps the baton compact and means the next lane reads the latest version of each section, not an ever-growing pile of stale copies.

Source:

- `scripts/utilities/symphony_linear_workpad.sh`

## 6. Where Context Comes From

Symphony uses three overlapping context channels.

### Channel 1: the first-turn workflow prompt

This is the biggest single prompt payload.

Current size of `.symphony/WORKFLOW.md`:

- front matter: 127 lines, 381 words
- prompt body: 312 lines, 4,543 words
- full file: 440 lines, 4,925 words

That full body is the source material for first-turn dispatch prompts, but the runtime now narrows the detailed `State-specific operating mode` section to the active lane before rendering. On a measured `In Progress` sample, that reduced the rendered first-turn prompt from 4,484 words with 8 lane blocks to 2,231 words with 1 lane block.

### Channel 2: the persistent Linear workpad

This is the official handoff baton.

Characteristics:

- one persistent comment
- stable marker-based identification
- latest non-workpad guidance comment tracked separately
- section replacement keeps the baton concise

This is a strong design choice. It prevents hidden state in terminal output and makes lane handoffs inspectable in Linear.

### Channel 3: lane-specific brief files

`In Progress` and `In Review` do not rely only on the initial prompt. They build markdown brief files from:

- the normalized Linear context helper
- the issue description
- comment bodies
- issue history
- PR state/checks
- latest Claude review content

That means the real runtime context model is:

- big static first-turn prompt
- plus workpad baton
- plus lane brief file

This is powerful, but it is also the main source of prompt/context bloat.

## 7. Optimization Audit

### 7.1 Highest-value changes

### 1. Keep trimming the monolithic first-turn prompt by lane

Why it matters:

- the first-turn prompt body alone is about 4,543 words
- it includes instructions for every lane even though the issue is only in one lane
- `In Progress` and `In Review` then fetch still more context through helper-generated brief files

What is happening now:

- `PromptBuilder` now keeps the shared workflow sections but collapses the detailed `State-specific operating mode` block to only the current lane before rendering
- that removes the biggest lane-specific duplication, but the prompt still carries shared summaries that mention every lane
- `In Progress` and `In Review` still add helper-generated briefs on top of that base prompt

Recommendation:

- keep the current active-lane narrowing
- if more reduction is needed, split additional multi-lane sections into explicit `shared + current_lane` fragments instead of carrying every lane summary in the base prompt

Expected benefit:

- first-turn token cost already drops materially with the active-lane reducer
- less instruction collision
- less chance of the model mentally carrying rules from the wrong lane

### 2. Keep the brief helpers honest about fetched context limits

Why it matters:

- `symphony_linear_issue_context.sh` defaults to `--comments-first 50` and `--history-first 50`
- `symphony_in_progress.sh` and `symphony_in_review.sh` now foreground `Current Handoff Signals`, but their fetched comment/history slices are still capped by the shared context helper defaults

This is both an optimization and an accuracy problem.

Recommendation:

- either paginate until exhausted for the few lanes that truly need everything
- or switch the brief builders to targeted context slices:
  - latest workpad
  - latest non-workpad guidance comment
  - current lane handoff section
  - latest Claude review summary
  - latest failing checks

Expected benefit:

- smaller briefs on long-lived tickets
- fewer “needle in a haystack” review passes
- fewer misleading claims about complete context

### 3. Make Claude feedback disposition vocabulary consistent

This inconsistency was cleaned up in the current local runtime pass, but it was a real source of drift:

- `In Progress` instructions say use `fixed` or `not taken`, and explicitly say not to use `deferred`
- `Ready for PR` still tells the agent to write `fixed`, `deferred`, or `not taken`
- `symphony_ready_for_pr.sh` also says not to use `deferred`

Recommendation:

- pick one vocabulary and use it everywhere
- the simplest choice is probably:
  - `fixed`
  - `not taken: <reason>`

Expected benefit:

- cleaner workpad parsing
- less agent indecision in Claude feedback loops
- easier human scanning during `Human Review Prep`

### 7.2 Medium-value changes

### 4. Reduce duplicated `no_pr` semantics

`no_pr` behavior currently appears in multiple layers:

- workflow state machine
- `Ready for PR` lane rules
- `AgentRunner.append_delivery_mode_guidance/2`
- helper behavior in `symphony_ready_for_pr.sh`
- helper behavior in `symphony_finalize_issue.sh`

The redundancy is intentional for safety, but it is now large enough to create maintenance drift.

Recommendation:

- keep the helper behavior authoritative
- keep only a short prompt reminder
- avoid repeating the full `no_pr` branch logic in several places

### 5. Be careful with workpad section replacement on repeated cycles

Current behavior:

- the workpad helper replaces an existing `## <Section>` block instead of appending another copy

That is good for compactness, but it means round-by-round reasoning can disappear unless the agent manually carries it forward.

This is most noticeable for:

- `Claude Feedback Disposition`
- `Claude Loop Decision`
- repeated `Review Outcome` cycles

Recommendation:

- keep replacement for canonical baton sections
- consider a compact rolling subsection for repeated review rounds, for example:
  - `### Round 1`
  - `### Round 2`

### 6. Consider narrowing the `In Review` brief

The current review brief includes:

- full issue description
- all fetched comments
- latest Claude PR review

That is thorough, but the reviewer usually needs:

- acceptance criteria
- the latest workpad handoff
- latest human comment
- latest Claude findings
- changed files and current PR state

Recommendation:

- keep full context available on demand
- default the generated review brief to the current baton plus the latest external feedback

### 7.3 Lower-value changes

### 7. Cache the parsed Liquid template, not just the raw file

`WorkflowStore` caches the loaded workflow file, but `PromptBuilder` still reparses the Liquid template on every prompt build.

This is small compared to model latency, so it is not urgent, but it is easy cleanup.

### 8. Consider file-watch reload instead of 1-second polling

`WorkflowStore` currently polls every second and hashes the entire file contents.

This is not the main runtime cost in Symphony, so this should stay low priority.

## 8. What Is Already Working Well

Several choices here are strong and should be preserved:

- External Linear state is authoritative, and the orchestrator kills mismatched active runs instead of letting one session drift across lanes.
- The persistent workpad comment is a good baton. It makes handoffs inspectable and avoids losing state in terminal output.
- Stall handling is bounded. Repeated stalls do not loop forever.
- Workspace bootstrap and guard hooks block bad runs early instead of letting agents code in a broken sandbox.
- Human feedback enters the next run quickly because the latest non-workpad comment is injected directly into the first-turn prompt.

## 9. Recommended Next Moves

If you want to improve Symphony without changing its overall workflow model, I would do the work in this order:

1. Split the prompt into shared rules plus lane-specific fragments.
2. Tighten the context helpers so `In Progress` and `In Review` do not dump broad history by default.
3. Normalize Claude disposition vocabulary and section structure.
4. Decide whether repeated review-round history should be compactly retained instead of overwritten.

## 10. Source Map

Primary runtime sources:

- `.symphony/WORKFLOW.md`
- `.symphony/run.sh`
- `.symphony/elixir/lib/symphony_elixir/orchestrator.ex`
- `.symphony/elixir/lib/symphony_elixir/agent_runner.ex`
- `.symphony/elixir/lib/symphony_elixir/prompt_builder.ex`
- `.symphony/elixir/lib/symphony_elixir/workflow_store.ex`
- `.symphony/elixir/lib/symphony_elixir/codex/app_server.ex`
- `scripts/utilities/symphony_linear_issue_context.sh`
- `scripts/utilities/symphony_linear_workpad.sh`
- `scripts/utilities/symphony_linear_issue_state.sh`
- `scripts/utilities/symphony_in_progress.sh`
- `scripts/utilities/symphony_in_review.sh`
- `scripts/utilities/symphony_ready_for_pr.sh`
- `scripts/utilities/symphony_human_review_prep.sh`
- `scripts/utilities/symphony_finalize_issue.sh`

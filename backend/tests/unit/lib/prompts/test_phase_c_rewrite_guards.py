"""Phase C base-prompt rewrite guards (no DB).

These guards protect the 26 loss-full Phase C base-prompt rewrites. They run with
NO database: the assembled prompt text is built straight from the files on disk
via ``phase_c_harness.assembled_prompt_text`` (locked core layers +
resolved ``prompt.yaml`` content + optional group rules), never via the
DB-backed prompt cache or the monkeypatched fake content the assembly unit tests
inject.

What each guard does:

* **Retention** — every phrase in every ``<agent>[.<group>].txt`` inventory file
  must appear in the real assembled render (rendered with that group when a
  ``.<group>`` suffix is present).
* **Workflow invariants** — every phrase in ``<agent>.invariants.txt`` must
  appear (reusing the retention assertion) AND in the file's declared order by
  first occurrence, so an evidence -> stage -> finalize sequence cannot be
  scrambled.
* **Dropped-list machine check** — for each ``relocated`` entry in
  ``<agent>.dropped.json`` the phrase (or a declared synonym) must appear in its
  declared ``new_home`` (the render, a bindings.yaml tool description, or
  get_agent_contract output); ``deleted`` entries are not asserted but are
  printed so silent deletions can't hide in the diff.
* **Config-divergence** — any agent with a ``prompt.yaml`` in BOTH
  ``config/agents`` and ``packages`` must keep them byte-identical (chat_output),
  unless explicitly allowlisted (initially: none).
* **Render smoke** — every agent's locked core prompt renders without error, and
  the full assembled text builds.
* **Contradiction dump** — every MUST/NEVER/ALWAYS line per agent is printed for
  the human reviewer (report-only).

The "prove it" canary (``test_planted_missing_phrase_is_detected``) asserts the
retention guard is non-vacuous: a deliberately-absent phrase is detected as
missing.
"""

from __future__ import annotations

import pytest

from tests.unit.lib.prompts import phase_c_harness as harness


# ---------------------------------------------------------------------------
# Retention guard (with group dimension)
# ---------------------------------------------------------------------------

_INVENTORY_FILES = harness.iter_inventory_files()


def _inventory_id(inventory: harness.InventoryFile) -> str:
    return inventory.path.name


@pytest.mark.skipif(not _INVENTORY_FILES, reason="no Phase C inventory files seeded yet")
@pytest.mark.parametrize("inventory", _INVENTORY_FILES, ids=[_inventory_id(i) for i in _INVENTORY_FILES])
def test_inventory_phrases_are_retained(inventory: harness.InventoryFile):
    """Every load-bearing phrase survives in the real no-DB assembled render."""
    assert inventory.phrases, f"{inventory.path.name} has no phrases to check"
    rendered = harness.assembled_prompt_text(inventory.agent_key, inventory.group_id)

    missing = [phrase for phrase in inventory.phrases if phrase not in rendered]
    group_note = f" (group={inventory.group_id})" if inventory.group_id else ""
    assert not missing, (
        f"Phase C retention failure for '{inventory.agent_key}'{group_note}: "
        f"{len(missing)} inventory phrase(s) absent from the assembled render. "
        f"Either the rewrite dropped a load-bearing rule (restore it) or the "
        f"phrase legitimately moved (record it in {inventory.agent_key}.dropped.json "
        f"as relocated with a verified new_home). Missing: {missing}"
    )


# ---------------------------------------------------------------------------
# Workflow-invariant guard (retention + ordering)
# ---------------------------------------------------------------------------

_INVARIANT_AGENTS = tuple(
    agent_key
    for agent_key in harness.all_agent_keys()
    if harness.load_invariants(agent_key)
)


@pytest.mark.skipif(not _INVARIANT_AGENTS, reason="no Phase C invariants seeded yet")
@pytest.mark.parametrize("agent_key", _INVARIANT_AGENTS)
def test_workflow_invariants_survive_in_order(agent_key: str):
    """Ordered workflow steps survive AND keep their workflow order.

    REQUIREMENT for invariant authors: each phrase in `<agent>.invariants.txt`
    MUST occur exactly once in the assembled render and be listed in workflow
    order. The ordering check below uses first-occurrence positions, so a phrase
    that appears more than once could spuriously satisfy ordering against a
    later step — therefore this test FAILS loudly if any invariant phrase occurs
    more than once. Pick verbatim phrases from the canonical ordered workflow
    block (where each step occurs exactly once), not generic tokens like a tool
    name that recurs in success-criteria or tool-list prose.
    """
    invariants = harness.load_invariants(agent_key)
    rendered = harness.assembled_prompt_text(agent_key)

    # Retention: every invariant phrase must be present (reuses retention logic).
    missing = [phrase for phrase in invariants if phrase not in rendered]
    assert not missing, (
        f"Phase C workflow-invariant retention failure for '{agent_key}': "
        f"{missing}"
    )

    # Uniqueness: the ordering check assumes one occurrence per phrase. A phrase
    # that recurs could spuriously satisfy ordering, so reject duplicates loudly.
    duplicated = [
        f"{phrase!r} (x{count})"
        for phrase in invariants
        if (count := rendered.count(phrase)) > 1
    ]
    assert not duplicated, (
        f"Phase C workflow-invariant uniqueness failure for '{agent_key}': each "
        f"invariant phrase must occur exactly once in the render so the ordering "
        f"check is meaningful, but these recur: {duplicated}. Choose a more "
        f"specific verbatim phrase from the ordered workflow block."
    )

    # Ordering: first occurrences must be strictly increasing (workflow order).
    positions = [(phrase, rendered.find(phrase)) for phrase in invariants]
    out_of_order = [
        positions[i][0]
        for i in range(1, len(positions))
        if positions[i][1] <= positions[i - 1][1]
    ]
    assert not out_of_order, (
        f"Phase C workflow-invariant ordering failure for '{agent_key}': these "
        f"steps appear out of declared workflow order: {out_of_order}. "
        f"First-occurrence positions: {positions}"
    )


# ---------------------------------------------------------------------------
# Reason-code survival framework
# ---------------------------------------------------------------------------

_REASON_CODE_AGENTS = tuple(
    agent_key
    for agent_key in harness.all_agent_keys()
    if harness.load_inventory(agent_key, None)
    and (harness.INVENTORY_DIR / f"{agent_key}.reason_codes.txt").exists()
)


@pytest.mark.skipif(
    not _REASON_CODE_AGENTS,
    reason=(
        "no Phase C reason-code inventories seeded yet; canonical reason codes are "
        "sourced from the domain pack (export/conversion modules) during each "
        "agent rewrite, not by grepping the prompt"
    ),
)
@pytest.mark.parametrize("agent_key", _REASON_CODE_AGENTS)
def test_reason_codes_survive(agent_key: str):
    """Every canonical reason code listed for an agent survives in the render."""
    reason_codes = harness._read_phrase_lines(
        harness.INVENTORY_DIR / f"{agent_key}.reason_codes.txt"
    )
    rendered = harness.assembled_prompt_text(agent_key)
    missing = [code for code in reason_codes if code not in rendered]
    assert not missing, (
        f"Phase C reason-code survival failure for '{agent_key}': {missing}"
    )


# ---------------------------------------------------------------------------
# Dropped-list machine check
# ---------------------------------------------------------------------------

_DROPPED_AGENTS = harness.agents_with_dropped_lists()


@pytest.mark.skipif(not _DROPPED_AGENTS, reason="no Phase C dropped-lists seeded yet")
@pytest.mark.parametrize("agent_key", _DROPPED_AGENTS)
def test_relocated_dropped_entries_have_verified_homes(agent_key: str):
    """Each relocated phrase actually appears in its declared new_home.

    This closes the "drop a rule and add it to the dropped-list" gaming hole: a
    relocated entry whose home check fails is a test failure, not a free pass.
    """
    entries = harness.load_dropped(agent_key)
    relocated = [entry for entry in entries if entry.category == "relocated"]

    failures: list[str] = []
    for entry in relocated:
        ok, _home_text = harness.relocated_phrase_satisfied(entry)
        if not ok:
            failures.append(
                f"  - phrase {entry.phrase!r} (synonyms={list(entry.synonyms)}) "
                f"NOT found in new_home {entry.new_home!r}"
            )

    assert not failures, (
        f"Phase C dropped-list home check failed for '{agent_key}': a relocated "
        f"rule was claimed to move but is absent from its declared home. Either "
        f"restore the rule, fix the new_home/synonym, or reclassify as deleted "
        f"with justification.\n" + "\n".join(failures)
    )


def test_deleted_dropped_entries_are_reported_for_review(capsys):
    """Print the full deleted-with-no-home list so deletions surface in review."""
    deleted_rows: list[str] = []
    for agent_key in _DROPPED_AGENTS:
        for entry in harness.load_dropped(agent_key):
            if entry.category != "deleted":
                continue
            if entry.new_home.strip():
                pytest.fail(
                    f"deleted entry for '{agent_key}' phrase {entry.phrase!r} "
                    f"must have an empty new_home (got {entry.new_home!r}); use "
                    f"category=relocated if it has a home."
                )
            deleted_rows.append(
                f"  [{agent_key}] {entry.phrase!r}\n"
                f"      reason: {entry.reason}\n"
                f"      old_source: {entry.old_source}"
            )

    # Print INSIDE capsys.disabled() so the dump surfaces on every (green) run,
    # not just on failure or under -s. This is the human-review affordance.
    with capsys.disabled():
        print("\n=== Phase C DELETED rules (no home — review required) ===")
        if deleted_rows:
            print("\n".join(deleted_rows))
        else:
            print("  (none)")


# ---------------------------------------------------------------------------
# Config-divergence guard
# ---------------------------------------------------------------------------

# Agents whose config/agents prompt.yaml is intentionally allowed to differ from
# its packages copy. Initially empty: chat_output (the only dual-tree agent) must
# stay byte-identical. supervisor/curation_prep are config-only and not pairs.
_CONFIG_DIVERGENCE_ALLOWLIST: frozenset[str] = frozenset()

_DUAL_TREE_PAIRS = harness.config_packages_prompt_pairs()


@pytest.mark.skipif(not _DUAL_TREE_PAIRS, reason="no dual-tree (config+packages) agents found")
@pytest.mark.parametrize("pair", _DUAL_TREE_PAIRS, ids=[pair[0] for pair in _DUAL_TREE_PAIRS])
def test_config_and_packages_prompts_are_identical(pair):
    """A dual-tree agent's config + packages prompt.yaml must be byte-identical."""
    agent_key, config_prompt, packages_prompt = pair
    if agent_key in _CONFIG_DIVERGENCE_ALLOWLIST:
        pytest.skip(f"{agent_key} is allowlisted to diverge")

    config_bytes = config_prompt.read_bytes()
    packages_bytes = packages_prompt.read_bytes()
    assert config_bytes == packages_bytes, (
        f"Phase C config-divergence guard: '{agent_key}' prompt.yaml differs "
        f"between config/agents and packages. A dual-tree agent must be edited in "
        f"BOTH trees in lockstep (an accidental single-tree edit is the bug this "
        f"guard catches). Sizes: config={len(config_bytes)}B "
        f"packages={len(packages_bytes)}B."
    )


# ---------------------------------------------------------------------------
# Render smoke
# ---------------------------------------------------------------------------

_ALL_AGENTS = harness.all_agent_keys()


@pytest.mark.parametrize("agent_key", _ALL_AGENTS)
def test_core_prompt_renders_without_error(agent_key: str):
    """Every agent's locked core prompt renders with no DB and no error."""
    from src.lib.prompts.assembly import build_agent_core_prompt

    bundle = build_agent_core_prompt(agent_key)
    rendered = bundle.render()
    assert isinstance(rendered, str)
    assert "## Platform Runtime Contract" in rendered


@pytest.mark.parametrize("agent_key", _ALL_AGENTS)
def test_assembled_prompt_text_builds(agent_key: str):
    """The full no-DB assembled text builds for every agent (core + base)."""
    rendered = harness.assembled_prompt_text(agent_key)
    assert isinstance(rendered, str)
    assert rendered.strip(), f"empty assembled render for {agent_key}"


def test_custom_agent_render_path_smoke():
    """Custom (curator-cloned) agent render reuses the catalog runtime-instructions path.

    The custom-agent runtime-instruction builder reads live catalog/DB state, so a
    real custom render needs a DB. This guard documents that dependency rather than
    forcing a fake; the curator-clone render is exercised by the Agent Studio
    integration suite with a DB. Skipped here on purpose (no-DB harness).
    """
    pytest.skip(
        "custom-agent render needs catalog/DB state; covered by the Agent Studio "
        "integration suite (this Phase C harness is intentionally no-DB)"
    )


# ---------------------------------------------------------------------------
# Contradiction dump (report-only)
# ---------------------------------------------------------------------------


def test_directive_contradiction_dump(capsys):
    """Print every MUST/NEVER/ALWAYS line per agent for human review (report-only).

    Current prompts carry ~zero such tokens; Phase C introduces a small number for
    true invariants. This dump lets the reviewer scan them for contradictions; it
    never fails (no automated semantic detection).
    """
    lines_out: list[str] = []
    for agent_key in _ALL_AGENTS:
        rendered = harness.assembled_prompt_text(agent_key)
        directives = harness.directive_lines(rendered)
        if directives:
            lines_out.append(f"[{agent_key}] {len(directives)} directive line(s):")
            lines_out.extend(f"    {line}" for line in directives)

    # Print INSIDE capsys.disabled() so the dump surfaces on every (green) run,
    # not just on failure or under -s. This is the human-review affordance.
    with capsys.disabled():
        print("\n=== Phase C MUST/NEVER/ALWAYS directive dump (review for contradictions) ===")
        if lines_out:
            print("\n".join(lines_out))
        else:
            print("  (no MUST/NEVER/ALWAYS directives in any agent prompt)")


# ---------------------------------------------------------------------------
# Prove-it: the retention guard is non-vacuous
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="planted-loss canary: this phrase is deliberately absent")
def test_planted_missing_phrase_is_detected():
    """A deliberately-absent phrase MUST be detected as missing by the guard.

    This is the committed proof that the retention mechanism is non-vacuous. The
    phrase below is not present in gene_extractor's assembled render, so the same
    membership assertion the retention guard uses fails here — and the strict
    xfail turns that failure into a passing canary. If someone breaks the guard so
    that absent phrases are silently "found", this canary stops xfailing and the
    suite goes RED.
    """
    rendered = harness.assembled_prompt_text("gene_extractor")
    planted = "PHASE_C_PLANTED_LOSS_THIS_PHRASE_MUST_NEVER_APPEAR_IN_ANY_PROMPT"
    assert planted in rendered, "planted phrase should be absent -> this assertion fails -> xfail passes"

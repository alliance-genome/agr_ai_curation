"""No-DB mechanical-check harness for Phase C base-prompt rewrites.

Phase C rewrites every agent's editable base prompt to a lean, outcome-first
structure. Those rewrites are *loss-full* (they change instructions the model
acts on), so each one is guarded by a committed per-agent inventory of
load-bearing phrases plus a machine-checked dropped-list. This module builds the
*real* assembled prompt text those guards check.

Critically, ``build_agent_prompt_layers(...).render()`` does NOT read prompt
files — it reads the DB-backed prompt cache (``assembly.py`` →
``get_all_active_prompts`` → ``cache.py`` raises ``RuntimeError`` when the cache
is uninitialized). The assembly unit tests monkeypatch fake content into that
cache. The Phase C guards must check the *real* prompt text, so this module
assembles it straight from the files on disk with no DB and no fake content:

  * locked core layers via ``build_agent_core_prompt(agent_id).render()``
    (no DB — verified),
  * the editable base prompt by resolving the agent's ``prompt.yaml`` the same
    way the existing contract tests do (``resolve_agent_config_sources()`` →
    ``source.prompt_yaml`` → ``yaml.safe_load(...)["content"]``), which applies
    the ``config/agents/`` override so supervisor/curation_prep/chat_output
    resolve to their config copies,
  * the group-rules content (only when a group is requested) read from the
    resolved agent's ``group_rules/<group>.yaml``.

The concatenation order mirrors ``build_agent_prompt_layers``:
``core.render() + base_content + group_content``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.lib.agent_contracts import get_agent_contract
from src.lib.config import agent_sources
from src.lib.config.agent_loader import (
    canonical_system_agent_key,
    load_agent_definitions,
)
from src.lib.prompts.assembly import build_agent_core_prompt


# ---------------------------------------------------------------------------
# Filesystem locations
# ---------------------------------------------------------------------------

# backend/tests/unit/lib/prompts/phase_c_harness.py -> repo root is parents[5].
REPO_ROOT = Path(__file__).resolve().parents[5]
PACKAGES_DIR = REPO_ROOT / "packages"
CONFIG_AGENTS_DIR = REPO_ROOT / "config" / "agents"
BINDINGS_YAML = PACKAGES_DIR / "alliance" / "tools" / "bindings.yaml"
INVENTORY_DIR = Path(__file__).resolve().parent / "phase_c_inventories"


# ---------------------------------------------------------------------------
# Agent config resolution (applies the config/agents override layer)
# ---------------------------------------------------------------------------


def _sources_by_folder() -> dict[str, agent_sources.AgentConfigSource]:
    """Resolve every agent bundle with the config/agents override layered in.

    Passing no ``search_path`` uses the default layered search paths
    (``packages`` then ``config/agents``), so supervisor/curation_prep/chat_output
    resolve to their config copies — the same precedence production uses. This is
    deliberately NOT ``resolve_agent_config_sources(PACKAGES_DIR)``: that single
    path would skip the override layer.

    Note this also surfaces empty ``config/agents`` stub directories that have no
    agent.yaml (e.g. ``chemical_extractor``, ``ontology_mapping``); those are
    filtered out by ``_agent_index`` because they have no loadable definition.
    """
    return {
        source.folder_name: source
        for source in agent_sources.resolve_agent_config_sources()
        if not source.folder_name.startswith("_")
    }


def _agent_index() -> dict[str, agent_sources.AgentConfigSource]:
    """Map every *real* agent to its resolved source, keyed by canonical agent id.

    The harness keys agents by the canonical system-agent id (what
    ``build_agent_core_prompt`` / ``get_agent_contract`` accept), NOT the folder
    name. Folder names diverge from the canonical id for a few agents
    (``pdf`` -> ``pdf_extraction``, ``ontology_term`` -> ``ontology_term_validation``),
    and some ``config/agents`` stub folders have no agent.yaml at all. Only agents
    with a loaded definition AND a resolvable prompt.yaml are included.

    Both the canonical id and the folder name are accepted as lookup keys so
    inventory files may use whichever name is natural.
    """
    sources = _sources_by_folder()
    definitions = load_agent_definitions()
    by_folder = {definition.folder_name: definition for definition in definitions.values()}

    index: dict[str, agent_sources.AgentConfigSource] = {}
    for folder_name, source in sources.items():
        definition = by_folder.get(folder_name)
        if definition is None:
            # Stub folder with no agent.yaml (not a real, in-scope agent).
            continue
        if source.prompt_yaml is None or not source.prompt_yaml.exists():
            continue
        canonical_id = canonical_system_agent_key(definition)
        index[canonical_id] = source
        # Folder-name alias so inventories can use either name.
        index.setdefault(folder_name, source)
    return index


def _canonical_id_for_source(source: agent_sources.AgentConfigSource) -> str:
    """Return the canonical system-agent id for a resolved source."""
    definitions = load_agent_definitions()
    for definition in definitions.values():
        if definition.folder_name == source.folder_name:
            return canonical_system_agent_key(definition)
    raise KeyError(
        f"No loaded agent definition for folder '{source.folder_name}'"
    )


def resolved_source(agent_key: str) -> agent_sources.AgentConfigSource:
    """Return the resolved config source for one agent (override applied).

    Accepts the canonical agent id or the folder name.
    """
    index = _agent_index()
    source = index.get(agent_key)
    if source is None:
        raise KeyError(
            f"Agent '{agent_key}' not found among resolvable agents: "
            f"{sorted(set(all_agent_keys()))}"
        )
    return source


def all_agent_keys() -> tuple[str, ...]:
    """Return every real agent's canonical id (override applied), sorted.

    Excludes empty config stub folders and reports each agent exactly once by its
    canonical system-agent id (the id ``build_agent_core_prompt`` accepts).
    """
    sources = _sources_by_folder()
    definitions = load_agent_definitions()
    by_folder = {definition.folder_name: definition for definition in definitions.values()}

    keys: set[str] = set()
    for folder_name, source in sources.items():
        definition = by_folder.get(folder_name)
        if definition is None:
            continue
        if source.prompt_yaml is None or not source.prompt_yaml.exists():
            continue
        keys.add(canonical_system_agent_key(definition))
    return tuple(sorted(keys))


def _base_prompt_content(source: agent_sources.AgentConfigSource) -> str:
    """Read the editable base prompt ``content`` for a resolved agent source."""
    prompt_yaml = source.prompt_yaml
    if prompt_yaml is None or not prompt_yaml.exists():
        raise FileNotFoundError(
            f"Agent '{source.folder_name}' has no resolvable prompt.yaml"
        )
    data = yaml.safe_load(prompt_yaml.read_text(encoding="utf-8"))
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        raise ValueError(
            f"prompt.yaml for '{source.folder_name}' has no string 'content'"
        )
    return content


def _group_rule_content(
    source: agent_sources.AgentConfigSource,
    group_id: str,
) -> str:
    """Read the group-rules ``content`` for ``<group_id>.yaml`` (case-insensitive).

    Group IDs are written upper/lower-case freely in inventory filenames; the
    on-disk group-rule files are lower-case (``fb.yaml``). Match by filename stem
    case-insensitively, mirroring how the resolver merges group rules by
    case-folded name.
    """
    wanted = f"{group_id}.yaml".casefold()
    for rule_file in source.group_rule_files:
        if rule_file.name.casefold() == wanted:
            data = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
            content = data.get("content") if isinstance(data, dict) else None
            if not isinstance(content, str):
                raise ValueError(
                    f"group rule '{rule_file.name}' for '{source.folder_name}' "
                    "has no string 'content'"
                )
            return content
    available = [rule_file.name for rule_file in source.group_rule_files]
    raise FileNotFoundError(
        f"Agent '{source.folder_name}' has no group rule '{group_id}.yaml'; "
        f"available: {available}"
    )


def assembled_prompt_text(agent_key: str, group_id: str | None = None) -> str:
    """Return the real assembled prompt text for an agent, with NO DB.

    Concatenates, in render order:

      1. the locked core layers (``build_agent_core_prompt(agent_key).render()``),
      2. the editable base prompt content (resolved ``prompt.yaml``),
      3. the requested group's rules content (only when ``group_id`` is given).

    This is the same text Phase C retention/invariant guards search. It uses the
    real files on disk and the config/agents override — never the monkeypatched
    fake content the assembly unit tests inject.
    """
    source = resolved_source(agent_key)
    # Resolve to the canonical id build_agent_core_prompt accepts, since a few
    # agents' folder names diverge from their canonical system-agent id.
    canonical_id = _canonical_id_for_source(source)
    core_render = build_agent_core_prompt(canonical_id).render()
    base_content = _base_prompt_content(source)

    parts = [core_render, base_content]
    if group_id is not None:
        parts.append(_group_rule_content(source, group_id))
    return "\n\n".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Inventory + dropped-list loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InventoryFile:
    """One inventory file: phrases checked against one (agent, group) render.

    ``group_id`` is parsed from a ``<agent>.<group>.txt`` filename suffix; a bare
    ``<agent>.txt`` checks the base render (no group).
    """

    path: Path
    agent_key: str
    group_id: str | None
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class DroppedEntry:
    """One machine-checked dropped-list entry.

    ``category`` is ``relocated`` (the phrase must still appear in ``new_home``)
    or ``deleted`` (truly redundant/inaccurate; no home, printed for review).
    ``synonyms`` lets a relocated phrase be satisfied by an equivalent wording in
    its new home.
    """

    agent_key: str
    phrase: str
    category: str
    reason: str
    old_source: str
    new_home: str
    synonyms: tuple[str, ...] = ()


def _read_phrase_lines(path: Path) -> tuple[str, ...]:
    """Read non-comment, non-blank phrase lines from an inventory file."""
    phrases: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        phrases.append(stripped)
    return tuple(phrases)


def _parse_inventory_filename(path: Path) -> tuple[str, str | None]:
    """Return ``(agent_key, group_id)`` parsed from a ``.txt`` inventory filename.

    ``gene_extractor.txt`` -> ``("gene_extractor", None)``.
    ``gene_extractor.fb.txt`` -> ``("gene_extractor", "fb")``.

    The agent key itself may contain underscores but never dots, so any dotted
    suffix before ``.txt`` is the group id. Reserved suffixes (``dropped``,
    ``invariants``) are NOT ``.txt`` inventory files and are excluded by callers.
    """
    stem = path.name[: -len(".txt")]
    if "." in stem:
        agent_key, _, group_id = stem.rpartition(".")
        return agent_key, group_id
    return stem, None


def iter_inventory_files() -> tuple[InventoryFile, ...]:
    """Discover every ``<agent>[.<group>].txt`` retention inventory file."""
    if not INVENTORY_DIR.exists():
        return ()
    inventories: list[InventoryFile] = []
    for path in sorted(INVENTORY_DIR.glob("*.txt")):
        # *.invariants.txt is handled by the invariant framework, not retention.
        if path.name.endswith(".invariants.txt"):
            continue
        agent_key, group_id = _parse_inventory_filename(path)
        inventories.append(
            InventoryFile(
                path=path,
                agent_key=agent_key,
                group_id=group_id,
                phrases=_read_phrase_lines(path),
            )
        )
    return tuple(inventories)


def load_inventory(agent_key: str, group_id: str | None = None) -> tuple[str, ...]:
    """Return the phrase list for ``<agent>[.<group>].txt`` (empty if absent)."""
    name = f"{agent_key}.{group_id}.txt" if group_id else f"{agent_key}.txt"
    path = INVENTORY_DIR / name
    if not path.exists():
        return ()
    return _read_phrase_lines(path)


def load_dropped(agent_key: str) -> tuple[DroppedEntry, ...]:
    """Return parsed dropped-list entries for ``<agent>.dropped.json``."""
    path = INVENTORY_DIR / f"{agent_key}.dropped.json"
    if not path.exists():
        return ()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON list of dropped entries")
    entries: list[DroppedEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{index}] must be an object")
        category = str(item.get("category", "")).strip()
        if category not in {"relocated", "deleted"}:
            raise ValueError(
                f"{path}[{index}] category must be 'relocated' or 'deleted', "
                f"got {category!r}"
            )
        synonyms_raw = item.get("synonyms") or ()
        if isinstance(synonyms_raw, str):
            synonyms = (synonyms_raw,)
        else:
            synonyms = tuple(str(value) for value in synonyms_raw)
        entries.append(
            DroppedEntry(
                agent_key=agent_key,
                phrase=str(item.get("phrase", "")),
                category=category,
                reason=str(item.get("reason", "")),
                old_source=str(item.get("old_source", "")),
                new_home=str(item.get("new_home", "")),
                synonyms=synonyms,
            )
        )
    return tuple(entries)


def agents_with_dropped_lists() -> tuple[str, ...]:
    """Return every agent key that has a ``<agent>.dropped.json`` file."""
    if not INVENTORY_DIR.exists():
        return ()
    return tuple(
        sorted(path.name[: -len(".dropped.json")] for path in INVENTORY_DIR.glob("*.dropped.json"))
    )


def load_invariants(agent_key: str) -> tuple[str, ...]:
    """Return ordered workflow-invariant phrases for ``<agent>.invariants.txt``.

    Invariants are stronger than plain retention: they name the ordered workflow
    steps that must survive a rewrite. Seeded for the pilot only; per-agent
    invariants are added as each rewrite lands.

    AUTHORING RULE: each phrase MUST occur exactly once in the assembled render
    and be listed in workflow order. The ordering guard
    (``test_workflow_invariants_survive_in_order``) keys on first-occurrence
    position and FAILS if any phrase occurs more than once, since a recurring
    phrase could spuriously satisfy ordering. Use verbatim phrases from the
    canonical ordered workflow block (where each step appears once), not generic
    tokens like a tool name that also shows up in success-criteria/tool-list
    prose.
    """
    path = INVENTORY_DIR / f"{agent_key}.invariants.txt"
    if not path.exists():
        return ()
    return _read_phrase_lines(path)


# ---------------------------------------------------------------------------
# Dropped-list "new_home" verification sources
# ---------------------------------------------------------------------------


def _load_bindings_tool_index() -> dict[str, dict[str, Any]]:
    """Index every bindings.yaml tool entry by ``tool_id``."""
    data = yaml.safe_load(BINDINGS_YAML.read_text(encoding="utf-8"))
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        raise ValueError(f"{BINDINGS_YAML} has no 'tools' list")
    index: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if isinstance(tool, dict) and tool.get("tool_id"):
            index[str(tool["tool_id"])] = tool
    return index


def bindings_tool_description(tool_id: str) -> str:
    """Return the full searchable text for one bindings.yaml tool.

    Concatenates the top-level ``description`` and the human-facing
    ``metadata.documentation.summary`` so a relocated phrase can be matched
    against whichever the rewrite moved it into.
    """
    tool = _load_bindings_tool_index().get(tool_id)
    if tool is None:
        raise KeyError(f"No bindings.yaml tool with tool_id '{tool_id}'")
    parts = [str(tool.get("description", ""))]
    metadata = tool.get("metadata")
    if isinstance(metadata, dict):
        documentation = metadata.get("documentation")
        if isinstance(documentation, dict):
            parts.append(str(documentation.get("summary", "")))
    return "\n".join(part for part in parts if part)


def agent_contract_text(agent_id: str, topic: str, detail_level: str = "detail") -> str:
    """Return ``get_agent_contract`` output for a topic, serialized for search.

    No DB: the contract lookup reads the in-process agent registry and domain
    pack registries only. The full result is JSON-serialized so a relocated
    phrase can be substring-matched against any field in the contract.
    """
    result = get_agent_contract(
        agent_id=agent_id,
        topic=topic,
        detail_level=detail_level,
    )
    return json.dumps(result, default=str)


def dropped_home_text(entry: DroppedEntry) -> str:
    """Return the searchable text for a relocated entry's declared ``new_home``.

    ``new_home`` is a small DSL so the home is explicit and machine-checkable:

      * ``render`` / ``render:<group>`` — the assembled render (optionally with a
        group rendered).
      * ``bindings:<tool_id>`` — that tool's bindings.yaml description/summary.
      * ``contract:<agent_id>:<topic>[:<detail_level>]`` — get_agent_contract
        output for that agent/topic.
    """
    home = entry.new_home.strip()
    if home == "render" or home.startswith("render:"):
        _, _, group = home.partition(":")
        return assembled_prompt_text(entry.agent_key, group or None)
    if home.startswith("bindings:"):
        tool_id = home[len("bindings:") :]
        return bindings_tool_description(tool_id)
    if home.startswith("contract:"):
        parts = home[len("contract:") :].split(":")
        if len(parts) == 2:
            agent_id, topic = parts
            return agent_contract_text(agent_id, topic)
        if len(parts) == 3:
            agent_id, topic, detail_level = parts
            return agent_contract_text(agent_id, topic, detail_level)
        raise ValueError(f"Malformed contract new_home: {home!r}")
    raise ValueError(
        f"Unsupported new_home '{home}' for relocated entry "
        f"'{entry.phrase}' (agent '{entry.agent_key}'). "
        "Expected render[:group], bindings:<tool_id>, or "
        "contract:<agent_id>:<topic>[:<detail_level>]."
    )


def relocated_phrase_satisfied(entry: DroppedEntry) -> tuple[bool, str]:
    """Return ``(ok, home_text)`` for a relocated entry's home check.

    The entry is satisfied when the phrase OR any declared synonym appears in the
    resolved home text.
    """
    home_text = dropped_home_text(entry)
    candidates = (entry.phrase, *entry.synonyms)
    ok = any(candidate and candidate in home_text for candidate in candidates)
    return ok, home_text


# ---------------------------------------------------------------------------
# Config-divergence guard support
# ---------------------------------------------------------------------------


def config_packages_prompt_pairs() -> tuple[tuple[str, Path, Path], ...]:
    """Return ``(agent, config_prompt, packages_prompt)`` for dual-tree agents.

    Only agents that have a ``prompt.yaml`` in BOTH ``config/agents`` and
    ``packages/alliance/agents`` are returned. supervisor/curation_prep are
    config-only and not included; chat_output is in both and must stay identical.
    """
    pairs: list[tuple[str, Path, Path]] = []
    if not CONFIG_AGENTS_DIR.exists():
        return ()
    for config_agent_dir in sorted(CONFIG_AGENTS_DIR.iterdir()):
        if not config_agent_dir.is_dir() or config_agent_dir.name.startswith("_"):
            continue
        config_prompt = config_agent_dir / "prompt.yaml"
        if not config_prompt.exists():
            continue
        packages_prompt = (
            PACKAGES_DIR / "alliance" / "agents" / config_agent_dir.name / "prompt.yaml"
        )
        if not packages_prompt.exists():
            continue
        pairs.append((config_agent_dir.name, config_prompt, packages_prompt))
    return tuple(pairs)


# ---------------------------------------------------------------------------
# Contradiction dump support
# ---------------------------------------------------------------------------

_DIRECTIVE_TOKENS = ("MUST", "NEVER", "ALWAYS")


def directive_lines(text: str) -> tuple[str, ...]:
    """Return every line containing a MUST/NEVER/ALWAYS directive token.

    Report-only: current prompts carry ~zero such tokens; Phase C introduces a
    small number for true invariants, and this dump surfaces them for the human
    reviewer to scan for contradictions.
    """
    hits: list[str] = []
    for line in text.splitlines():
        if any(token in line for token in _DIRECTIVE_TOKENS):
            hits.append(line.strip())
    return tuple(hits)

import re
from pathlib import Path

import pytest
import yaml

from src.lib.config.agent_loader import load_agent_definitions, reset_cache
from src.lib.config.agent_sources import resolve_agent_config_sources
from src.lib.prompts import assembly
from src.schemas.models.base import ExclusionReasonCode


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _agent_output_schema(folder_name: str):
    source = next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == folder_name
    )
    agent_yaml = source.prompt_yaml.with_name("agent.yaml")
    data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    return data.get("output_schema")


def _is_builder_extractor(folder_name: str) -> bool:
    """A migrated builder extractor binds no envelope output schema; the backend
    builds its curatable_objects from staged builder state."""

    return _agent_output_schema(folder_name) is None


def _load_prompt_content(folder_name: str) -> str:
    source = next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == folder_name
    )
    prompt_path = source.prompt_yaml
    assert prompt_path is not None
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    editable_content = str(data.get("content") or "")
    try:
        agents = load_agent_definitions(_repo_root() / "packages", force_reload=True)
        agent = next(agent for agent in agents.values() if agent.folder_name == folder_name)
        generated_content = assembly.build_agent_core_prompt(agent.agent_id).render()
    finally:
        reset_cache()
    return "\n\n".join([generated_content, editable_content])


def _extractor_prompt_sources():
    return sorted(
        [
            source
            for source in resolve_agent_config_sources(_repo_root() / "packages")
            if source.folder_name == "gene_expression" or source.folder_name.endswith("_extractor")
        ],
        key=lambda source: source.folder_name,
    )


def _listed_reason_codes(content: str) -> set[str]:
    codes: set[str] = set()
    collecting = False

    for line in content.splitlines():
        stripped = line.strip()
        if (
            stripped == "<exclusion_reason_codes>"
            or stripped == "# Exclusion reason codes"
            or stripped == "Exclude with canonical reason_code when applicable:"
        ):
            collecting = True
            continue

        if not collecting:
            continue

        if (
            stripped.startswith("</")
            or stripped.startswith("<")
            or (stripped.startswith("# ") and stripped != "# Exclusion reason codes")
        ):
            collecting = False
            continue

        match = re.match(r"-\s+`?(?P<code>[a-z_]+)`?(?:\s|$)", stripped)
        if match:
            codes.add(match.group("code"))

    return codes


@pytest.mark.parametrize(
    ("folder_name", "domain_specific_snippet"),
    [
        ("allele_extractor", "Strong allele evidence usually does one or more of the following:"),
        ("disease_extractor", "The disease is mentioned only as motivation, background, or population context"),
        # phenotype migrated to the builder pattern; its prompt was rewritten. The
        # domain-specific exclusion guidance now distinguishes this-paper findings
        # from cited prior work in the phenotype evidence rules.
        ("phenotype_extractor", "Phenotypes mentioned only as previously reported, predicted, hypothesized, narrative, or review-style claims."),
        ("gene_expression", "Rescue or ectopic overexpression statements where \"expression\" is only the experimental tool"),
    ],
)
def test_non_gene_extractor_prompts_include_record_evidence_domain_guidance(
    folder_name: str,
    domain_specific_snippet: str,
):
    content = _load_prompt_content(folder_name)

    # Span-evidence workflow guidance shared by every extractor prompt
    # (builder or envelope).
    assert "record_evidence" in content
    assert "Strong quote examples:" in content
    assert "curatable_objects[]" in content
    assert "`read_chunk.evidence_spans[].span_id` values" in content
    assert "record_evidence(span_ids=[...])" in content
    assert "active-run evidence workspace" in content
    assert "evidence_record_ids" in content

    if _is_builder_extractor(folder_name):
        # Builder extractors do not hand-author top-level semantic lists; the
        # backend builds curatable_objects from staged builder state, so the
        # prompt must direct the agent through the builder tool-loop instead of
        # the old "do not emit legacy lists" wording.
        assert "builder tools" in content
        assert re.search(r"stage_\w+", content), folder_name
        assert re.search(r"finalize_\w+_extraction", content), folder_name
    else:
        # Envelope extractors still hand-author the result and must be told not
        # to emit the legacy top-level semantic lists.
        assert re.search(
            r"Do not emit top-level legacy semantic lists:\s+`items\[\]`,\s+`annotations\[\]`,\s+"
            r"`genes\[\]`,\s+`alleles\[\]`,\s+`diseases\[\]`,\s+"
            r"`chemicals\[\]`,\s+(?:or\s+)?`phenotypes\[\]`",
            content,
        )
    assert domain_specific_snippet in content


@pytest.mark.parametrize(
    "source",
    _extractor_prompt_sources(),
    ids=lambda source: source.folder_name,
)
def test_extractor_prompt_reason_codes_match_schema_contract(source):
    prompt_path = source.prompt_yaml
    assert prompt_path is not None
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    prompt_reason_codes = _listed_reason_codes(content)

    schema_reason_codes = {reason_code.value for reason_code in ExclusionReasonCode}

    # Core contract (every extractor): any exclusion reason_code the prompt lists
    # must be a canonical schema code. Builder extractors moved exclusions to
    # ``metadata.*`` audit fields and may list no canonical codes at all, so the
    # non-empty requirement applies only to envelope extractors that still
    # hand-author an exclusion reason-code enumeration.
    assert prompt_reason_codes <= schema_reason_codes
    if not _is_builder_extractor(source.folder_name):
        assert prompt_reason_codes

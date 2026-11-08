"""ALL-1297: searchable, bounded guidance for representative curator requests.

These tests validate the assistant's supplied guidance, not a model's obedience.
Canonical proposal behavior is exercised in test_execution_revisions.
"""
from pathlib import Path

import pytest

from src.lib.agent_studio.studio_guide import prepare_studio_prompt_template, read_studio_guide


ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("query,topic,requirements", [
    ("split", "output_values_and_columns", ["header_template", "{n}", "NOT JSON", "never extra rows", "not permission to discard"]),
    ("UNRESOLVED", "output_values_and_columns", ["paper wording is a separate", "not every cell", "declared label path"]),
    ("inspect_results", "bounded_results_and_contracts", ["NOT a callable Studio tool", "get_domain_envelope_state", "next_request/next_call", "not a free run"]),
    ("ca_", "bounded_results_and_contracts", ["exact revision", "output_profile", "prompt_manifest", "Do not invent a Studio get_agent_contract"]),
    ("namespace", "tool_discovery_and_limits", ["studio_saved_work", "flow_authoring", "different catalogs", "do not grant permission"]),
    ("reasoning", "tool_discovery_and_limits", ["reasoning_options/defaults", "pinned revision", "INCOMPLETE", "never PASS"]),
    ("phenotype_terms", "domain_workflows", ["complete declared phenotype_terms", "not the primary display label", "under development", "not a phenotype extraction or an automatic"]),
    ("perturbation", "domain_workflows", ["measured changes after perturbation", "Rescue-only", "reagent, specimen and allele validation remain under development"]),
    ("parallel batches", "extraction_fanout", ["Never assume it is on", "submit_extraction_plan (shown as Plan Records)", "Agent Studio test runs", "run as one writer", "writers get only read_chunk, search_document, record_evidence and the stage tool", "extraction_plan_missing", "writer_completed", "not judgments about the records", "several model-call inputs"]),
    ("Plan Records", "extraction_fanout", ["code never matches labels or merges records", "a planning miss, a writing miss and a check miss", "(binding metadata builder_catalog) is told to call it before reading", "plan only records that answer the request under its instructions and to leave contrasts and exclusions", "code never checks plan labels for it"]),
    ("Checked once", "validator_inputs", ['"Checked once with N other records that had the same value and evidence."', "where N counts the other records", "the same evidence records with the same IDs, quotes and locations", "(a different entity label or different spans) has different IDs and runs separately", "Nothing is matched by similarity", "without any check being skipped", "Revalidate record"]),
    ("custom validator prompt", "validator_inputs", ["The saved validation request keeps every evidence record whole", "receives selected_inputs exactly as saved", "keeps its evidence_record_id, verified_quote and field_path", "In that evidence list, chunk and span locators", "get_trace_payload", "never to span IDs, source fragments or revision bookkeeping", "evidence_record_ids"]),
    ("review packet", "review_history", ["read_review_packet", "review_decision_packet fact", "not a decision", "its applied list gives each handle's decision_unit_id and target_id", "Decision facts keep their usual shape", "under an R handle, which links between records also use", "reads a fresh packet", "decide_record_retirement", "under additional_fields", "A waiting item has no handle", "its codes give the reason", "accepts only acknowledge_failure", "clears that attempt's blocker", "not a scientific verdict", "the answer or source that selection chose", "earlier_decisions per component", "the error names both", "same_quote_as"]),
])
def test_curator_question_finds_complete_relevant_guidance(monkeypatch, query, topic, requirements):
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    index = read_studio_guide(template=template, render_diagnostic_tools=str, query=query)
    assert topic in {item["topic"] for item in index["topics"]}
    arguments = {"topic": topic}
    chunks = []
    while True:
        result = read_studio_guide(template=template, render_diagnostic_tools=str, **arguments)
        assert result["success"] and result["complete"]
        chunks.append(result["content"])
        if result["complete"]:
            assert not result["next_call"]
            break
        arguments = result["next_call"]["arguments"]
    content = "".join(chunks)
    for requirement in requirements:
        assert requirement in content
    always_sent, _ = prepare_studio_prompt_template(template)
    assert content not in always_sent


def test_extraction_guidance_has_no_lookup_helper_exception():
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    assert "Only an explicit fixed `extraction_mapping`" in template
    assert "agr_species_context_lookup" in template
    assert "Domain-pack-declared extractor helper tools may provide" not in template
    assert "For current 0.7.x" not in template
    assert 'record_evidence(selections=[{"chunk_index": 0, "span_index": 0}])' in template


def test_curator_domain_docs_match_shipped_scope():
    import yaml

    docs = {name: yaml.safe_load((ROOT / f"packages/alliance/agents/{name}/docs.yaml").read_text())
            for name in ("gene_expression", "phenotype_extractor", "disease_extractor")}
    ge = str(docs["gene_expression"])
    phenotype = str(docs["phenotype_extractor"])
    disease = str(docs["disease_extractor"])
    assert "only wild-type patterns" not in ge
    assert "Measured expression changes after perturbation" in ge
    assert "reagent, specimen and allele validation remain under development" in ge
    assert "subject and reference resolution remain under development" in phenotype
    assert "complete term list" in phenotype
    assert "contributes_to" not in disease
    assert "subject validation routes separately" in disease


def test_context_guide_explains_a_skipped_token_count():
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    guide = read_studio_guide(template=template, render_diagnostic_tools=str, topic="token_budget")["content"]
    assert "The estimate is advisory; request admission uses the provider's token count." not in guide
    for phrase in ("count_skipped=true", "request_bytes", "an empty `input_tokens_before`",
                   "A skipped count is not missing data."):
        assert phrase in guide


@pytest.mark.parametrize("package", ["core", "alliance"])
def test_extraction_fanout_guide_is_available_on_demand_for_each_package(package):
    template = (ROOT / f"packages/{package}/config/agent_studio_system_prompt.md").read_text()
    always_sent, topics = prepare_studio_prompt_template(template)
    assert "extraction_fanout" in {topic.topic_id for topic in topics}
    result = read_studio_guide(template=template, render_diagnostic_tools=str, topic="extraction_fanout")
    assert result["success"] and result["complete"]
    assert result["content"] not in always_sent
    assert "extraction_fanout" in {
        item["topic"] for item in read_studio_guide(
            template=template, render_diagnostic_tools=str, query="extraction_plan_missing",
        )["topics"]
    }


def test_trace_workflow_points_fanout_runs_to_the_fanout_guide():
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    workflow = read_studio_guide(template=template, render_diagnostic_tools=str, topic="trace_investigation")
    assert "read studio guide topic `extraction_fanout`" in workflow["content"]


def test_fanout_guide_says_evidence_review_and_completion_are_skipped_by_instruction():
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    guide = read_studio_guide(template=template, render_diagnostic_tools=str, topic="extraction_fanout")["content"]
    assert "recording evidence from the chunks it already read" in guide
    assert "extraction-completion steps are skipped by instruction, not blocked" in guide
    assert "The planner and the check pass still have those tools; writers do not." in guide
    assert "is not told to skip those steps" in guide
    assert "steps are replaced" not in guide
    assert "the planner's staging turn, the writers and the check pass" in guide
    assert "reach the planner and the check pass, not the writers" not in guide


@pytest.mark.parametrize("package", ["core", "alliance"])
def test_review_history_is_available_on_demand_for_each_package(package):
    template = (ROOT / f"packages/{package}/config/agent_studio_system_prompt.md").read_text()
    always_sent, topics = prepare_studio_prompt_template(template)
    assert "review_history" in {topic.topic_id for topic in topics}
    result = read_studio_guide(
        template=template, render_diagnostic_tools=str, topic="review_history",
    )
    assert result["success"] and result["complete"]
    assert result["content"] not in always_sent
    assert "review_history" in {
        item["topic"] for item in read_studio_guide(
            template=template, render_diagnostic_tools=str, query="revalidation",
        )["topics"]
    }


def _alliance_guide(topic):
    template = (ROOT / "packages/alliance/config/agent_studio_system_prompt.md").read_text()
    result = read_studio_guide(template=template, render_diagnostic_tools=str, topic=topic)
    assert result["success"] and result["complete"]
    always_sent, _ = prepare_studio_prompt_template(template)
    assert result["content"] not in always_sent
    return result["content"]


def test_fanout_guide_says_in_category_controls_are_planned():
    guide = _alliance_guide("extraction_fanout")
    assert "an item inside the requested category is planned even when the paper uses it as a control" in guide


def test_extraction_guidance_keeps_contrasts_in_the_written_answer():
    guide = _alliance_guide("domain_envelopes")
    for phrase in (
        "Records answer the curator's question.",
        "even when the paper uses it as a control",
        "items whose membership in the category the paper does not establish go in its written answer (its final explanation, with any uncertainty), not as records",
        "unless the curator asks for those items themselves",
        "and so does the fan-out planning step, under the agent's own instructions (guide topic extraction_fanout)",
        "read the extractor's final explanation in the trace before calling it an extraction miss",
        "keeps the prompt it was saved with",
        "only their fan-out planning step does",
    ):
        assert phrase in guide
    assert "Controls, wild-type strains, look-alikes" not in guide


def test_review_guidance_explains_retiring_and_restoring_out_of_scope_records():
    guide = _alliance_guide("review_history")
    for phrase in (
        "a record must be shown in the paper to answer the curator's question",
        "each record ends accepted, retired or left pending",
        "A reviewer retirement can remove a validated record too",
        "the reviewer cannot retire the records the curator asked to re-review, either itself or by approving",
        "Retirement is not deletion",
        "has no extractor recommendation",
        "Restore for review, with the curator's reason",
    ):
        assert phrase in guide
    assert "finding no support is still not evidence against them" not in guide
    assert guide.count("a reject only records an opinion and leaves the record pending") == 1


def test_review_guidance_explains_rechecks_and_how_review_ends():
    guide = _alliance_guide("review_history")
    for phrase in (
        "one targeted re-check per review round",
        "a round the curator requests (Revalidate record or all records) gives each record a fresh one",
        "whether one is left (follow_up_available)",
        "Saved results and history are retained.",
        "it needs a curator's decision (including a curator's pending decision), it could not be checked (an acknowledged validation failure), the reviewer did not accept it, or the reviewer did not decide it",
        '"no re-check left for N records"',
        "removing 2 records that don't answer the question",
        "Review stops starting new runs once no review action could change the remaining records",
        'A "Review paused: ..." notice means an operational stop with no pending list',
        "a cancelled, expired or superseded run, an uncertain execution, a retired document or an unsupported state",
        "a curator's own reject, defer or acknowledged failure in the round stops reviewer runs for that record",
        'named "the extraction as a whole"',
        "a re-check also needs a saved extraction agent that supports re-checks and the record's original extraction",
        "In AI Chat only, on the turn a removal happened, the notes also name each record the review retired, "
        "with its saved reason, and the current record count",
        "\"Review removed 1 record that doesn't answer the question: Crb-GFP (",
        'this line can sit beside a "Review paused: ..." notice',
        "the supervisor receives the accepted, pending and retired-in-this-review counts and the list of records "
        "this review retired",
    ):
        assert phrase in guide
    assert "a time or spending limit" not in guide


def test_review_guidance_says_the_quick_check_only_logs():
    guide = _alliance_guide("review_history")
    for phrase in (
        "Quick check beside review: when the server setting is shadow",
        "with the records in forward and in reversed order",
        "question set v6-shadow",
        "under three candidate rules as the trace event review_quick_check.shadow",
        "TraceReview groups under review_quick_check",
        "It is logging only: the supervisor still reviews every record",
        "Never present its probabilities or would-be routes as review decisions",
        "With the setting off there is no such event",
    ):
        assert phrase in guide
    workflow = _alliance_guide("trace_investigation")
    assert "A `review_quick_check.shadow` event is logging only; read studio guide topic `review_history`" in workflow


def test_review_guidance_attributes_each_retirement_from_the_stored_origin():
    guide = _alliance_guide("review_history")
    for phrase in (
        "Which path retired a record is recorded; state it from the record, never assume it.",
        'the record_retirement_decision fact has origin "review" when the reviewer retired the record itself',
        'origin "extractor_recommendation" when the extraction agent proposed retirement and the reviewer answered with decide_record_retirement',
        'only decision "approve" retires the record',
        "A fact with no origin was saved before this release and follows the extractor path.",
        "a retire decision in record_validation_review is the reviewer's own retirement; a targeted re-check that came before it is not a retirement recommendation",
    ):
        assert phrase in guide


def test_prompt_playbook_leads_with_the_saved_prompt_of_a_copied_agent():
    guide = _alliance_guide("prompt_playbook")
    assert "say first that it keeps the prompt it was saved with" in guide
    assert "a later release's change to the packaged prompt does not reach it" in guide


def test_review_guidance_explains_refused_retires_restores_and_the_system_error_pause():
    guide = _alliance_guide("review_history")
    for phrase in (
        "the refusal names the referring record by its label and target ID",
        "the reviewer must retire the records that refer to it in the same save, or keep it",
        "to approve an extraction agent's recommendation, it first retires the referring records itself",
        'which is still removed from the results; restore that record first',
        "the refused restore changes nothing, and both records keep their saved values and history",
        "Nothing is retired or restored automatically to resolve it.",
        '"Review paused: a system error stopped a save before it changed anything, and it was reported. Saved results and history are retained."',
        "that save changed nothing, the error was reported to the maintainers automatically",
        "does not offer Continue existing round for it",
    ):
        assert phrase in guide

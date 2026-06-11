"""Package-owned AGR curation controlled vocabulary helper tests."""

from __future__ import annotations

from types import SimpleNamespace

from agr_ai_curation_alliance.tools import agr_curation


def _query_fn():
    return agr_curation._unwrap_function_tool_callable(
        agr_curation.agr_curation_query,
        "agr_curation_query",
    )


def _term_helper_fn():
    return agr_curation._unwrap_function_tool_callable(
        agr_curation.get_domain_field_term_options,
        "get_domain_field_term_options",
    )


def _search_resolver_fn():
    return agr_curation._unwrap_function_tool_callable(
        agr_curation.search_domain_field_terms,
        "search_domain_field_terms",
    )


def _inspect_resolver_fn():
    return agr_curation._unwrap_function_tool_callable(
        agr_curation.inspect_ontology_term,
        "inspect_ontology_term",
    )


def _resolve_resolver_fn():
    return agr_curation._unwrap_function_tool_callable(
        agr_curation.resolve_domain_field_term,
        "resolve_domain_field_term",
    )


class _Resolver:
    def __init__(self, db):
        self._db = db

    def get_db_client(self):
        return self._db


def _term(
    *,
    internal_id: int,
    vocabulary: str = "Disease Relation",
    name: str = "is_implicated_in",
    abbreviation: str | None = None,
    obsolete: bool = False,
    synonyms: list[str] | None = None,
):
    return SimpleNamespace(
        id=internal_id,
        vocabulary=vocabulary,
        vocabulary_label=vocabulary,
        name=name,
        abbreviation=abbreviation,
        definition=f"{name} definition",
        obsolete=obsolete,
        synonyms=synonyms or [],
    )


def test_get_vocabulary_term_resolves_exact_term(monkeypatch):
    calls = []

    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**kwargs):
            calls.append(kwargs)
            return [
                _term(
                    internal_id=101,
                    name="is_implicated_in",
                    abbreviation="implicated",
                    synonyms=["implicated in"],
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="Disease Relation",
        term_name="is_implicated_in",
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.count == 1
    assert result.data[0]["internal_id"] == 101
    assert result.data[0]["term_name"] == "is_implicated_in"
    assert result.data[0]["vocabulary"] == "Disease Relation"
    assert result.data[0]["abbreviation"] == "implicated"
    assert result.data[0]["synonyms"] == ["implicated in"]
    assert result.result_projections[0]["projection_type"] == "vocabulary_term_reference"
    assert calls == [
        {
            "term": "is_implicated_in",
            "vocabulary": "Disease Relation",
            "exact_match": True,
            "include_synonyms": True,
            "include_obsolete": False,
            "limit": 100,
        }
    ]


def test_get_vocabulary_term_preserves_zero_internal_id(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**_kwargs):
            return [_term(internal_id=0)]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="Disease Relation",
        term_name="is_implicated_in",
    )

    assert result.status == "ok"
    assert result.data[0]["id"] == 0
    assert result.data[0]["internal_id"] == 0
    assert result.result_projections[0]["resolved_id"] == 0


def test_get_vocabulary_term_reports_no_match(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**_kwargs):
            return []

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="Disease Relation",
        abbreviation="missing",
    )

    assert result.status == "ok"
    assert result.lookup_status == "not_found"
    assert result.failure_classification == "not_found"
    assert result.count == 0
    assert "Vocabulary term not found" in (result.message or "")
    assert result.lookup_attempts[0]["attempted_query"]["query_field"] == "abbreviation"


def test_get_vocabulary_term_preserves_obsolete_candidate(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**kwargs):
            assert kwargs["include_obsolete"] is True
            return [
                _term(
                    internal_id=202,
                    name="legacy_relation",
                    obsolete=True,
                    synonyms=["old relation"],
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="Disease Relation",
        synonym="old relation",
        include_obsolete=True,
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.data[0]["obsolete"] is True
    assert "obsolete_vocabulary_terms:1" in result.warnings
    # The candidate is a lightweight pointer now; it keeps a scalar object_type
    # but no longer re-embeds the full projection (that lives once under
    # result_projections).
    assert "projection" not in result.candidate_matches[0]
    assert result.candidate_matches[0]["object_type"] == "VocabularyTerm"


def test_get_vocabulary_term_reports_ambiguous_exact_matches(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**_kwargs):
            return [
                _term(internal_id=301, vocabulary="Relation", name="expressed in"),
                _term(internal_id=302, vocabulary="Expression Relation", name="expressed in"),
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="relation",
        term_name="expressed in",
    )

    assert result.status == "ok"
    assert result.lookup_status == "ambiguous"
    assert result.failure_classification == "ambiguous"
    assert result.count == 2
    assert {item["internal_id"] for item in result.data} == {301, 302}


def test_search_vocabulary_terms_and_unavailable_helper(monkeypatch):
    class SearchDb:
        @staticmethod
        def search_vocabulary_terms(**kwargs):
            assert kwargs["exact_match"] is False
            return [_term(internal_id=401, vocabulary="Condition Relation Type", name="has_condition")]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(SearchDb()),
    )

    result = _query_fn()(
        method="search_vocabulary_terms",
        vocabulary="Condition Relation Type",
        term="condition",
        exact_match=False,
        limit=5,
    )
    assert result.status == "ok"
    assert result.count == 1
    assert result.lookup_attempts[0]["attempted_query"]["limit"] == 5

    class MissingHelperDb:
        pass

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(MissingHelperDb()),
    )

    unavailable = _query_fn()(
        method="search_vocabulary_terms",
        vocabulary="Condition Relation Type",
    )
    assert unavailable.status == "error"
    assert unavailable.lookup_status == "under_development"
    assert "search_vocabulary_terms" in (unavailable.message or "")


def test_domain_field_term_options_returns_gene_expression_relation(monkeypatch):
    calls = []

    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**kwargs):
            calls.append(kwargs)
            return [
                _term(
                    internal_id=200000200,
                    vocabulary="Expression Relation",
                    name="is_expressed_in",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="relation.name",
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.count == 1
    helper_result = result.data["helper_results"][0]
    assert helper_result["term_source"] == {
        "kind": "controlled_vocabulary",
        "vocabulary": "Expression Relation",
    }
    assert helper_result["value"] == "is_expressed_in"
    assert helper_result["helper_result"]["value"] == "is_expressed_in"
    assert helper_result["helper_result"]["vocabulary"] == "Expression Relation"
    assert helper_result["helper_result"]["internal_id"] == 200000200
    assert helper_result["source"] == {
        "provider": "alliance_curation_db",
        "tool": "agr_curation_query",
        "method": "search_vocabulary_terms",
    }
    assert helper_result["helper_result"]["source"] == {
        "provider": "alliance_curation_db",
        "tool": "agr_curation_query",
        "method": "search_vocabulary_terms",
    }
    assert result.data["options"][0] == {
        "field_path": "relation.name",
        "value": "is_expressed_in",
        "term_name": "is_expressed_in",
        "internal_id": 200000200,
        "vocabulary": "Expression Relation",
        "source": {
            "provider": "alliance_curation_db",
            "tool": "agr_curation_query",
            "method": "search_vocabulary_terms",
        },
    }
    assert result.data["match_status"] == "resolved"
    assert calls == [
        {
            "term": None,
            "vocabulary": "Expression Relation",
            "exact_match": False,
            "include_synonyms": True,
            "include_obsolete": False,
            "limit": 25,
        }
    ]


def test_domain_field_term_option_uses_only_canonical_source_location():
    option = agr_curation._helper_option(
        {
            "field_path": "relation.name",
            "value": "is_expressed_in",
            "term_name": "is_expressed_in",
            "helper_result": {
                "source": {
                    "provider": "legacy_nested_source",
                    "tool": "agr_curation_query",
                    "method": "search_vocabulary_terms",
                },
            },
        }
    )

    assert "source" not in option


def test_domain_field_term_options_refuses_undeclared_field(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**_kwargs):
            raise AssertionError("undeclared fields must not reach broad lookup")

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="single_reference.reference_id",
        query="PMID:1",
    )

    assert result.status == "error"
    assert result.lookup_status == "not_found"
    assert "No domain-pack term helper policy" in (result.message or "")


def test_domain_field_term_options_searches_assay_stage_and_direct_site_fields(monkeypatch):
    calls = []

    class FakeDb:
        @staticmethod
        def search_ontology_terms(**kwargs):
            calls.append(("ontology", kwargs))
            return [
                SimpleNamespace(
                    curie="MMO:0000655",
                    name="reverse transcription polymerase chain reaction assay",
                    ontology_type="MMOTerm",
                )
            ]

        @staticmethod
        def search_life_stage_terms(**kwargs):
            calls.append(("stage", kwargs))
            return [
                SimpleNamespace(
                    curie="ZFS:0000037",
                    name="18 hpf",
                    ontology_type="ZFSTerm",
                )
            ]

        @staticmethod
        def search_anatomy_terms(**kwargs):
            calls.append(("anatomy", kwargs))
            return [
                SimpleNamespace(
                    curie="ZFA:0001094",
                    name="brain",
                    ontology_type="ZFATerm",
                )
            ]

        @staticmethod
        def search_go_terms(**kwargs):
            calls.append(("go", kwargs))
            return [
                SimpleNamespace(
                    curie="GO:0005634",
                    name="nucleus",
                    namespace="cellular_component",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    assay = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_experiment.expression_assay_used",
        query="immunohistochemistry assay",
        exact_match=True,
        limit=2,
    )
    stage = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="when_expressed_stage_name",
        source_phrase="18 hpf",
        data_provider="ZFIN",
    )
    anatomy = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.anatomical_structure",
        source_phrase="brain",
        data_provider="ZFIN",
    )
    cellular = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.cellular_component",
        source_phrase="nucleus",
    )

    assert assay.data["options"][0]["curie"] == "MMO:0000655"
    assert assay.data["options"][0]["source"]["method"] == "search_ontology_terms"
    assert stage.data["options"][0]["curie"] == "ZFS:0000037"
    assert anatomy.data["options"][0]["curie"] == "ZFA:0001094"
    assert cellular.data["options"][0]["curie"] == "GO:0005634"
    assert calls == [
        (
            "ontology",
            {
                "term": "immunohistochemistry assay",
                "ontology_type": "MMOTerm",
                "exact_match": True,
                "include_synonyms": True,
                "limit": 2,
            },
        ),
        (
            "stage",
            {
                "term": "18 hpf",
                "data_provider": "ZFIN",
                "exact_match": False,
                "include_synonyms": True,
                "limit": 25,
            },
        ),
        (
            "anatomy",
            {
                "term": "brain",
                "data_provider": "ZFIN",
                "exact_match": False,
                "include_synonyms": True,
                "limit": 25,
            },
        ),
        (
            "go",
            {
                "term": "nucleus",
                "go_aspect": "cellular_component",
                "exact_match": False,
                "include_synonyms": True,
                "limit": 25,
            },
        ),
    ]


def test_domain_field_term_options_reads_canonical_evidence_context_keys(monkeypatch):
    calls = []

    class FakeDb:
        @staticmethod
        def search_ontology_terms(**kwargs):
            calls.append(kwargs)
            return [
                SimpleNamespace(
                    curie="MMO:0000655",
                    name="reverse transcription polymerase chain reaction assay",
                    ontology_type="MMOTerm",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_experiment.expression_assay_used",
        evidence_context={
            "label": "ignored legacy label",
            "query": "immunohistochemistry assay",
        },
    )

    assert result.status == "ok"
    assert result.data["source_phrase"] == "immunohistochemistry assay"
    assert calls[0]["term"] == "immunohistochemistry assay"


def test_domain_field_term_options_uses_configured_assay_label_mapping(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_ontology_terms(**_kwargs):
            raise AssertionError("configured assay labels should not hit live lookup")

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_experiment.expression_assay_used",
        source_phrase="whole-mount in situ hybridization",
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.data["match_status"] == "resolved"
    assert result.data["options"] == [
        {
            "field_path": "expression_experiment.expression_assay_used",
            "value": "MMO:0000658",
            "term_name": "whole mount in situ hybridization assay",
            "curie": "MMO:0000658",
            "ontology_type": "MMOTerm",
            "slot_hint": "expression_experiment.expression_assay_used",
            "source": {
                "provider": "domain_pack_config",
                "tool": "get_domain_field_term_options",
                "method": "configured_label_mapping",
                "mapping_index": 0,
                "mapping_id": (
                    "gene_expression_assay.whole_mount_in_situ_hybridization"
                ),
            },
        }
    ]
    assert result.data["helper_results"][0]["candidate"]["authority"] == (
        "configured_mapping"
    )


def test_domain_field_term_options_uses_term_source_for_ontology_filter(monkeypatch):
    calls = []

    class FakeDb:
        @staticmethod
        def search_ontology_terms(**kwargs):
            calls.append(kwargs)
            return [
                SimpleNamespace(
                    curie="MMO:0000655",
                    name="reverse transcription polymerase chain reaction assay",
                    ontology_type="MMOTerm",
                )
            ]

    def helper_policy(**_kwargs):
        return {
            "term_source": {
                "kind": "ontology",
                "ontology_family": "assay",
                "ontology_term_type": "MMOTerm",
            },
            "lookup": {
                "package_tool": "get_domain_field_term_options",
                "method": "search_ontology_terms",
                "ontology_term_type": "ConflictingLookupTerm",
                "candidate_authority": "selector_evidence",
            },
        }

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)
    monkeypatch.setattr(agr_curation, "_field_term_helper_policy", helper_policy)

    result = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_experiment.expression_assay_used",
        source_phrase="reverse transcription polymerase chain reaction assay",
    )

    assert result.status == "ok"
    assert result.data["term_source"]["ontology_term_type"] == "MMOTerm"
    assert calls[0]["ontology_type"] == "MMOTerm"


def test_domain_field_term_options_routes_gene_expression_site(monkeypatch):
    calls = []

    class FakeDb:
        @staticmethod
        def search_anatomy_terms(**kwargs):
            calls.append(("anatomy", kwargs))
            return [
                SimpleNamespace(
                    curie="ZFA:0001094",
                    name="brain",
                    ontology_type="ZFATerm",
                )
            ]

        @staticmethod
        def search_go_terms(**kwargs):
            calls.append(("go", kwargs))
            return [
                SimpleNamespace(
                    curie="GO:0005634",
                    name="nucleus",
                    namespace="cellular_component",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _term_helper_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed",
        source_phrase="nucleus",
        data_provider="ZFIN",
        limit=3,
    )

    assert result.status == "ok"
    assert result.count == 2
    by_slot = {
        item["slot_hint"]: item
        for item in result.data["helper_results"]
    }
    assert by_slot[
        "expression_pattern.where_expressed.anatomical_structure"
    ]["candidate"]["curie"] == "ZFA:0001094"
    cellular = by_slot["expression_pattern.where_expressed.cellular_component"]
    assert cellular["term_source"] == {
        "kind": "ontology",
        "ontology_family": "go",
        "go_aspect": "cellular_component",
    }
    assert cellular["candidate"] == {
        "curie": "GO:0005634",
        "label": "nucleus",
        "name": "nucleus",
        "namespace": "cellular_component",
        "ontology_type": None,
        "obsolete": False,
        "authority": "hint_only",
    }
    assert calls == [
        (
            "anatomy",
            {
                "term": "nucleus",
                "data_provider": "ZFIN",
                "exact_match": False,
                "include_synonyms": True,
                "limit": 3,
            },
        ),
        (
            "go",
            {
                "term": "nucleus",
                "go_aspect": "cellular_component",
                "exact_match": False,
                "include_synonyms": True,
                "limit": 3,
            },
        ),
    ]


def test_search_domain_field_terms_returns_field_scoped_candidates(monkeypatch):
    calls = []

    class FakeDb:
        @staticmethod
        def search_anatomy_terms(**kwargs):
            calls.append(kwargs)
            return [
                SimpleNamespace(
                    curie="WBbt:0004758",
                    name="ALM neuron",
                    ontology_type="WBBTTerm",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _search_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.anatomical_structure",
        query="ALM neuron",
        data_provider="WB",
        limit=5,
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.data["authority"] == "candidate_discovery_only"
    assert result.data["candidates"][0]["curie"] == "WBbt:0004758"
    assert result.data["candidates"][0]["source_tool"] == "search_domain_field_terms"
    assert result.data["diagnostic_summary"].startswith(
        "search success for expression_pattern.where_expressed.anatomical_structure"
    )
    assert result.data["debug"]["resolver_stage"] == "search"
    assert result.data["debug"]["candidate_count"] == 1
    assert result.data["debug"]["selected_curie"] == "WBbt:0004758"
    assert result.data["next_tool_call"]["tool"] == "resolve_domain_field_term"
    assert result.data["next_tool_call"]["arguments"]["field_path"] == (
        "expression_pattern.where_expressed.anatomical_structure"
    )
    assert "limited_search_backend:current_api_exact_prefix_contains" in result.warnings
    assert calls == [
        {
            "term": "ALM neuron",
            "data_provider": "WB",
            "exact_match": False,
            "include_synonyms": True,
            "limit": 5,
        }
    ]


def test_search_domain_field_terms_does_not_suggest_ontology_inspection_for_cv_ambiguity(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_vocabulary_terms(**_kwargs):
            return [
                _term(internal_id=1, vocabulary="Expression Relation", name="is_expressed_in"),
                _term(internal_id=2, vocabulary="Expression Relation", name="is_not_expressed_in"),
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )

    result = _search_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="relation.name",
        query="expressed",
    )

    assert result.status == "ok"
    assert result.lookup_status == "ambiguous"
    assert result.data["next_tool_call"]["tool"] == "search_domain_field_terms"
    assert "candidate_value" in result.data["next_tool_call"]["note"]


def test_inspect_ontology_term_returns_bounded_context(monkeypatch):
    class FakeDb:
        @staticmethod
        def get_ontology_term(curie):
            assert curie == "WBbt:0004758"
            return SimpleNamespace(
                curie="WBbt:0004758",
                name="ALM neuron",
                namespace="wormbase_anatomy",
                definition="An ALM mechanosensory neuron.",
                ontology_type="WBBTTerm",
                synonyms=["anterior lateral microtubule cell"],
            )

        @staticmethod
        def get_ontology_pairs(prefix):
            assert prefix == "WBbt"
            return [
                {
                    "parent_curie": "WBbt:0004017",
                    "parent_name": "mechanosensory neuron",
                    "parent_type": "wormbase_anatomy",
                    "parent_is_obsolete": False,
                    "child_curie": "WBbt:0004758",
                    "child_name": "ALM neuron",
                    "child_type": "wormbase_anatomy",
                    "child_is_obsolete": False,
                },
                {
                    "parent_curie": "WBbt:0004017",
                    "parent_name": "mechanosensory neuron",
                    "parent_type": "wormbase_anatomy",
                    "parent_is_obsolete": False,
                    "child_curie": "WBbt:0004759",
                    "child_name": "PLM neuron",
                    "child_type": "wormbase_anatomy",
                    "child_is_obsolete": False,
                },
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _inspect_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.anatomical_structure",
        curie="WBbt:0004758",
        data_provider="WB",
        include_siblings=True,
    )

    assert result.status == "ok"
    assert result.data["term"]["curie"] == "WBbt:0004758"
    assert result.data["context"]["parents"][0]["curie"] == "WBbt:0004017"
    assert result.data["context"]["siblings"][0]["curie"] == "WBbt:0004759"
    assert result.data["authority"] == "inspection_only"
    assert result.data["debug"]["resolver_stage"] == "inspect"
    assert result.data["debug"]["context_counts"] == {
        "parents": 1,
        "children": 0,
        "siblings": 1,
    }
    assert result.data["next_tool_call"]["tool"] == "resolve_domain_field_term"


def test_inspect_ontology_term_uses_targeted_tree_lookup_when_session_available(monkeypatch):
    execute_calls = []

    class FakeSession:
        def execute(self, query, params):
            query_text = str(query)
            execute_calls.append((query_text, dict(params)))
            curie = params["curie"]
            is_parent_lookup = "otc.curie = :curie" in query_text
            is_child_lookup = "otp.curie = :curie" in query_text
            if is_parent_lookup and curie == "WBbt:0004758":
                return SimpleNamespace(
                    fetchall=lambda: [
                        (
                            "parent",
                            "WBbt:0004017",
                            "mechanosensory neuron",
                            "wormbase_anatomy",
                            False,
                        )
                    ]
                )
            if is_child_lookup and curie == "WBbt:0004017":
                return SimpleNamespace(
                    fetchall=lambda: [
                        (
                            "child",
                            "WBbt:0004758",
                            "ALM neuron",
                            "wormbase_anatomy",
                            False,
                        ),
                        (
                            "child",
                            "WBbt:0004759",
                            "PLM neuron",
                            "wormbase_anatomy",
                            False,
                        ),
                    ]
                )
            return SimpleNamespace(fetchall=lambda: [])

        @staticmethod
        def close():
            return None

    class FakeDb:
        @staticmethod
        def get_ontology_term(curie):
            return SimpleNamespace(
                curie=curie,
                name="ALM neuron",
                namespace="wormbase_anatomy",
                ontology_type="WBBTTerm",
            )

        @staticmethod
        def create_session():
            return FakeSession()

        @staticmethod
        def get_ontology_pairs(_prefix):
            raise AssertionError("targeted DB session lookup should avoid broad pair scan")

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _inspect_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.anatomical_structure",
        curie="WBbt:0004758",
        data_provider="WB",
        include_siblings=True,
    )

    assert result.status == "ok"
    assert result.data["context"]["parents"][0]["curie"] == "WBbt:0004017"
    assert result.data["context"]["siblings"][0]["curie"] == "WBbt:0004759"
    assert result.data["debug"]["context_counts"] == {
        "parents": 1,
        "children": 0,
        "siblings": 1,
    }
    assert all(call[1]["curieprefix"] == "WBbt%" for call in execute_calls)
    assert any("otc.curie LIKE :curieprefix" in call[0] for call in execute_calls)


def test_inspect_ontology_term_falls_back_when_targeted_tree_lookup_fails(monkeypatch):
    class FakeSession:
        @staticmethod
        def execute(_query, _params):
            raise RuntimeError("schema drift")

        @staticmethod
        def close():
            return None

    class FakeDb:
        @staticmethod
        def get_ontology_term(curie):
            return SimpleNamespace(
                curie=curie,
                name="ALM neuron",
                namespace="wormbase_anatomy",
                ontology_type="WBBTTerm",
            )

        @staticmethod
        def create_session():
            return FakeSession()

        @staticmethod
        def get_ontology_pairs(prefix):
            assert prefix == "WBbt"
            return [
                {
                    "parent_curie": "WBbt:0004017",
                    "parent_name": "mechanosensory neuron",
                    "parent_type": "wormbase_anatomy",
                    "parent_is_obsolete": False,
                    "child_curie": "WBbt:0004758",
                    "child_name": "ALM neuron",
                    "child_type": "wormbase_anatomy",
                    "child_is_obsolete": False,
                }
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _inspect_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.anatomical_structure",
        curie="WBbt:0004758",
        data_provider="WB",
    )

    assert result.status == "ok"
    assert result.data["context"]["parents"][0]["curie"] == "WBbt:0004017"


def test_inspect_ontology_term_blocks_terms_outside_slim_allowlist(monkeypatch):
    class FakeDb:
        @staticmethod
        def get_ontology_term(curie):
            return SimpleNamespace(
                curie=curie,
                name="not an allowed slim term",
                ontology_type="UBERONTerm",
            )

        @staticmethod
        def get_ontology_pairs(_prefix):
            return []

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _inspect_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.anatomical_structure_uberon_terms",
        curie="UBERON:9999999",
    )

    assert result.status == "ok"
    assert result.data["policy_checks"]["ontology_type_matches"] is True
    assert result.data["policy_checks"]["allowed_by_slim_membership"] is False
    assert result.data["debug"]["policy_blocker"] == (
        "candidate_not_in_allowed_slim_terms"
    )
    assert result.data["next_tool_call"] is None


def test_inspect_ontology_term_blocks_go_term_with_wrong_aspect(monkeypatch):
    class FakeDb:
        @staticmethod
        def get_ontology_term(curie):
            return SimpleNamespace(
                curie=curie,
                name="biological process",
                namespace="biological_process",
            )

        @staticmethod
        def get_ontology_pairs(_prefix):
            return []

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _inspect_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.cellular_component",
        curie="GO:0008150",
    )

    assert result.status == "ok"
    assert result.lookup_status == "blocked"
    assert result.data["policy_checks"]["go_aspect_matches"] is False
    assert result.data["debug"]["policy_blocker"] == "candidate_go_aspect_mismatch"
    assert result.data["next_tool_call"] is None


def test_resolve_domain_field_term_returns_builder_resolver_call_instruction(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_ontology_terms(**kwargs):
            assert kwargs["ontology_type"] == "MMOTerm"
            return [
                SimpleNamespace(
                    curie="MMO:0000658",
                    name="whole mount in situ hybridization assay",
                    ontology_type="MMOTerm",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _resolve_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_experiment.expression_assay_used",
        source_phrase="reverse transcription polymerase chain reaction assay",
        candidate_curie="MMO:0000655",
    )

    assert result.status == "resolved"
    assert result.lookup_status == "success"
    assert result.data["diagnostic_summary"].startswith(
        "resolve success for expression_experiment.expression_assay_used"
    )
    assert result.data["debug"]["resolver_stage"] == "resolve"
    assert result.data["debug"]["selected_curie"] == "MMO:0000655"
    selection = result.data["helper_selection"]
    assert selection["source_tool"] == "resolve_domain_field_term"
    assert selection["field_path"] == "expression_experiment.expression_assay_used"
    assert selection["selected_value"] == "MMO:0000655"
    assert selection["selected_name"] == "reverse transcription polymerase chain reaction assay"
    assert selection["authority"] == "selector_evidence"
    instructions = " ".join(result.data["instructions"])
    normalized_instructions = instructions.casefold()
    assert "provenance is verified automatically" in instructions
    assert "do not author metadata.provenance.helper_selections" in normalized_instructions
    assert "Copy helper_selection into metadata.provenance.helper_selections[]" not in instructions
    assert result.data["payload_field_instructions"] == {
        "set": [
            {
                "field_path": "expression_experiment.expression_assay_used.curie",
                "value": "MMO:0000655",
            },
            {
                "field_path": "expression_experiment.expression_assay_used.name",
                "value": "reverse transcription polymerase chain reaction assay",
            },
        ]
    }


def test_resolve_domain_field_term_routes_broad_site_to_concrete_slot(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_anatomy_terms(**_kwargs):
            return []

        @staticmethod
        def search_go_terms(**_kwargs):
            return [
                SimpleNamespace(
                    curie="GO:0005634",
                    name="nucleus",
                    namespace="cellular_component",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _resolve_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed",
        source_phrase="nucleus",
        candidate_curie="GO:0005634",
        data_provider="ZFIN",
    )

    assert result.status == "resolved"
    assert result.data["field_path"] == (
        "expression_pattern.where_expressed.cellular_component"
    )
    assert result.data["requested_field_path"] == "expression_pattern.where_expressed"
    assert result.data["payload_field_instructions"]["set"][0]["field_path"] == (
        "expression_pattern.where_expressed.cellular_component.curie"
    )
    assert result.data["helper_selection"]["field_path"] == (
        "expression_pattern.where_expressed.cellular_component"
    )


def test_resolve_domain_field_term_blocks_slim_terms_outside_allowlist(monkeypatch):
    class FakeDb:
        @staticmethod
        def search_ontology_terms(**_kwargs):
            return [
                SimpleNamespace(
                    curie="UBERON:9999999",
                    name="not an allowed slim term",
                    ontology_type="UBERONTerm",
                )
            ]

    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(FakeDb()),
    )
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = _resolve_resolver_fn()(
        domain_pack_id="agr.alliance.gene_expression",
        object_type="GeneExpressionAnnotation",
        field_path="expression_pattern.where_expressed.anatomical_structure_uberon_terms",
        source_phrase="not an allowed slim term",
        candidate_curie="UBERON:9999999",
    )

    assert result.status == "blocked"
    assert result.data["policy_blocker"] == "candidate_not_in_allowed_slim_terms"
    assert result.data["debug"]["policy_blocker"] == (
        "candidate_not_in_allowed_slim_terms"
    )


# =============================================================================
# Subset-aware controlled-vocabulary lookups (Part A: data-type-axis subsets).
# =============================================================================


class _FakeSubsetSession:
    """Fake SQLAlchemy session resolving vocabularytermset members by subset name."""

    def __init__(self, members_by_subset):
        # members_by_subset: {subset_name_upper: [term_name, ...]}
        self._members_by_subset = {
            key.upper(): list(values) for key, values in members_by_subset.items()
        }
        self.executed = []
        self.closed = False

    def execute(self, _query, params):
        subset_name = params.get("subset_name")
        self.executed.append(subset_name)
        members = self._members_by_subset.get(str(subset_name).upper(), [])
        # The member-resolution query selects LOWER(vt.name); mirror that here.
        return SimpleNamespace(
            fetchall=lambda: [(name.lower(),) for name in members]
        )

    def close(self):
        self.closed = True


class _SubsetDb:
    """Fake curation DB exposing search_vocabulary_terms + a subset-member session."""

    def __init__(self, *, terms, members_by_subset):
        self._terms = terms
        self._members_by_subset = members_by_subset
        self.session = None

    def search_vocabulary_terms(self, **kwargs):
        # Honor the term filter so get_vocabulary_term exact lookups return only the
        # requested term, mirroring the real DB search (the subset filter then applies
        # on top of that result set).
        term = kwargs.get("term")
        if term:
            term_lower = str(term).strip().lower()
            return [t for t in self._terms if t.name.lower() == term_lower]
        return list(self._terms)

    def create_session(self):
        self.session = _FakeSubsetSession(self._members_by_subset)
        return self.session


_DISEASE_RELATION_TERMS = [
    _term(internal_id=1, name="is_implicated_in"),
    _term(internal_id=2, name="is_marker_for"),
    _term(internal_id=3, name="is_model_of"),
    _term(internal_id=4, name="is_ameliorated_model_of"),
    _term(internal_id=5, name="is_exacerbated_model_of"),
    _term(internal_id=6, name="is_implicated_via_orthology"),
    _term(internal_id=7, name="is_marker_via_orthology"),
]

_DISEASE_RELATION_SUBSET_MEMBERS = {
    "AGM Disease Relation": [
        "is_model_of",
        "is_ameliorated_model_of",
        "is_exacerbated_model_of",
    ],
    "Gene Disease Relation": ["is_implicated_in", "is_marker_for"],
    "Allele Disease Relation": ["is_implicated_in"],
    "Via Orthology Disease Relation": [
        "is_implicated_via_orthology",
        "is_marker_via_orthology",
    ],
}


def _subset_resolver(monkeypatch):
    db = _SubsetDb(
        terms=_DISEASE_RELATION_TERMS,
        members_by_subset=_DISEASE_RELATION_SUBSET_MEMBERS,
    )
    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(db),
    )
    return db


def test_search_vocabulary_terms_without_subset_returns_full_vocabulary(monkeypatch):
    _subset_resolver(monkeypatch)
    result = _query_fn()(
        method="search_vocabulary_terms",
        vocabulary="Disease Relation",
        limit=100,
    )
    assert result.status == "ok"
    assert result.count == 7
    names = {item["term_name"] for item in result.data}
    assert names == {
        "is_implicated_in",
        "is_marker_for",
        "is_model_of",
        "is_ameliorated_model_of",
        "is_exacerbated_model_of",
        "is_implicated_via_orthology",
        "is_marker_via_orthology",
    }
    # No subset -> no subset-applied warning.
    assert not any(
        str(w).startswith("vocabulary_subset_applied")
        for w in (result.warnings or [])
    )


def test_search_vocabulary_terms_with_agm_subset_restricts_members(monkeypatch):
    _subset_resolver(monkeypatch)
    result = _query_fn()(
        method="search_vocabulary_terms",
        vocabulary="Disease Relation",
        subset="AGM Disease Relation",
        limit=100,
    )
    assert result.status == "ok"
    assert result.count == 3
    names = {item["term_name"] for item in result.data}
    assert names == {
        "is_model_of",
        "is_ameliorated_model_of",
        "is_exacerbated_model_of",
    }
    assert "vocabulary_subset_applied:AGM Disease Relation" in (result.warnings or [])


def test_search_vocabulary_terms_with_gene_union_subset(monkeypatch):
    _subset_resolver(monkeypatch)
    result = _query_fn()(
        method="search_vocabulary_terms",
        vocabulary="Disease Relation",
        subset=["Gene Disease Relation", "Via Orthology Disease Relation"],
        limit=100,
    )
    assert result.status == "ok"
    names = {item["term_name"] for item in result.data}
    assert names == {
        "is_implicated_in",
        "is_marker_for",
        "is_implicated_via_orthology",
        "is_marker_via_orthology",
    }


def test_get_vocabulary_term_wrong_subtype_relation_is_rejected(monkeypatch):
    """is_model_of resolves under the AGM subset but is rejected under the gene subset."""
    _subset_resolver(monkeypatch)

    resolved = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="Disease Relation",
        subset="AGM Disease Relation",
        term_name="is_model_of",
    )
    assert resolved.status == "ok"
    assert resolved.count == 1
    assert resolved.data[0]["term_name"] == "is_model_of"

    rejected = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="Disease Relation",
        subset=["Gene Disease Relation", "Via Orthology Disease Relation"],
        term_name="is_model_of",
    )
    assert rejected.status == "ok"
    assert rejected.count == 0
    assert "Vocabulary term not found" in (rejected.message or "")

    # Without the subset, the umbrella vocabulary still resolves is_model_of.
    umbrella = _query_fn()(
        method="get_vocabulary_term",
        vocabulary="Disease Relation",
        term_name="is_model_of",
    )
    assert umbrella.status == "ok"
    assert umbrella.count == 1


def test_vocabulary_subset_unknown_name_yields_empty(monkeypatch):
    _subset_resolver(monkeypatch)
    result = _query_fn()(
        method="search_vocabulary_terms",
        vocabulary="Disease Relation",
        subset="Nonexistent Subset",
        limit=100,
    )
    assert result.status == "ok"
    assert result.count == 0
    assert "subset_not_found_or_empty:Nonexistent Subset" in (result.warnings or [])

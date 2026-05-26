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
    assert result.candidate_matches[0]["projection"]["object_type"] == "VocabularyTerm"


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
    assert helper_result["helper_result"] == {
        "name": "is_expressed_in",
        "term_name": "is_expressed_in",
        "internal_id": 200000200,
        "abbreviation": None,
        "synonyms": [],
        "obsolete": False,
        "authority": "live_validated_option",
    }
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

"""Package-owned AGR curation controlled vocabulary helper tests."""

from __future__ import annotations

from types import SimpleNamespace

from agr_ai_curation_alliance.tools import agr_curation


def _query_fn():
    return agr_curation._unwrap_function_tool_callable(
        agr_curation.agr_curation_query,
        "agr_curation_query",
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

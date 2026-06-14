"""Integration coverage for allele fuzzy lookup SQL against real Postgres."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[4]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.tools import agr_curation  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.database_integration]

TEST_SCHEMA = "all_605_allele_fuzzy_test"
TEST_THRESHOLD = 0.41
ZFIN_CASP3B = "ZFIN:ZDB-ALT-ALL-605-1"
WB_CASP3B = "WB:WBVarALL6050002"
ZFIN_SHHA = "ZFIN:ZDB-ALT-ALL-605-3"


@dataclass
class _AlleleFuzzyDbShim:
    session_factory: sessionmaker

    def create_session(self):
        session = self.session_factory()
        session.execute(text(f"SET LOCAL search_path TO {TEST_SCHEMA}, public"))
        return session


def _database_url() -> str:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        pytest.skip("DATABASE_URL not set for allele fuzzy database integration test")
    return db_url


def _current_similarity_threshold(engine) -> float:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT current_setting('pg_trgm.similarity_threshold')")
        ).scalar_one()
    return float(value)


def _search(db, *, search_pattern: str, taxon_curie: str | None, include_synonyms: bool):
    return agr_curation._search_alleles_fuzzy_via_db(
        db,
        search_pattern=search_pattern,
        taxon_curie=taxon_curie,
        include_synonyms=include_synonyms,
        limit=10,
    )


@pytest.fixture(scope="module")
def allele_fuzzy_engine():
    engine = create_engine(_database_url(), pool_size=1, max_overflow=0)
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT 1"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
            conn.execute(
                text(
                    f"""
                    CREATE TABLE {TEST_SCHEMA}.ontologyterm (
                        id bigint PRIMARY KEY,
                        curie text NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    CREATE TABLE {TEST_SCHEMA}.biologicalentity (
                        id bigint PRIMARY KEY,
                        primaryexternalid text NOT NULL,
                        taxon_id bigint REFERENCES {TEST_SCHEMA}.ontologyterm(id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    CREATE TABLE {TEST_SCHEMA}.allele (
                        id bigint PRIMARY KEY
                    )
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    CREATE TABLE {TEST_SCHEMA}.slotannotation (
                        id bigint PRIMARY KEY,
                        singleallele_id bigint REFERENCES {TEST_SCHEMA}.allele(id),
                        slotannotationtype text NOT NULL,
                        obsolete boolean NOT NULL DEFAULT false,
                        displaytext text
                    )
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    CREATE INDEX slotannotation_displaytext_trgm_idx
                    ON {TEST_SCHEMA}.slotannotation
                    USING gin (upper(displaytext) gin_trgm_ops)
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {TEST_SCHEMA}.ontologyterm (id, curie)
                    VALUES
                        (1, 'NCBITaxon:7955'),
                        (2, 'NCBITaxon:6239')
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {TEST_SCHEMA}.biologicalentity
                        (id, primaryexternalid, taxon_id)
                    VALUES
                        (101, :zfin_casp3b, 1),
                        (102, :wb_casp3b, 2),
                        (103, :zfin_shha, 1)
                    """
                ),
                {
                    "zfin_casp3b": ZFIN_CASP3B,
                    "wb_casp3b": WB_CASP3B,
                    "zfin_shha": ZFIN_SHHA,
                },
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {TEST_SCHEMA}.allele (id)
                    VALUES (101), (102), (103)
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {TEST_SCHEMA}.slotannotation
                        (id, singleallele_id, slotannotationtype, obsolete, displaytext)
                    VALUES
                        (1001, 101, 'AlleleSymbolSlotAnnotation', false, 'casp3b'),
                        (1002, 102, 'AlleleSymbolSlotAnnotation', false, 'casp3b'),
                        (1003, 103, 'AlleleSymbolSlotAnnotation', false, 'shha'),
                        (1004, 103, 'AlleleSynonymSlotAnnotation', false, 'sonic hedgehog alpha')
                    """
                )
            )
        yield engine
    except (OperationalError, ProgrammingError) as exc:
        pytest.skip(f"Postgres with pg_trgm is not available for allele fuzzy test: {exc}")
    finally:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        except SQLAlchemyError:
            pass
        engine.dispose()


@pytest.fixture
def allele_fuzzy_db(allele_fuzzy_engine):
    return _AlleleFuzzyDbShim(sessionmaker(bind=allele_fuzzy_engine))


def test_allele_fuzzy_db_lookup_uses_trigrams_and_transaction_local_threshold(
    allele_fuzzy_engine,
    allele_fuzzy_db,
    monkeypatch,
):
    monkeypatch.setattr(
        agr_curation,
        "_ALLELE_FUZZY_SIMILARITY_THRESHOLD",
        TEST_THRESHOLD,
    )
    original_threshold = _current_similarity_threshold(allele_fuzzy_engine)
    assert original_threshold != TEST_THRESHOLD

    zfin_results = _search(
        allele_fuzzy_db,
        search_pattern="casp3",
        taxon_curie="NCBITaxon:7955",
        include_synonyms=False,
    )
    assert len(zfin_results) == 1
    assert zfin_results[0]["entity_curie"] == ZFIN_CASP3B
    assert zfin_results[0]["taxon_curie"] == "NCBITaxon:7955"
    assert zfin_results[0]["entity"] == "casp3b"
    assert zfin_results[0]["match_type"] == "fuzzy_symbol"
    assert zfin_results[0]["score"] >= TEST_THRESHOLD

    assert _search(
        allele_fuzzy_db,
        search_pattern="unrelated-query",
        taxon_curie="NCBITaxon:7955",
        include_synonyms=True,
    ) == []

    wb_results = _search(
        allele_fuzzy_db,
        search_pattern="casp3",
        taxon_curie="NCBITaxon:6239",
        include_synonyms=False,
    )
    assert [row["entity_curie"] for row in wb_results] == [WB_CASP3B]

    synonym_results = _search(
        allele_fuzzy_db,
        search_pattern="sonic hedgehog",
        taxon_curie="NCBITaxon:7955",
        include_synonyms=True,
    )
    assert len(synonym_results) == 1
    assert synonym_results[0]["entity_curie"] == ZFIN_SHHA
    assert synonym_results[0]["taxon_curie"] == "NCBITaxon:7955"
    assert synonym_results[0]["entity"] == "sonic hedgehog alpha"
    assert synonym_results[0]["match_type"] == "fuzzy_synonym"
    assert synonym_results[0]["score"] >= TEST_THRESHOLD
    assert _search(
        allele_fuzzy_db,
        search_pattern="sonic hedgehog",
        taxon_curie="NCBITaxon:7955",
        include_synonyms=False,
    ) == []

    assert _current_similarity_threshold(allele_fuzzy_engine) == original_threshold

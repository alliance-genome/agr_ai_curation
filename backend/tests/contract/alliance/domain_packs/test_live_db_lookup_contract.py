"""Opt-in live curation DB contract checks for domain-pack lookup grounding."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


LIVE_DB_ENV = "ALLIANCE_LIVE_DB_CONTRACT_TESTS"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _live_db_url() -> str | URL | None:
    explicit_url = os.getenv("CURATION_DB_URL", "").strip()
    if explicit_url:
        return explicit_url

    host = os.getenv("PERSISTENT_STORE_DB_HOST", "").strip()
    port = os.getenv("PERSISTENT_STORE_DB_PORT", "").strip()
    database = os.getenv("PERSISTENT_STORE_DB_NAME", "").strip()
    username = os.getenv("PERSISTENT_STORE_DB_USERNAME", "").strip()
    password = os.getenv("PERSISTENT_STORE_DB_PASSWORD", "")
    if not all((host, port, database, username, password)):
        return None
    return URL.create(
        drivername="postgresql+psycopg2",
        username=username,
        password=password,
        host=host,
        port=int(port),
        database=database,
    )


@pytest.fixture(scope="module")
def live_db_engine():
    if not _truthy(os.getenv(LIVE_DB_ENV)):
        pytest.skip(f"Set {LIVE_DB_ENV}=1 to run live curation DB contract tests")

    url = _live_db_url()
    if url is None:
        pytest.fail(
            f"{LIVE_DB_ENV}=1 but no live DB URL or PERSISTENT_STORE_DB_* tunnel "
            "environment is available."
        )

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1")).scalar_one()
    except Exception as exc:
        pytest.fail(
            "Live curation DB contract tests were explicitly requested, but the "
            f"read-only DB is unavailable: {type(exc).__name__}: {exc}"
        )
    try:
        yield engine
    finally:
        engine.dispose()


def test_live_db_representative_gene_and_allele_lookup_rows(live_db_engine):
    with live_db_engine.connect() as conn:
        gene_rows = conn.execute(
            text(
                """
                SELECT be.primaryexternalid, symbol.displaytext AS symbol, taxon.curie AS taxon
                FROM public.biologicalentity be
                JOIN public.gene g ON be.id = g.id
                LEFT JOIN public.slotannotation symbol
                  ON g.id = symbol.singlegene_id
                 AND symbol.slotannotationtype = 'GeneSymbolSlotAnnotation'
                 AND symbol.obsolete = false
                LEFT JOIN public.ontologyterm taxon ON be.taxon_id = taxon.id
                WHERE be.primaryexternalid IN (
                    'FB:FBgn0000117',
                    'SGD:S000004578',
                    'WB:WBGene00000298'
                )
                ORDER BY be.primaryexternalid
                """
            )
        ).mappings().all()
        allele_rows = conn.execute(
            text(
                """
                SELECT be.primaryexternalid, symbol.displaytext AS symbol, taxon.curie AS taxon
                FROM public.biologicalentity be
                JOIN public.allele a ON be.id = a.id
                LEFT JOIN public.slotannotation symbol
                  ON a.id = symbol.singleallele_id
                 AND symbol.slotannotationtype = 'AlleleSymbolSlotAnnotation'
                 AND symbol.obsolete = false
                LEFT JOIN public.ontologyterm taxon ON be.taxon_id = taxon.id
                WHERE be.primaryexternalid IN ('MGI:3689328', 'WB:WBVar00000001')
                ORDER BY be.primaryexternalid
                """
            )
        ).mappings().all()

    assert {row["primaryexternalid"] for row in gene_rows} == {
        "FB:FBgn0000117",
        "SGD:S000004578",
        "WB:WBGene00000298",
    }
    assert all(row["symbol"] and row["taxon"] for row in gene_rows)
    assert {row["primaryexternalid"] for row in allele_rows} == {
        "MGI:3689328",
        "WB:WBVar00000001",
    }
    assert all(row["symbol"] and row["taxon"] for row in allele_rows)


def test_live_db_allele_reference_and_evidence_association_targets(live_db_engine):
    with live_db_engine.connect() as conn:
        constraints = conn.execute(
            text(
                """
                SELECT conrelid::regclass::text AS table_name,
                       conname,
                       pg_get_constraintdef(oid) AS definition
                FROM pg_constraint
                WHERE conrelid IN (
                    'public.allele_reference'::regclass,
                    'public.allelegeneassociation'::regclass,
                    'public.allelegeneassociation_informationcontententity'::regclass
                )
                ORDER BY table_name, conname
                """
            )
        ).mappings().all()
        allele_reference_rows = conn.execute(
            text(
                """
                SELECT ar.allele_id,
                       be.primaryexternalid AS allele_primary_external_id,
                       ar.references_id,
                       ice.curie AS reference_curie
                FROM public.allele_reference ar
                JOIN public.allele a ON a.id = ar.allele_id
                JOIN public.genomicentity ge ON ge.id = a.id
                JOIN public.biologicalentity be ON be.id = ge.id
                JOIN public.reference r ON r.id = ar.references_id
                JOIN public.informationcontententity ice ON ice.id = r.id
                LIMIT 5
                """
            )
        ).mappings().all()
        evidence_rows = conn.execute(
            text(
                """
                SELECT agaice.association_id,
                       agaice.evidence_id,
                       ice.curie AS evidence_curie
                FROM public.allelegeneassociation_informationcontententity agaice
                JOIN public.informationcontententity ice ON ice.id = agaice.evidence_id
                LIMIT 5
                """
            )
        ).mappings().all()

    constraint_defs = {row["definition"] for row in constraints}
    assert "FOREIGN KEY (allele_id) REFERENCES allele(id)" in constraint_defs
    assert "FOREIGN KEY (references_id) REFERENCES reference(id)" in constraint_defs
    assert (
        "FOREIGN KEY (association_id) REFERENCES allelegeneassociation(id)"
        in constraint_defs
    )
    assert (
        "FOREIGN KEY (evidence_id) REFERENCES informationcontententity(id)"
        in constraint_defs
    )
    assert allele_reference_rows
    assert evidence_rows
    assert all(row["allele_primary_external_id"] for row in allele_reference_rows)
    assert all(row["reference_curie"] for row in allele_reference_rows)
    assert all(row["evidence_curie"] for row in evidence_rows)


def test_live_db_representative_disease_chemical_and_phenotype_projection_rows(live_db_engine):
    with live_db_engine.connect() as conn:
        ontology_terms = conn.execute(
            text(
                """
                SELECT curie, name, ontologytermtype
                FROM public.ontologyterm
                WHERE curie IN (
                    'CHEBI:16113',
                    'DOID:0050434',
                    'MP:0001569',
                    'WBPhenotype:0000180'
                )
                ORDER BY curie
                """
            )
        ).mappings().all()
        disease_rows = conn.execute(
            text(
                """
                SELECT da.id, ot.curie AS disease_curie, ot.name AS disease_name,
                       vt.name AS relation_name, org.abbreviation AS data_provider
                FROM public.diseaseannotation da
                LEFT JOIN public.ontologyterm ot ON da.diseaseannotationobject_id = ot.id
                LEFT JOIN public.vocabularyterm vt ON da.relation_id = vt.id
                LEFT JOIN public.organization org ON da.dataprovider_id = org.id
                WHERE da.id IN (209127250, 209127267, 209127402)
                ORDER BY da.id
                """
            )
        ).mappings().all()
        chemical_condition = conn.execute(
            text(
                """
                SELECT ec.id, chem.curie AS chemical_curie, chem.name AS chemical_name,
                       class.curie AS class_curie, class.name AS class_name
                FROM public.experimentalcondition ec
                LEFT JOIN public.ontologyterm chem ON ec.conditionchemical_id = chem.id
                LEFT JOIN public.ontologyterm class ON ec.conditionclass_id = class.id
                WHERE ec.id = 200016096
                """
            )
        ).mappings().one()
        condition_relation = conn.execute(
            text(
                """
                SELECT cr.id, vt.name AS condition_relation_type, count(ce.conditions_id) AS condition_count
                FROM public.conditionrelation cr
                LEFT JOIN public.vocabularyterm vt ON cr.conditionrelationtype_id = vt.id
                LEFT JOIN public.conditionrelation_experimentalcondition ce
                  ON cr.id = ce.conditionrelation_id
                WHERE cr.id = 200019015
                GROUP BY cr.id, vt.name
                """
            )
        ).mappings().one()
        phenotype_projection = conn.execute(
            text(
                """
                SELECT pa.id AS phenotypeannotation_id, pot.phenotypeterms_id,
                       ot.curie, ot.name, ot.ontologytermtype
                FROM public.phenotypeannotation pa
                JOIN public.phenotypeannotation_ontologyterm pot
                  ON pa.id = pot.phenotypeannotation_id
                JOIN public.ontologyterm ot ON pot.phenotypeterms_id = ot.id
                WHERE pa.id = 210270365
                """
            )
        ).mappings().one()

    terms_by_curie = {row["curie"]: row for row in ontology_terms}
    assert terms_by_curie["DOID:0050434"]["ontologytermtype"] == "DOTerm"
    assert terms_by_curie["CHEBI:16113"]["ontologytermtype"] == "CHEBITerm"
    assert terms_by_curie["MP:0001569"]["ontologytermtype"] == "MPTerm"
    assert terms_by_curie["WBPhenotype:0000180"]["ontologytermtype"] == "WBPhenotypeTerm"

    assert {row["id"] for row in disease_rows} == {209127250, 209127267, 209127402}
    assert all(row["disease_curie"] and row["relation_name"] for row in disease_rows)
    assert chemical_condition["chemical_curie"] == "CHEBI:9168"
    assert chemical_condition["class_curie"] == "ZECO:0000111"
    assert condition_relation["condition_relation_type"] == "has_condition"
    assert condition_relation["condition_count"] >= 1
    assert phenotype_projection["curie"] == "MP:0003733"
    assert phenotype_projection["ontologytermtype"] == "MPTerm"

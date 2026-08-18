"""Alliance identifier-prefix discovery against the curation schema."""

from __future__ import annotations

import psycopg2


_PREFIX_QUERIES = (
    "SELECT DISTINCT split_part(referencedcurie, ':', 1) AS prefix "
    "FROM crossreference WHERE referencedcurie LIKE '%:%' AND referencedcurie IS NOT NULL;",
    "SELECT DISTINCT split_part(curie, ':', 1) AS prefix "
    "FROM ontologyterm WHERE curie LIKE '%:%' AND curie IS NOT NULL;",
    "SELECT DISTINCT split_part(primaryexternalid, ':', 1) AS prefix "
    "FROM biologicalentity WHERE primaryexternalid LIKE '%:%' AND primaryexternalid IS NOT NULL;",
)


def get_identifier_prefixes(database_url: str) -> list[str]:
    """Return the distinct identifier prefixes present in Alliance curation data."""

    prefixes: list[str] = []
    with psycopg2.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for query in _PREFIX_QUERIES:
                cursor.execute(query)
                prefixes.extend(row[0] for row in cursor.fetchall())
    return prefixes

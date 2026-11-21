"""Read-only check that each paper's stored piece numbers run unbroken from 0.

The paper-files fill step records each existing main file's piece range as
0..count-1. It refuses a paper whose stored piece numbers have a gap, a
duplicate or a start other than 0. This module holds that check, and can also
run on its own, before the release, against a database that has not been
migrated yet. It reads only columns that exist before and after the
migration (``pdf_documents.id``, ``user_id`` and ``status``; ``users.auth_sub``)
and the search index's ``documentId`` and ``chunkIndex`` properties.

It writes nothing. The database work runs in one READ ONLY transaction, and
the search index is only listed. It prints counts and, for papers whose piece
numbers are not unbroken, their internal ``pdf_documents.id`` values. It never
prints titles, file names, paper identifiers or text.

Run it in the backend container of the release that is live, with the file on
standard input (it imports only modules that every release has):

    docker exec -i <backend container> python - --json \\
        < backend/src/lib/document_files/piece_numbers.py

Exit status: 0 when every paper is unbroken, 1 when any is not, 2 on error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


CHUNK_COLLECTION = "DocumentChunk"


class PieceNumberCheckError(RuntimeError):
    """The check could not read what it needs; nothing was changed."""


@dataclass(frozen=True, slots=True)
class PieceNumbers:
    """The stored piece numbers of one paper."""

    count: int
    distinct: int
    lowest: int | None
    highest: int | None

    @classmethod
    def of(cls, numbers: Iterable[int]) -> "PieceNumbers":
        values = list(numbers)
        return cls(
            count=len(values),
            distinct=len(set(values)),
            lowest=min(values) if values else None,
            highest=max(values) if values else None,
        )

    @property
    def unbroken(self) -> bool:
        """True when the numbers are exactly 0..count-1, each once (or there are none)."""

        if self.count == 0:
            return True
        return self.distinct == self.count and self.lowest == 0 and self.highest == self.count - 1


@dataclass(frozen=True, slots=True)
class PaperOwner:
    document_id: str
    owner_auth_sub: str
    status: str


def tenant_name(owner_auth_sub: str) -> str:
    from src.lib.weaviate_helpers import get_tenant_name

    return get_tenant_name(owner_auth_sub)


def read_paper_owners(connection: Any) -> list[PaperOwner]:
    """List every paper with its owner, inside the caller's read-only transaction."""

    from sqlalchemy import text

    rows = connection.execute(text(
        "SELECT d.id, u.auth_sub, d.status FROM pdf_documents d "
        "JOIN users u ON u.user_id = d.user_id ORDER BY d.id"
    )).all()
    return [PaperOwner(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def scan_tenant_piece_numbers(client: Any, tenant: str) -> dict[str, list[int]]:
    """Return every stored piece number of one tenant, grouped by ``documentId``."""

    collection = client.collections.get(CHUNK_COLLECTION).with_tenant(tenant)
    numbers: dict[str, list[int]] = defaultdict(list)
    for item in collection.iterator(return_properties=["documentId", "chunkIndex"]):
        properties = item.properties
        document_id = properties.get("documentId")
        chunk_index = properties.get("chunkIndex")
        if not isinstance(document_id, str) or type(chunk_index) is not int:
            raise PieceNumberCheckError(
                f"A stored piece in tenant {tenant} has no documentId or no integer chunkIndex"
            )
        numbers[document_id].append(chunk_index)
    return dict(numbers)


def existing_tenants(client: Any) -> set[str]:
    return set(client.collections.get(CHUNK_COLLECTION).tenants.get())


def scan_papers(client: Any, papers: Iterable[PaperOwner]) -> tuple[dict[str, PieceNumbers], dict[str, Any]]:
    """Return each paper's piece numbers, and counts about what was scanned."""

    papers = list(papers)
    by_tenant: dict[str, list[PaperOwner]] = defaultdict(list)
    for paper in papers:
        by_tenant[tenant_name(paper.owner_auth_sub)].append(paper)
    tenants = existing_tenants(client)
    numbers: dict[str, PieceNumbers] = {}
    orphan_groups = 0
    missing_tenants = 0
    for tenant, owned in sorted(by_tenant.items()):
        if tenant not in tenants:
            missing_tenants += 1
            for paper in owned:
                numbers[paper.document_id] = PieceNumbers.of(())
            continue
        stored = scan_tenant_piece_numbers(client, tenant)
        owned_ids = {paper.document_id for paper in owned}
        orphan_groups += sum(1 for document_id in stored if document_id not in owned_ids)
        for paper in owned:
            numbers[paper.document_id] = PieceNumbers.of(stored.get(paper.document_id, ()))
    return numbers, {
        "tenants_scanned": len(by_tenant) - missing_tenants,
        "owner_tenants_without_index": missing_tenants,
        "piece_groups_without_paper_row": orphan_groups,
    }


def summarize(papers: list[PaperOwner], numbers: Mapping[str, PieceNumbers], scan_counts: Mapping[str, Any]) -> dict[str, Any]:
    """Counts and the internal ids of papers that are not unbroken. No text, no names."""

    broken = sorted(document_id for document_id, value in numbers.items() if not value.unbroken)
    by_status: dict[str, dict[str, int]] = defaultdict(lambda: {"papers": 0, "with_pieces": 0, "pieces": 0})
    for paper in papers:
        value = numbers[paper.document_id]
        bucket = by_status[paper.status]
        bucket["papers"] += 1
        bucket["with_pieces"] += 1 if value.count else 0
        bucket["pieces"] += value.count
    largest = max((value.count for value in numbers.values()), default=0)
    return {
        "papers": len(papers),
        "papers_with_pieces": sum(1 for value in numbers.values() if value.count),
        "pieces": sum(value.count for value in numbers.values()),
        "largest_paper_pieces": largest,
        "by_status": dict(sorted(by_status.items())),
        **scan_counts,
        "papers_not_unbroken": len(broken),
        "papers_not_unbroken_ids": broken,
        "not_unbroken_detail": {
            document_id: {
                "count": numbers[document_id].count,
                "distinct": numbers[document_id].distinct,
                "lowest": numbers[document_id].lowest,
                "highest": numbers[document_id].highest,
            }
            for document_id in broken
        },
    }


def run_read_only_check() -> dict[str, Any]:
    """Read the database (READ ONLY transaction) and the search index; change nothing."""

    from sqlalchemy import create_engine, text

    from src.config import get_app_database_url
    from src.lib.weaviate_client.connection import get_connection

    engine = create_engine(get_app_database_url())
    try:
        with engine.connect() as connection:
            connection = connection.execution_options(postgresql_readonly=True)
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                if connection.scalar(text("SHOW transaction_read_only")) != "on":
                    raise PieceNumberCheckError("The database transaction is not read-only")
                papers = read_paper_owners(connection)
    finally:
        engine.dispose()
    with get_connection().session() as client:
        numbers, scan_counts = scan_papers(client, papers)
    return summarize(papers, numbers, scan_counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = parser.parse_args(argv)
    try:
        summary = run_read_only_check()
    except Exception as exc:  # report the kind only; messages can carry connection details
        print(f"piece-number check failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")
    return 0 if summary["papers_not_unbroken"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""The read-only piece-number check: classification, summary and import surface."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.lib.document_files import piece_numbers
from src.lib.document_files.piece_numbers import PaperOwner, PieceNumbers


@pytest.mark.parametrize(
    ("numbers", "unbroken"),
    [
        ([], True),
        ([0], True),
        ([2, 0, 1], True),
        ([1, 2], False),
        ([0, 2], False),
        ([0, 1, 1], False),
        ([0, 0, 3, 3], False),
    ],
)
def test_only_zero_to_count_minus_one_each_once_is_unbroken(numbers, unbroken):
    assert PieceNumbers.of(numbers).unbroken is unbroken


class _Collection:
    def __init__(self, stored):
        self.stored = stored
        self.tenants = SimpleNamespace(get=lambda: {tenant: object() for tenant in stored})
        self.tenant = None

    def with_tenant(self, tenant):
        scoped = _Collection(self.stored)
        scoped.tenant = tenant
        return scoped

    def iterator(self, *, return_properties):
        assert return_properties == ["documentId", "chunkIndex"]
        for document_id, number in self.stored[self.tenant]:
            yield SimpleNamespace(properties={"documentId": document_id, "chunkIndex": number})


def _client(stored):
    collection = _Collection(stored)
    return SimpleNamespace(collections=SimpleNamespace(get=lambda name: collection))


def test_scan_groups_by_owner_tenant_and_counts_orphan_groups(monkeypatch):
    monkeypatch.setattr(piece_numbers, "tenant_name", lambda sub: f"t_{sub}")
    client = _client({
        "t_a": [("p1", 0), ("p1", 1), ("p2", 1), ("orphan", 0)],
    })
    papers = [PaperOwner("p1", "a", "completed"), PaperOwner("p2", "a", "failed"),
              PaperOwner("p3", "b", "pending")]

    numbers, counts = piece_numbers.scan_papers(client, papers)
    summary = piece_numbers.summarize(papers, numbers, counts)

    assert numbers["p1"].unbroken and not numbers["p2"].unbroken
    assert numbers["p3"].count == 0
    assert counts == {
        "tenants_scanned": 1,
        "owner_tenants_without_index": 1,
        "piece_groups_without_paper_row": 1,
    }
    assert summary["papers_not_unbroken_ids"] == ["p2"]
    assert summary["by_status"]["pending"] == {"papers": 1, "with_pieces": 0, "pieces": 0}
    assert summary["largest_paper_pieces"] == 2


def test_a_piece_without_an_integer_number_stops_the_check():
    client = _client({"t": [("p1", "0")]})
    with pytest.raises(piece_numbers.PieceNumberCheckError):
        piece_numbers.scan_tenant_piece_numbers(client, "t")


def test_the_check_imports_only_modules_every_release_has():
    """It runs, piped on standard input, inside the release that is live before 0.12.0."""

    tree = ast.parse(Path(piece_numbers.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src")
    }
    assert imported == {"src.config", "src.lib.weaviate_client.connection", "src.lib.weaviate_helpers"}

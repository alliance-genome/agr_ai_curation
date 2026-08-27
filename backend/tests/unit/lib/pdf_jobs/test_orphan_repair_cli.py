"""Tests for the manual pending PDF no-job orphan repair entry point."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.lib.pdf_jobs import orphan_repair_cli


def test_cli_defaults_to_environment_mode_and_emits_content_free_json(
    monkeypatch,
    capsys,
):
    observed = {}
    summary = SimpleNamespace(
        dry_run=True,
        batch_size=100,
        qualifying_count=1,
        records=(),
        to_json=lambda: {
            "dry_run": True,
            "cutoff": datetime(2026, 8, 26, tzinfo=timezone.utc).isoformat(),
            "batch_size": 100,
            "qualifying_count": 1,
            "records": [
                {
                    "document_id": "11111111-1111-1111-1111-111111111111",
                    "status": "would_fail",
                    "reason": "retry processing",
                    "job_id": None,
                }
            ],
        },
    )

    def fake_reconcile(*, apply):
        observed["apply"] = apply
        return summary

    monkeypatch.setattr(
        orphan_repair_cli,
        "reconcile_pending_documents_without_jobs",
        fake_reconcile,
    )

    assert orphan_repair_cli.main(["--json"]) == 0

    output = capsys.readouterr().out
    assert observed == {"apply": None}
    assert '"document_id"' in output
    assert "filename" not in output
    assert "content" not in output


def test_cli_apply_and_dry_run_flags_override_environment_mode(monkeypatch):
    observed = []
    summary = SimpleNamespace(
        dry_run=True,
        batch_size=100,
        qualifying_count=0,
        records=(),
        to_json=lambda: {
            "dry_run": True,
            "cutoff": "2026-08-26T00:00:00+00:00",
            "batch_size": 100,
            "qualifying_count": 0,
            "records": [],
        },
    )
    monkeypatch.setattr(
        orphan_repair_cli,
        "reconcile_pending_documents_without_jobs",
        lambda *, apply: observed.append(apply) or summary,
    )

    assert orphan_repair_cli.main(["--apply", "--json"]) == 0
    assert orphan_repair_cli.main(["--dry-run", "--json"]) == 0
    assert observed == [True, False]

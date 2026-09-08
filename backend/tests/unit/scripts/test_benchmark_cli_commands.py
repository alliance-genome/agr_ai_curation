import pytest

from src.lib.benchmark_cli import commands
from src.lib.benchmark_cli.client import ClientError

JOB = "00000000-0000-4000-8000-000000000001"
CELL = "00000000-0000-4000-8000-000000000002"


class RecordingClient:
    def request(self, method, path, **kwargs):
        return {"method": method, "path": path, **kwargs}


def execute(argv):
    return commands.execute(commands.build_parser().parse_args(argv), RecordingClient())


@pytest.mark.parametrize("argv,method,path", [
    (["catalog", "models"], "GET", "/catalog"),
    (["suites"], "GET", "/suites"),
    (["suite", "example.v2"], "GET", "/suites/example.v2"),
    (["jobs"], "GET", "/jobs"),
    (["cells", JOB], "GET", f"/jobs/{JOB}/cells"),
    (["get", JOB], "GET", f"/jobs/{JOB}"),
    (["get", JOB, "--cell-id", CELL], "GET", f"/jobs/{JOB}/cells/{CELL}"),
    (["cancel", JOB], "POST", f"/jobs/{JOB}/cancel"),
    (["delete", JOB, "--confirm", JOB], "DELETE", f"/jobs/{JOB}"),
])
def test_command_endpoint_mapping(argv, method, path):
    result = execute(argv)
    assert result["method"] == method
    assert result["path"] == commands.PREFIX + path


def test_rerun_has_human_but_never_source_and_preserves_key():
    result = execute(["rerun", JOB, "--cell-id", CELL, "--idempotency-key", "stable"])
    assert result["body"] == {"cell_ids": [CELL]}
    assert result["human"] is True
    assert result["source"] is False
    assert result["idempotency_key"] == "stable"


def test_empty_rerun_means_all_failed_cells():
    assert execute(["rerun", JOB, "--idempotency-key", "stable"])["body"] == {"cell_ids": []}


def test_paginated_catalog_keeps_digest_and_section():
    result = execute(["catalog", "route_slots", "--cursor", "slot", "--catalog-digest", "digest"])
    assert result["params"] == {"section": "route_slots", "cursor": "slot", "catalog_digest": "digest"}


def test_job_and_cell_cursors_keep_endpoint_fields():
    assert execute(["jobs", "--cursor-created-at", "time", "--cursor-job-id", JOB])["params"] == {"cursor_created_at": "time", "cursor_job_id": JOB}
    assert execute(["cells", JOB, "--cursor-position", "0", "--cursor-cell-id", CELL])["params"] == {"cursor_position": "0", "cursor_cell_id": CELL}


def test_delete_requires_matching_confirmation():
    with pytest.raises(ClientError, match="confirmation"):
        execute(["delete", JOB, "--confirm", CELL])


def test_argument_errors_never_echo_values(capsys):
    assert commands.main(["--unknown", "sensitive-value"]) == 2
    output = capsys.readouterr()
    assert "sensitive-value" not in output.err + output.out


def test_submit_passes_exact_object_without_planning(tmp_path):
    path = tmp_path / "request.json"
    path.write_text('{"suite":{"cases":[]},"plan":{"digest":"given"}}')
    result = execute(["submit", "--request", str(path), "--idempotency-key", "same", "--delegate-source"])
    assert result["body"] == {"suite": {"cases": []}, "plan": {"digest": "given"}}
    assert result["source"] is True


def test_submit_requires_explicit_key():
    with pytest.raises(ClientError):
        execute(["submit", "--request", "request.json"])

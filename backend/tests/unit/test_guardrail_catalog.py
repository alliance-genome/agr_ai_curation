from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "docs/testing/guardrail-catalog.md"
PATH_COLUMN = "Test module / guard file"


def _markdown_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= {"-", ":"} for cell in cells)


def _strip_code_span(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        return value[1:-1].strip()
    return value


def _catalog_rows() -> list[dict[str, str]]:
    lines = CATALOG_PATH.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, str]] = []

    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue

        headers = _markdown_table_cells(line)
        if PATH_COLUMN not in headers:
            continue

        if index + 1 >= len(lines):
            raise AssertionError("guardrail catalog table is missing separator row")

        separator = _markdown_table_cells(lines[index + 1])
        if not _is_separator_row(separator):
            raise AssertionError("guardrail catalog table has an invalid separator row")

        for row_line in lines[index + 2 :]:
            if not row_line.startswith("|"):
                break

            cells = _markdown_table_cells(row_line)
            if len(cells) != len(headers):
                raise AssertionError(
                    f"guardrail catalog row has {len(cells)} cells; expected {len(headers)}: {row_line}"
                )
            rows.append(dict(zip(headers, cells, strict=True)))
        break

    return rows


def test_guardrail_catalog_referenced_paths_exist() -> None:
    assert CATALOG_PATH.exists(), "guardrail catalog document is missing"

    rows = _catalog_rows()
    assert rows, "guardrail catalog table has no guard rows"

    missing_paths: list[str] = []
    invalid_paths: list[str] = []

    for row in rows:
        guard_id = row.get("Guard ID", "").strip()
        guard_name = row.get("Guard name", "").strip()
        raw_path = row.get(PATH_COLUMN, "").strip()

        assert guard_id, f"catalog row is missing a Guard ID: {row}"
        assert guard_name, f"catalog row is missing a Guard name: {row}"
        assert raw_path, f"catalog row is missing a guard path: {row}"

        repo_relative_path = Path(_strip_code_span(raw_path))
        if repo_relative_path.is_absolute() or ".." in repo_relative_path.parts:
            invalid_paths.append(f"{guard_id} {guard_name}: {raw_path}")
            continue

        if not (REPO_ROOT / repo_relative_path).exists():
            missing_paths.append(f"{guard_id} {guard_name}: {repo_relative_path}")

    assert not invalid_paths, "guardrail catalog paths must be repo-relative:\n" + "\n".join(
        invalid_paths
    )
    assert not missing_paths, "guardrail catalog paths are missing:\n" + "\n".join(
        missing_paths
    )

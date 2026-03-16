"""Unit tests for runtime package contract helpers."""

from pathlib import Path

CORE_TOOLS_PACKAGE_EXPORTS = (
    "agr_curation_query",
    "alliance_api_call",
    "chebi_api_call",
    "create_curation_db_sql_tool",
    "create_read_section_tool",
    "create_read_subsection_tool",
    "create_search_document_tool",
    "go_api_call",
    "quickgo_api_call",
    "save_csv_file",
    "save_json_file",
    "save_tsv_file",
)


def find_repo_root(start: Path) -> Path:
    """Resolve the repository root by walking upward to a known sentinel."""
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "docker-compose.test.yml").exists():
            return candidate
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate

    raise RuntimeError(f"Could not resolve repository root from {start}")
